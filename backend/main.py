"""
Backend PoC buscador Roca (FastAPI).
Carga products.json + relations.json en memoria y expone la API.
v1: busqueda por texto simple + relaciones. Pensado para crecer
(mas adelante: interpretacion con Claude, embeddings, filtros avanzados).
"""
import json, os, re, unicodedata
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
    PRODUCTS = json.load(f)
with open(os.path.join(DATA, "relations.json"), encoding="utf-8") as f:
    RELATIONS = json.load(f)   # { model: [ {type, code, description, collection, category} ] }
try:
    with open(os.path.join(DATA, "images.json"), encoding="utf-8") as f:
        IMAGES = json.load(f)  # { model: cloudinary_url }
except FileNotFoundError:
    IMAGES = {}

# --- Autocompletado: conceptos + embeddings (ver build_concepts.py) ---
with open(os.path.join(DATA, "concepts.json"), encoding="utf-8") as f:
    CONCEPTS = json.load(f)
CONCEPT_VECTORS = np.load(os.path.join(DATA, "concept_vectors.npy"))  # (N, dim) normalizados
INTENT = CONCEPTS["intent"]                     # [{term, type, count}] alineado con vectores
COLOR_LEXICON = CONCEPTS["color_lexicon"]        # palabra normalizada -> [valores de acabado]
QUALIFIERS = CONCEPTS["qualifiers"]              # palabra/frase -> 'high'|'low'
PRICE_BANDS = CONCEPTS["price_bands"]            # categoria -> {p25, p75}  (+ '__global__')
TEXTURES = ["mate", "brillo", "satinado", "pulido", "texturizado"]

# modelo de embeddings estatico (numpy puro, sin torch); se carga perezosamente
_MODEL = None
def _model():
    global _MODEL
    if _MODEL is None:
        from model2vec import StaticModel
        _MODEL = StaticModel.from_pretrained(CONCEPTS["model"])
    return _MODEL


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()

# indice normalizado de terminos de intencion para prefijo
INTENT_NORM = [_norm(c["term"]) for c in INTENT]


def dims_str(p):
    d = p.get("dims") or {}
    parts = [d.get("length_mm"), d.get("width_mm"), d.get("height_mm")]
    parts = [str(x) for x in parts if x]
    return " x ".join(parts) if parts else None

# --- Indices en memoria ---
BY_SKU = {p["sku"]: p for p in PRODUCTS}
BY_MODEL = defaultdict(list)          # model -> [productos]
for p in PRODUCTS:
    if p.get("model"):
        BY_MODEL[p["model"]].append(p)

def search_blob(p):
    parts = [p.get("title"), p.get("category"), p.get("subcategory"),
             p.get("collection"), p.get("finish"), p.get("sku"),
             (p.get("desc") or {}).get("marketing"),
             (p.get("desc") or {}).get("extended")]
    return " ".join(x for x in parts if x).lower()

SEARCH_INDEX = {p["sku"]: search_blob(p) for p in PRODUCTS}

def summary(p):
    return {
        "sku": p["sku"], "title": p.get("title"), "category": p.get("category"),
        "collection": p.get("collection"), "finish": p.get("finish"),
        "price_rrp": p.get("price_rrp"), "is_spare_part": p.get("is_spare_part"),
        "image": IMAGES.get(p.get("model")), "dims": dims_str(p),
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    _model()   # precarga el modelo de embeddings: evita latencia en la 1a sugerencia
    yield


app = FastAPI(title="Roca Buscador PoC", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "products": len(PRODUCTS),
            "relations_models": len(RELATIONS)}

# ---------------------------------------------------------------- query understanding
def _resolve_price_band(band, category):
    b = PRICE_BANDS.get(category) or PRICE_BANDS["__global__"]
    return {"min_price": b["p75"]} if band == "high" else {"max_price": b["p25"]}


def parse_query(q: str):
    """Divide la consulta en intencion (categoria/coleccion) + atributos (filtros)."""
    raw = q or ""
    ends_space = len(raw) > 0 and raw[-1].isspace()
    tokens = raw.strip().split()
    ntok = [_norm(t) for t in tokens]
    nfull = _norm(raw)
    consumed, filters = set(), []

    # calificador de precio (soporta bigramas: 'gama alta')
    price_band = price_label = None
    for phrase, bnd in QUALIFIERS.items():
        pw = phrase.split()
        if len(pw) > 1:
            for i in range(len(ntok) - len(pw) + 1):
                if ntok[i:i + len(pw)] == pw:
                    price_band, price_label = bnd, phrase
                    consumed.update(range(i, i + len(pw)))
        else:
            for i, t in enumerate(ntok):
                if t == phrase:
                    price_band, price_label = bnd, phrase
                    consumed.add(i)

    # color / textura -> filtro de acabado
    finish_values, color_label, texture = [], None, None
    for i, t in enumerate(ntok):
        if t in COLOR_LEXICON:
            finish_values, color_label = COLOR_LEXICON[t], tokens[i]
            consumed.add(i)
        if t in TEXTURES:
            texture = t
            consumed.add(i)
    if finish_values and texture:
        ft = [f for f in finish_values if texture in _norm(f)]
        finish_values = ft or finish_values
        color_label = f"{color_label} {texture}"
    elif texture and not finish_values:
        finish_values = [f for f in CONCEPTS["finishes"] if texture in _norm(f)]
        color_label = texture
    if finish_values:
        filters.append({"type": "finish", "label": color_label, "values": finish_values})

    # frase de intencion = tokens no consumidos como atributo
    intent_tokens = [tokens[i] for i in range(len(tokens)) if i not in consumed]
    intent_phrase = " ".join(intent_tokens)

    suggestions, seen = [], set()
    # prefijo: solo mientras el usuario escribe la ultima palabra (sin espacio final)
    last_incomplete = (not ends_space and intent_tokens
                       and (len(tokens) - 1) not in consumed)
    if last_incomplete:
        frag = _norm(intent_tokens[-1])
        if len(frag) >= 2:
            for i, nt in enumerate(INTENT_NORM):
                if nt.startswith(frag) and nt not in seen:
                    c = INTENT[i]
                    seen.add(nt)
                    suggestions.append({**c, "source": "prefix"})
            suggestions.sort(key=lambda x: -x["count"])
            suggestions = suggestions[:6]
    # semantico: cuando hay palabra completa o frase multi-token
    if intent_phrase and (ends_space or len(intent_tokens) >= 2 or not last_incomplete):
        qv = _model().encode([intent_phrase])[0].astype("float32")
        qv /= (np.linalg.norm(qv) + 1e-9)
        scores = CONCEPT_VECTORS @ qv
        for idx in np.argsort(-scores)[:8]:
            s = float(scores[idx])
            if s < 0.35:
                break
            c = INTENT[int(idx)]
            nt = INTENT_NORM[int(idx)]
            if nt in seen:
                continue
            seen.add(nt)
            suggestions.append({**c, "score": round(s, 3), "source": "semantic"})

    # resuelve la banda de precio a min/max con la mejor categoria detectada
    if price_band:
        cat = next((s["term"] for s in suggestions if s["type"] == "category"), None)
        filters.append({"type": "price", "label": price_label, "band": price_band,
                        **_resolve_price_band(price_band, cat)})

    return {"query": raw, "intent_phrase": intent_phrase,
            "suggestions": suggestions[:8], "filters": filters}


@app.get("/suggest")
def suggest(q: str = ""):
    if not q.strip():
        return {"query": q, "intent_phrase": "", "suggestions": [], "filters": []}
    return parse_query(q)


def _field_has(p, field, value):
    return any(value == part.strip() for part in (p.get(field) or "").split("|"))


@app.get("/search")
def search(q: str = "", limit: int = 30, include_spare: bool = False,
           category: Optional[str] = None, subcategory: Optional[str] = None,
           collection: Optional[str] = None,
           finish: Optional[list[str]] = Query(None),
           min_price: Optional[float] = None, max_price: Optional[float] = None):
    tokens = [t for t in re.split(r"\s+", q.lower().strip()) if t]
    results = []
    for p in PRODUCTS:
        if not include_spare and p.get("is_spare_part"):
            continue
        if category and not _field_has(p, "category_base", category):
            continue
        if subcategory and not _field_has(p, "subcategory", subcategory):
            continue
        if collection and not _field_has(p, "collection", collection):
            continue
        if finish and not any(_field_has(p, "finish", fv) for fv in finish):
            continue
        rrp = p.get("price_rrp")
        if min_price is not None and (rrp is None or rrp < min_price):
            continue
        if max_price is not None and (rrp is None or rrp > max_price):
            continue
        blob = SEARCH_INDEX[p["sku"]]
        if tokens:
            score = sum(1 for t in tokens if t in blob)
            if score == 0:
                continue
            # bonus si aparece en el titulo
            title = (p.get("title") or "").lower()
            score += sum(1 for t in tokens if t in title)
        else:
            score = 0
        results.append((score, p))
    results.sort(key=lambda x: -x[0])
    return {"query": q, "total": len(results),
            "results": [summary(p) for _, p in results[:limit]]}

def resolve(code):
    """ code (modelo, con posibles '..') -> productos reales del catalogo """
    return [summary(p) for p in BY_MODEL.get(code, [])]

@app.get("/products/{sku}")
def product_detail(sku: str):
    p = BY_SKU.get(sku)
    if not p:
        raise HTTPException(404, f"SKU {sku} no encontrado")
    rels = RELATIONS.get(p.get("model"), [])
    grouped = {"compatible": [], "optional": [], "included": [], "sparepart": []}
    for r in rels:
        targets = resolve(r["code"])
        if not targets:
            continue
        grouped.setdefault(r["type"], []).extend(targets)
    return {
        **summary(p),
        "subcategory": p.get("subcategory"),
        "desc": p.get("desc") or {"marketing": None, "extended": None},
        "relations": grouped,
    }

@app.get("/recommend/{sku}")
def recommend(sku: str, intent: str = "complete_solution"):
    detail = product_detail(sku)
    rels = detail["relations"]
    if intent == "alternatives":
        recs = rels.get("compatible", [])
    elif intent == "components":
        recs = rels.get("sparepart", [])
    else:  # complete_solution
        recs = rels.get("compatible", []) + rels.get("optional", [])
    return {"seed_sku": sku, "intent": intent, "recommendations": recs}
