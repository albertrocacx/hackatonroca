"""
Backend PoC buscador Roca (FastAPI).
Carga products.json + relations.json en memoria y expone la API.
v1: busqueda por texto simple + relaciones. Pensado para crecer
(mas adelante: interpretacion con Claude, embeddings, filtros avanzados).
"""
import json, os, re
from collections import defaultdict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
    PRODUCTS = json.load(f)
with open(os.path.join(DATA, "relations.json"), encoding="utf-8") as f:
    RELATIONS = json.load(f)   # { model: [ {type, code, description, collection, category} ] }

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
    }

app = FastAPI(title="Roca Buscador PoC", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "products": len(PRODUCTS),
            "relations_models": len(RELATIONS)}

@app.get("/search")
def search(q: str = "", limit: int = 30, include_spare: bool = False):
    tokens = [t for t in re.split(r"\s+", q.lower().strip()) if t]
    results = []
    for p in PRODUCTS:
        if not include_spare and p.get("is_spare_part"):
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
    return {**p, "relations": grouped}

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
