"""
Backend PoC buscador Roca (FastAPI).
Carga products.json + relations.json en memoria y expone la API.
v1: busqueda por texto simple + relaciones. Pensado para crecer
(mas adelante: interpretacion con Claude, embeddings, filtros avanzados).
"""
import json, os, re, sys, unicodedata
from collections import defaultdict, Counter
from contextlib import asynccontextmanager
from typing import Optional
import numpy as np

# En Windows, si stdout es una tubería (servicio, preview, CI) su encoding por defecto es
# cp1252 y los prints con caracteres fuera de ese mapa (p.ej. '──' en los logs de azure)
# lanzan UnicodeEncodeError DENTRO de los handlers -> 500. Forzamos UTF-8 tolerante.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# chat IA opcional: si el SDK no está instalado, la app sigue funcionando (solo búsqueda).
# Guardamos la causa REAL del fallo (no siempre es "falta anthropic": puede ser una versión
# sin AsyncAnthropicFoundry, un error de sintaxis, etc.) para reportarla con precisión.
try:
    import chat
    _chat_import_error = None
except Exception as e:  # noqa: BLE001
    chat = None
    _chat_import_error = e

# búsqueda semántica (Azure AI Search). Si el módulo/SDK no carga, /search cae al
# buscador de texto por substring (fallback) para que la app nunca se rompa.
try:
    import azure_search
except Exception:  # noqa: BLE001
    azure_search = None

# búsqueda por imagen (endpoint DINOv2 en Azure ML). Opcional: sin el módulo o sin
# API key, la app funciona igual y /health expone image_ready=false.
try:
    import image_search
except Exception:  # noqa: BLE001
    image_search = None

# "Diseña tu baño" (render IA con gpt-image). Opcional como el chat.
try:
    import design
except Exception:  # noqa: BLE001
    design = None

# nº de vecinos a pedir a Azure. Alto para que las facetas tengan suficiente material
# (las facetas se calculan sobre el conjunto devuelto). Configurable por entorno.
AZURE_K = int(os.getenv("AZURE_SEARCH_K", "120"))

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
    PRODUCTS = json.load(f)
with open(os.path.join(DATA, "relations.json"), encoding="utf-8") as f:
    RELATIONS = json.load(f)   # { model: [ {type, code, description, collection, category} ] }
try:
    with open(os.path.join(DATA, "images.json"), encoding="utf-8") as f:
        IMAGES = json.load(f)  # { sku: cloudinary_url } — solo fotos de producto (ver tools/build_images.py)
except FileNotFoundError:
    IMAGES = {}

# orden de escaparate de roca.es por modelo (menor = antes), extraído del índice
# semántico donde viaja dentro del texto de cada chunk (ver build_websort.py).
# Sin el fichero, sort=websort degrada al orden del catálogo (no rompe nada).
try:
    with open(os.path.join(DATA, "websort.json"), encoding="utf-8") as f:
        WEBSORT = json.load(f)   # { model -> posición (int) }
except FileNotFoundError:
    WEBSORT = {}

# red de distribuidores/puntos de venta para la compra OFFLINE (ver build_suppliers.py).
# datos reales del export oficial de POS Roca en España (861 puntos geolocalizados).
try:
    with open(os.path.join(DATA, "suppliers.json"), encoding="utf-8") as f:
        SUPPLIERS = json.load(f)
except FileNotFoundError:
    SUPPLIERS = []

# --- Autocompletado: conceptos + embeddings (ver build_concepts.py) ---
with open(os.path.join(DATA, "concepts.json"), encoding="utf-8") as f:
    CONCEPTS = json.load(f)
INTENT = CONCEPTS["intent"]                     # [{term, type, count}] alineado con vectores
COLOR_LEXICON = CONCEPTS["color_lexicon"]        # palabra normalizada -> [valores de acabado]
QUALIFIERS = CONCEPTS["qualifiers"]              # palabra/frase -> 'high'|'low'
PRICE_BANDS = CONCEPTS["price_bands"]            # categoria -> {p25, p75}  (+ '__global__')
TEXTURES = ["mate", "brillo", "satinado", "pulido", "texturizado"]

# El autocompletado SEMANTICO (embeddings model2vec descargados de HuggingFace) esta
# desactivado por defecto: en instancias con poca RAM su carga dispara un OOM que reinicia
# el contenedor en bucle. El autocompletado por PREFIJO funciona sin modelo. Para activarlo
# en un entorno con memoria suficiente, define la variable de entorno ENABLE_SEMANTIC=1.
ENABLE_SEMANTIC = os.getenv("ENABLE_SEMANTIC", "").strip().lower() in ("1", "true", "yes", "on")

CONCEPT_VECTORS = None
if ENABLE_SEMANTIC:
    CONCEPT_VECTORS = np.load(os.path.join(DATA, "concept_vectors.npy"))  # (N, dim) normalizados

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

# El índice de Azure titula el modelo COMPACTADO (sin los '..' comodín): '857853' para el
# modelo '857853...' del catálogo. Sin este mapa se perdían familias enteras (~105/120
# resultados de mobiliario descartados por "no existir" en products.json).
MODEL_COMPACT = {}                    # model sin puntos -> model canónico del catálogo
for m in BY_MODEL:
    MODEL_COMPACT.setdefault(m.replace(".", ""), m)


def resolve_azure_models(scores: dict) -> tuple[dict, list]:
    """Traduce los modelos que devuelve Azure a los del catálogo (resolviendo el
    compactado de comodines). Devuelve ({model_catalogo -> score}, [sin_match])."""
    out, missing = {}, []
    for m, s in scores.items():
        cm = m if m in BY_MODEL else MODEL_COMPACT.get(m.replace(".", ""))
        if cm is None:
            missing.append(m)
        elif s > out.get(cm, -1.0):
            out[cm] = s
    return out, missing

def search_blob(p):
    parts = [p.get("title"), p.get("category"), p.get("subcategory"),
             p.get("collection"), p.get("finish"), p.get("sku"),
             (p.get("desc") or {}).get("marketing"),
             (p.get("desc") or {}).get("extended")]
    return " ".join(x for x in parts if x).lower()

# blobs normalizados (minúsculas y sin acentos): los usan el fallback substring y la
# verificación de atributos ("antideslizante"); los tokens de consulta se normalizan igual.
SEARCH_INDEX = {p["sku"]: _norm(search_blob(p)) for p in PRODUCTS}

# --- Colores principales: agrupan los acabados compuestos del catálogo -----------------
# 'Negro mate', 'Porcelana negra', 'Mármol negro Marquina' caen bajo 'Negro'. stems =
# subcadenas del nombre normalizado; extra = valores que no contienen la palabra. Un
# acabado puede caer en varios colores ('Negro/Blanco', 'Oro rosado'); los que no casan
# con ninguno caen en 'Otros'. La faceta `color` de /search se construye con esto.
MAIN_COLORS = [
    ("Blanco",   ["blanc", "white"], ["Edelweiss", "Pergamon", "Jazmín", "Magnolia", "Perla"]),
    ("Negro",    ["negr", "black"], ["Ébano", "Onix", "Onix/Blanco"]),
    ("Gris",     ["gris", "grey", "graphit", "grafito", "antracita"],
                 ["Cemento", "Hormigón", "Pizarra"]),
    ("Beige",    ["beige", "cream", "arena"], []),
    ("Madera",   ["roble", "fresno", "nogal", "abedul", "aliso", "cedro", "cerezo", "olmo",
                  "madera", "teka", "wenge", "decapado"], []),
    ("Marrón",   ["moka", "cafe", "nogal", "wenge", "brown"], []),
    ("Rojo",     ["roj", "red", "terracota"], []),
    ("Naranja",  ["naranja", "orange"], []),
    ("Azul",     ["azul", "blue"], []),
    ("Verde",    ["verde", "green"], []),
    ("Amarillo", ["amarill", "yellow"], []),
    ("Rosa",     ["rosa", "pink"], []),
    ("Morado",   ["morado", "purpur", "purple", "violet", "lila"], []),
    ("Dorado",   ["oro", "dorad", "gold"], []),
    ("Plateado", ["plata", "silver", "niquel", "nickel"],
                 ["Acero inoxidable", "Acero inoxidable pulido", "Acabado acero inoxidable"]),
    ("Cromado",  ["cromad", "cromo", "chrome"], []),
    ("Cobre",    ["cobre", "copper"], []),
    ("Transparente", ["transparente"], []),
]


def _main_colors_of(finish_seg: str) -> list[str]:
    n = _norm(finish_seg)
    cols = [name for name, stems, extra in MAIN_COLORS
            if any(s in n for s in stems) or finish_seg in extra]
    return cols or ["Otros"]


def _color_stem(w: str) -> str:
    """Raíz sin plural/género ('negros' -> 'negr'), como la de azure_search (módulo opcional)."""
    base = re.sub(r"(?:os|as|es)$", "", w)
    if len(base) < 3:
        base = w
    stem = base[:-1] if base[-1:] in ("o", "a") else base
    return stem if len(stem) >= 3 else base


def _colors_of_term(term: str) -> list[str]:
    """'negros' -> ['Negro']: colores principales que nombra un término de UNA palabra.
    Los compuestos ('negro mate') devuelven [] para conservar la precisión del filtro."""
    tn = _norm(str(term))
    if not tn or " " in tn:
        return []
    ts = _color_stem(tn)
    return [name for name, stems, _extra in MAIN_COLORS
            if ts == _color_stem(_norm(name)) or any(s in tn for s in stems)]


# acabado del catálogo -> colores principales (precalculado sobre todos los segmentos)
FINISH_COLOR = {}
for p in PRODUCTS:
    for seg in str(p.get("finish") or "").split("|"):
        seg = seg.strip()
        if seg and seg not in FINISH_COLOR:
            FINISH_COLOR[seg] = _main_colors_of(seg)

# color principal -> TODOS sus acabados del catálogo. El sidebar marca/desmarca el grupo
# COMPLETO (no solo lo presente en el scope): así desmarcar un color nunca deja acabados
# seleccionados "invisibles" (p. ej. los 17 que aplica el auto-filtro de "negros").
COLOR_FINISHES = defaultdict(list)
for seg, cols in sorted(FINISH_COLOR.items()):
    for c in cols:
        COLOR_FINISHES[c].append(seg)

# vocabulario real del catálogo -> intérprete LLM de consultas (azure_search.analyze_query):
# los filtros que devuelva serán SIEMPRE valores existentes, listos para el sidebar.
def _vocab_of(field):
    vals = set()
    for p in PRODUCTS:
        for seg in str(p.get(field) or "").split("|"):
            if seg.strip():
                vals.add(seg.strip())
    return sorted(vals)

if azure_search is not None:
    azure_search.set_vocab(finishes=CONCEPTS["finishes"],
                           categories=_vocab_of("category_base"),
                           collections=_vocab_of("collection"))

def price_type_of(p):
    pt = p.get("price_type") or p.get("PriceType")
    if pt in ("OnlineFrom", "PVPR"):
        return pt
    return "OnlineFrom" if p.get("ecommerce") else "PVPR"


def summary(p):
    return {
        "sku": p["sku"], "title": p.get("title"), "category": p.get("category"),
        "collection": p.get("collection"), "finish": p.get("finish"),
        "price_rrp": p.get("price_rrp"), "price_type": price_type_of(p),
        "is_spare_part": p.get("is_spare_part"),
        "image": IMAGES.get(p["sku"]), "dims": dims_str(p),
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Solo con ENABLE_SEMANTIC=1 se precarga el modelo de embeddings, en segundo plano para
    # no bloquear el arranque. Por defecto NO se carga: asi se evita la descarga desde HF y
    # el OOM que reinicia el contenedor en bucle. Busqueda/facetas/agrupacion no lo necesitan.
    if ENABLE_SEMANTIC:
        import asyncio
        asyncio.get_running_loop().run_in_executor(None, _model)
    yield


app = FastAPI(title="Roca Buscador PoC", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# resumen de arranque: qué datos y qué motores quedaron disponibles (visible en logs de Railway)
print(f"[startup] productos={len(PRODUCTS)} modelos={len(BY_MODEL)} relaciones={len(RELATIONS)} "
      f"imagenes={len(IMAGES)} suppliers={len(SUPPLIERS)} websort={len(WEBSORT)}", flush=True)
print(f"[startup] azure_search={'ok' if azure_search else 'NO'} "
      f"chat={'ok' if chat else 'NO'} chat_key={'si' if (chat and chat.API_KEY) else 'no'} "
      f"design={'ok' if (design and design.READY) else 'NO'} "
      f"semantic={'on' if ENABLE_SEMANTIC else 'off'} AZURE_K={AZURE_K}", flush=True)
if _chat_import_error is not None:
    print(f"[startup] chat NO cargó -> {_chat_import_error!r} "
          f"(instala backend/requirements.txt: pip install -r requirements.txt)", flush=True)

@app.get("/health")
def health():
    return {"status": "ok", "products": len(PRODUCTS),
            "relations_models": len(RELATIONS),
            "chat_ready": bool(chat and chat.API_KEY),
            "image_ready": bool(image_search and image_search.ready()),
            "design_ready": bool(design and design.READY)}

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
    # semantico (solo si ENABLE_SEMANTIC): cuando hay palabra completa o frase multi-token
    if ENABLE_SEMANTIC and intent_phrase and (ends_space or len(intent_tokens) >= 2 or not last_incomplete):
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


def _num(v):
    """Coerce a numero (las medidas vienen como texto en los datos). None si no se puede."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _len_mm(p):  return _num((p.get("dims") or {}).get("length_mm"))
def _wid_mm(p):  return _num((p.get("dims") or {}).get("width_mm"))
def _hei_mm(p):  return _num((p.get("dims") or {}).get("height_mm"))

# getter de cada faceta de rango (para leave-one-out y para el filtro)
RANGE_GETTERS = {"price": lambda p: _num(p.get("price_rrp")),
                 "length": _len_mm, "width": _wid_mm, "height": _hei_mm}


def _range_ok(v, lo, hi):
    """True si v cae en [lo,hi]. Si el rango no esta activo -> True. Nulo con rango activo -> False."""
    if lo is None and hi is None:
        return True
    if v is None:
        return False
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def matches(p, sel, exclude=None):
    """Comprueba p contra la seleccion de facetas 'sel', saltando 'exclude' (leave-one-out)."""
    if exclude != "category" and sel["categories"]:
        if not any(_field_has(p, "category_base", c) for c in sel["categories"]):
            return False
    if exclude != "collection" and sel["collections"]:
        if not any(_field_has(p, "collection", c) for c in sel["collections"]):
            return False
    if exclude != "finish" and sel["finishes"]:
        if not any(_field_has(p, "finish", fv) for fv in sel["finishes"]):
            return False
    for key, getter in RANGE_GETTERS.items():
        if exclude != key:
            lo, hi = sel[key]
            if not _range_ok(getter(p), lo, hi):
                return False
    return True


def _agg(products, field):
    """Cuenta valor -> nº (campos multivalor 'A|B' cuentan en cada segmento). Orden por count desc."""
    c = Counter()
    for p in products:
        val = p.get(field)
        if not val:
            continue
        for seg in str(val).split("|"):
            seg = seg.strip()
            if seg:
                c[seg] += 1
    return [{"value": k, "count": n} for k, n in c.most_common()]


def _bounds(products, getter):
    vals = [getter(p) for p in products]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"min": min(vals), "max": max(vals)}


def variant_summary(p):
    """Resumen ligero de una variante (acabado) para thumbnails y ficha."""
    return {"sku": p["sku"], "finish": p.get("finish"),
            "image": IMAGES.get(p["sku"]), "price_rrp": p.get("price_rrp"),
            "price_type": price_type_of(p), "dims": dims_str(p)}


def _agg_models(products, field):
    """Cuenta MODELOS distintos por valor (un modelo cuenta en cada valor que tenga).
    Campos multivalor 'A|B' cuentan en cada segmento. Orden por count desc."""
    groups = defaultdict(set)
    for p in products:
        val = p.get(field)
        if not val:
            continue
        for seg in str(val).split("|"):
            seg = seg.strip()
            if seg:
                groups[seg].add(p.get("model"))
    return sorted(({"value": k, "count": len(s)} for k, s in groups.items()),
                  key=lambda x: -x["count"])


def _agg_colors(products):
    """Faceta de colores PRINCIPALES: cuenta modelos del scope por color; cada grupo
    lleva TODOS los acabados del color en el catálogo (COLOR_FINISHES), para que el
    frontend filtre marcando el color ('Negro' -> Negro, Negro mate, Porcelana negra...)."""
    models = defaultdict(set)     # color -> modelos distintos en el scope
    for p in products:
        val = p.get("finish")
        if not val:
            continue
        for seg in str(val).split("|"):
            seg = seg.strip()
            if not seg:
                continue
            for c in FINISH_COLOR.get(seg) or _main_colors_of(seg):
                models[c].add(p.get("model"))
    return sorted(({"value": c, "count": len(ms), "finishes": COLOR_FINISHES[c]}
                   for c, ms in models.items()), key=lambda x: -x["count"])


# órdenes de la parrilla que acepta /search (por defecto, relevancia del motor).
# websort = orden de escaparate de roca.es; es el que pide el frontend al abrir la app.
SORT_KEYS = {"relevance", "websort", "price_asc", "price_desc", "alpha_asc", "alpha_desc"}


@app.get("/search")
def search(q: str = "", limit: int = 30, include_spare: bool = False,
           auto: bool = False, sort: Optional[str] = None,
           subcategory: Optional[str] = None,
           category: Optional[list[str]] = Query(None),
           collection: Optional[list[str]] = Query(None),
           finish: Optional[list[str]] = Query(None),
           min_price: Optional[float] = None, max_price: Optional[float] = None,
           min_length: Optional[float] = None, max_length: Optional[float] = None,
           min_width: Optional[float] = None, max_width: Optional[float] = None,
           min_height: Optional[float] = None, max_height: Optional[float] = None):
    # normaliza los parametros lista (soporta llamada directa además de HTTP)
    category = category if isinstance(category, list) else None
    collection = collection if isinstance(collection, list) else None
    finish = finish if isinstance(finish, list) else None
    sort_key = sort if sort in SORT_KEYS else "relevance"

    q_clean = q.strip()
    tokens = [t for t in re.split(r"\s+", _norm(q)) if t]

    # Interpretación LLM de la query (cacheada; la reutiliza search_models). Siempre que
    # hay texto se usa para la verificación de atributos ("antideslizante"); los FILTROS
    # y el orden solo se auto-aplican con auto=1 (búsqueda nueva del frontend), y solo
    # los que el llamador NO fijó; lo aplicado se devuelve en "auto" para el sidebar.
    analysis = None
    auto_applied = {}
    if q_clean and azure_search is not None:
        try:
            analysis = azure_search.analyze_query(q_clean)
        except Exception as e:  # noqa: BLE001 — sin análisis se busca como siempre
            print(f"[/search] analyze FALLO ({e!r}) -> sin análisis", flush=True)
    if auto:
        if analysis:
            # colores simples ("negros") se amplían al grupo de color COMPLETO: el sidebar
            # marca un color solo si TODO su grupo está seleccionado. Los compuestos
            # ("negro mate") mantienen su lista precisa. El snap corre aunque el LLM no
            # eligiera acabados: "dorados" no es subcadena de ningún acabado ('Oro
            # cepillado'), pero el término SÍ nombra el color Dorado.
            snapped = set(analysis["finish"])
            for t in analysis.get("finish_terms") or []:
                for col in _colors_of_term(t):
                    snapped.update(COLOR_FINISHES.get(col, []))
            fin_auto = sorted(snapped)
            if not finish and fin_auto:
                finish = fin_auto
                auto_applied["finish"] = finish
            if not category and analysis["category"]:
                category = analysis["category"]
                auto_applied["category"] = category
            if not collection and analysis["collection"]:
                collection = analysis["collection"]
                auto_applied["collection"] = collection
            if min_price is None and analysis["min_price"] is not None:
                min_price = analysis["min_price"]
                auto_applied["min_price"] = min_price
            if max_price is None and analysis["max_price"] is not None:
                max_price = analysis["max_price"]
                auto_applied["max_price"] = max_price
            if sort not in SORT_KEYS and analysis.get("sort"):
                sort_key = analysis["sort"]
                auto_applied["sort"] = sort_key
            # precio cualitativo ("baratos"): banda p25/p75 de la PRIMERA categoría
            # detectada (o global), solo sin precio explícito. Mejor de ambas ramas:
            # filtra a la banda Y ordena asc/desc si la query no pidió otro orden.
            band = analysis.get("price_band")
            if band and min_price is None and max_price is None:
                pf = _resolve_price_band_named(band, (category or [None])[0])
                if "min_price" in pf:
                    min_price = pf["min_price"]
                    auto_applied["min_price"] = min_price
                if "max_price" in pf:
                    max_price = pf["max_price"]
                    auto_applied["max_price"] = max_price
                if pf:
                    auto_applied["price_band"] = band
                    if sort not in SORT_KEYS and not analysis.get("sort") and band != "mid":
                        sort_key = "price_asc" if band == "cheap" else "price_desc"
                        auto_applied["sort"] = sort_key
            # tamaño cualitativo ("grande"): percentiles p33/p66 por dimensión de la categoría
            sband = analysis.get("size")
            if sband and category:
                dims_applied = {}
                for dim, smin, smax in _resolve_size_multi(category[0], sband):
                    if dim == "length" and min_length is None and max_length is None:
                        min_length, max_length = smin, smax
                    elif dim == "width" and min_width is None and max_width is None:
                        min_width, max_width = smin, smax
                    elif dim == "height" and min_height is None and max_height is None:
                        min_height, max_height = smin, smax
                    else:
                        continue
                    dims_applied[dim] = {"min": smin, "max": smax}
                if dims_applied:
                    auto_applied["size"] = {"band": sband, "dims": dims_applied}
            if auto_applied:
                print(f"[/search] auto-filtros {auto_applied!r} "
                      f"(texto: {analysis['search_text']!r})", flush=True)

    # Ranking por texto (q):
    #   - sin query  -> navegación: todo el catálogo con score 0 (solo facetas).
    #   - con query  -> Azure AI Search (semántico/híbrido) devuelve {model -> score};
    #                   solo entran esos modelos, ordenados por relevancia de Azure.
    #   - si Azure no está disponible o falla -> fallback al buscador por substring.
    model_scores = None
    use_fallback = False
    if q_clean:
        if azure_search is None:
            use_fallback = True
        else:
            try:
                # solo con auto se busca por el producto "limpio" (atributos -> filtros);
                # sin auto (chat, facetas) el texto conserva colores (keywords).
                q_eff = analysis["search_text"] if (auto and analysis) else None
                model_scores = azure_search.search_models(q_clean, k=AZURE_K, q_eff=q_eff)
            except Exception as e:  # noqa: BLE001 — Azure caído/timeout: no romper la búsqueda
                use_fallback = True
                print(f"[/search] Azure FALLO ({e!r}) -> fallback substring", flush=True)
        engine = "fallback-substring" if use_fallback else "azure-semantic"
        n = 0 if model_scores is None else len(model_scores)
        print(f"[/search] q={q_clean!r} motor={engine} modelos_azure={n}", flush=True)
        # Cobertura: traduce los modelos de Azure a los del catálogo (comodines '..'
        # compactados) y reporta los que de verdad no existen en products.json.
        if model_scores:
            n_azure = len(model_scores)
            model_scores, faltan = resolve_azure_models(model_scores)
            print(f"[/search] cobertura catálogo: {len(model_scores)}/{n_azure} modelos resueltos"
                  + (f" · sin match ({len(faltan)}): {faltan[:10]}" if faltan else ""),
                  flush=True)

    # SCOPE = texto (q) + include_spare + subcategory ; con score para ordenar
    scope = []
    for p in PRODUCTS:
        if not include_spare and p.get("is_spare_part"):
            continue
        if subcategory and not _field_has(p, "subcategory", subcategory):
            continue
        if not q_clean:
            score = 0
        elif use_fallback:
            blob = SEARCH_INDEX[p["sku"]]
            score = sum(1 for t in tokens if t in blob)
            if score == 0:
                continue
            score += sum(1 for t in tokens if t in (p.get("title") or "").lower())
        else:
            score = model_scores.get(p.get("model"))
            if score is None:                 # modelo no devuelto por Azure -> fuera
                continue
        scope.append((score, p))

    # --- atributos técnicos pedidos ("antideslizante", "extraplano"): el LLM los señala
    # y aquí se VERIFICAN contra el texto real de cada producto (título+descripciones).
    # Si algún producto del scope los cumple todos, el scope se queda solo con esos;
    # si ninguno los cumple (dato inexistente o redactado distinto), no se toca (fail-open).
    if scope and analysis and analysis.get("attr_terms"):
        attr_terms = [_norm(t) for t in analysis["attr_terms"] if str(t).strip()]

        def _has_attr(p, t):
            blob = SEARCH_INDEX.get(p["sku"], "")
            forms = {t, t + "s"}
            if t.endswith("s"):
                forms.add(t[:-1])
            if t.endswith("a"):                  # género: termostática -> termostatico
                forms.add(t[:-1] + "o")
            elif t.endswith("o"):
                forms.add(t[:-1] + "a")
            return any(f in blob for f in forms)

        cumplen = [(s, p) for s, p in scope
                   if all(_has_attr(p, t) for t in attr_terms)]
        if cumplen:
            print(f"[/search] atributos {attr_terms}: {len(cumplen)}/{len(scope)} "
                  f"productos los cumplen -> filtrado", flush=True)
            scope = cumplen
        else:
            print(f"[/search] atributos {attr_terms}: 0 productos los cumplen -> ignorados",
                  flush=True)

    sel = {
        "categories": category or [],
        "collections": collection or [],
        "finishes": finish or [],
        "price": (min_price, max_price),
        "length": (min_length, max_length),
        "width": (min_width, max_width),
        "height": (min_height, max_height),
    }

    # SKUs que cumplen todas las facetas, ordenados por score
    matched = [(s, p) for (s, p) in scope if matches(p, sel)]

    # los auto-filtros nunca deben dejar la parrilla a cero (p. ej. color sin stock en esa
    # familia). Relajación progresiva: se retira primero lo INFERIDO (categoría) y al final
    # lo que el usuario pidió con palabras (precio, color), re-filtrando tras cada retirada.
    if not matched and auto_applied:
        for key in ("category", "collection", "size", "min_price", "max_price", "finish"):
            if key not in auto_applied:
                continue
            if key == "category":
                sel["categories"] = []
            elif key == "collection":
                sel["collections"] = []
            elif key == "finish":
                sel["finishes"] = []
            elif key == "size":
                sel["length"] = sel["width"] = sel["height"] = (None, None)
            else:
                lo, hi = sel["price"]
                sel["price"] = (None, hi) if key == "min_price" else (lo, None)
                # sin uno de sus extremos, la etiqueta de banda ("Barato") ya no es cierta
                auto_applied.pop("price_band", None)
            del auto_applied[key]
            matched = [(s, p) for (s, p) in scope if matches(p, sel)]
            print(f"[/search] auto-filtro '{key}' retirado (0 resultados); "
                  f"quedan {list(auto_applied) or 'ninguno'}", flush=True)
            if matched:
                break

    matched.sort(key=lambda x: -x[0])

    # leave-one-out (facetas y variantes-thumbnails, estas ultimas sin el filtro de color)
    def loo(facet):
        return [p for (_, p) in scope if matches(p, sel, exclude=facet)]
    finish_scope = loo("finish")

    # variantes por modelo ignorando el filtro de color -> thumbnails de cada tarjeta
    variants_by_model = defaultdict(list)
    for p in finish_scope:
        variants_by_model[p.get("model")].append(p)

    # agrupa la parrilla por modelo: una tarjeta por modelo, en orden de relevancia
    order, rep = [], {}
    for _, p in matched:
        m = p.get("model")
        if m not in rep:
            rep[m] = p
            order.append(m)

    # reordena la parrilla COMPLETA si se pide (el limit trocea después): precio del
    # modelo = min/max entre sus variantes que cumplen los filtros; sin precio, al final.
    if sort_key != "relevance" and order:
        if sort_key == "websort":
            # ascendente (menor = antes en el escaparate); los modelos sin posición
            # van al final conservando entre sí el orden previo (sort estable)
            order.sort(key=lambda m: WEBSORT.get(m, float("inf")))
        elif sort_key in ("price_asc", "price_desc"):
            asc = sort_key == "price_asc"
            best = {}
            for _, p in matched:
                v = _num(p.get("price_rrp"))
                if v is None:
                    continue
                m = p.get("model")
                cur = best.get(m)
                best[m] = v if cur is None else (min(cur, v) if asc else max(cur, v))
            order.sort(key=lambda m: (m not in best,
                                      (best[m] if asc else -best[m]) if m in best else 0.0))
        else:
            order.sort(key=lambda m: (not (rep[m].get("title") or ""),
                                      _norm(rep[m].get("title") or "")),
                       reverse=(sort_key == "alpha_desc"))

    def build_card(m):
        variants = variants_by_model.get(m) or [rep[m]]
        default = 0
        if sel["finishes"]:
            for i, v in enumerate(variants):
                if any(_field_has(v, "finish", fv) for fv in sel["finishes"]):
                    default = i
                    break
        p = rep[m]
        return {"model": m, "title": p.get("title"), "collection": p.get("collection"),
                "category": p.get("category"), "default": default,
                "variants": [variant_summary(v) for v in variants]}

    facets = {
        "category": _agg_models(loo("category"), "category_base"),
        "collection": _agg_models(loo("collection"), "collection"),
        "finish": _agg_models(finish_scope, "finish"),
        "color": _agg_colors(finish_scope),
        "price": _bounds(loo("price"), RANGE_GETTERS["price"]),
        "dims": {
            "length": _bounds(loo("length"), _len_mm),
            "width": _bounds(loo("width"), _wid_mm),
            "height": _bounds(loo("height"), _hei_mm),
        },
    }

    if q_clean:
        print(f"[/search] q={q_clean!r} -> modelos={len(order)} (scope={len(scope)}) limit={limit}",
              flush=True)

    resp = {"query": q, "sort": sort_key, "total": len(order),
            "results": [build_card(m) for m in order[:limit]],
            "facets": facets}
    if auto:
        # lo que el intérprete aplicó de verdad (tras el salvavidas de 0 resultados), el
        # texto "limpio" de producto para próximas consultas, los tags para la barra de
        # filtros y la corrección de erratas para el banner "Mostrando resultados para…".
        corrected_query = analysis["corrected_query"] if analysis else q_clean
        resp["auto"] = {"search_text": analysis["search_text"] if analysis else q_clean,
                        "applied": auto_applied,
                        "tags": _auto_tags(auto_applied, analysis or {}),
                        "corrected_query": corrected_query,
                        "corrected": bool(analysis) and _norm(corrected_query) != _norm(q_clean)}
    return resp


# --------------------------------------------- precio y tamaño cualitativos ("barato", "grande")
# Portados de la rama feat/nl-search-filters: resuelven las bandas contra la distribución
# REAL de la categoría (percentiles), no con umbrales fijos.
SIZE_LABEL = {"small": "pequeño", "medium": "mediano", "large": "grande"}
_DIM_GETTER = {"length": _len_mm, "width": _wid_mm, "height": _hei_mm}
_DIM_PARAM = {"length": ("min_length", "max_length"),
              "width": ("min_width", "max_width"),
              "height": ("min_height", "max_height")}


def _category_products(category):
    return [p for p in PRODUCTS if _field_has(p, "category_base", category)]


def _category_dim_percentiles(products, dimension):
    """min, p33, p66, max (y cobertura) de una dimension para un conjunto de productos.
    None si no hay valores. La cobertura es la fraccion de productos con valor numerico."""
    getter = _DIM_GETTER.get(dimension)
    if not getter or not products:
        return None
    vals = [v for v in (getter(p) for p in products) if v is not None]
    if not vals:
        return None
    arr = np.array(vals, dtype="float64")
    return {"min": float(arr.min()), "p33": float(np.percentile(arr, 33)),
            "p66": float(np.percentile(arr, 66)), "max": float(arr.max()),
            "coverage": len(vals) / len(products)}


def _resolve_size_multi(category, band):
    """Para cada dimension RELEVANTE de la categoria (cobertura >=40% y con variacion),
    reparte por tercios de PERCENTIL: small=[min,p33], medium=[p33,p66], large=[p66,max].
    Devuelve [(dimension, min, max), ...] (vacio si no hay dims relevantes)."""
    if band not in ("small", "medium", "large") or not category:
        return []
    prods = _category_products(category)
    out = []
    for dim in ("length", "width", "height"):
        pc = _category_dim_percentiles(prods, dim)
        if not pc or pc["coverage"] < 0.4 or pc["p66"] <= pc["p33"]:
            continue
        if band == "small":
            r = (pc["min"], pc["p33"])
        elif band == "medium":
            r = (pc["p33"], pc["p66"])
        else:
            r = (pc["p66"], pc["max"])
        out.append((dim, round(r[0]), round(r[1])))
    return out


PRICE_BAND_LABEL = {"cheap": "Barato", "mid": "Gama media", "expensive": "Caro"}


def _resolve_price_band_named(name, category):
    """Precio cualitativo -> min/max usando las bandas p25/p75 de la categoria (o global).
    cheap=<=p25, expensive=>=p75, mid=[p25,p75]. Devuelve dict de filtros o {}."""
    if name not in ("cheap", "mid", "expensive"):
        return {}
    b = PRICE_BANDS.get(category) or PRICE_BANDS["__global__"]
    if name == "cheap":
        return {"max_price": b["p25"]}
    if name == "expensive":
        return {"min_price": b["p75"]}
    return {"min_price": b["p25"], "max_price": b["p75"]}


def _price_label(mn, mx):
    if mn is not None and mx is not None:
        return f"{int(mn)} – {int(mx)} €"
    if mx is not None:
        return f"Hasta {int(mx)} €"
    if mn is not None:
        return f"Desde {int(mn)} €"
    return None


def _auto_tags(applied, analysis):
    """Chips de los filtros que aplicó el intérprete (barra de tags del frontend).
    El orden no lleva tag: tiene su propio selector en la parrilla."""
    tags = []
    if applied.get("category"):
        tags.append({"id": "category", "type": "category",
                     "label": " · ".join(applied["category"])})
    if applied.get("collection"):
        tags.append({"id": "collection", "type": "collection",
                     "label": " · ".join(applied["collection"])})
    if applied.get("finish"):
        terms = [t.capitalize() for t in (analysis.get("finish_terms") or []) if t]
        tags.append({"id": "finish", "type": "finish",
                     "label": " · ".join(terms) or "Color"})
    if applied.get("min_price") is not None or applied.get("max_price") is not None:
        label = (PRICE_BAND_LABEL.get(applied.get("price_band"))
                 or _price_label(applied.get("min_price"), applied.get("max_price")))
        tags.append({"id": "price", "type": "price", "label": label})
    if applied.get("size"):
        s = applied["size"]
        tags.append({"id": "size", "type": "size", "dimensions": sorted(s["dims"]),
                     "label": f"Tamaño {SIZE_LABEL.get(s['band'], s['band'])}"})
    return tags


# ---------------------------------------------------------------- búsqueda por imagen
MAX_PHOTOS = 6
MAX_PHOTO_MB = 10
IMAGE_TOP_K = 50      # candidatos por foto: alto para que el filtro de texto tenga material


def _sel_from_filters(filters):
    """Filtros de parse_query (acabado/precio) -> formato 'sel' que entiende matches()."""
    sel = {"categories": [], "collections": [], "finishes": [],
           "price": (None, None), "length": (None, None),
           "width": (None, None), "height": (None, None)}
    for f in filters:
        if f["type"] == "finish":
            sel["finishes"] = f.get("values") or []
        elif f["type"] == "price":
            sel["price"] = (f.get("min_price"), f.get("max_price"))
    return sel


def _filter_by_text(sku_scores, q):
    """El texto FILTRA los candidatos visuales (el score visual sigue ordenando):
    filtros estructurados de parse_query (acabado/banda de precio) + los tokens de
    intención restantes por substring (AND) contra SEARCH_INDEX."""
    parsed = parse_query(q)
    sel = _sel_from_filters(parsed["filters"])
    # _norm (no .lower()): los blobs de SEARCH_INDEX están sin acentos, así que los
    # tokens deben normalizarse igual o "grifería"/"cerámica" no casarían nunca.
    tokens = [t for t in _norm(parsed["intent_phrase"]).split() if t]
    out = {}
    for sku, score in sku_scores.items():
        p = BY_SKU.get(sku)
        if not p or not matches(p, sel):
            continue
        blob = SEARCH_INDEX.get(sku, "")
        if tokens and not all(t in blob for t in tokens):
            continue
        out[sku] = score
    return out


def _image_cards(sku_scores, limit=30):
    """{sku: score} -> (total_modelos, ModelCards ordenadas por score). Score de modelo =
    máx de sus SKUs; default = la variante con mejor score. Los recambios SÍ entran
    (identificar un recambio por foto es el caso de uso principal)."""
    best = {}                                # model -> (score, sku)
    for sku, score in sku_scores.items():
        p = BY_SKU.get(sku)
        m = p.get("model") if p else None
        if not m:
            continue
        if m not in best or score > best[m][0]:
            best[m] = (score, sku)
    order = sorted(best, key=lambda m: -best[m][0])
    cards = []
    for m in order[:limit]:
        variants = BY_MODEL[m]
        # el fallback 0 es inalcanzable por construccion (best[m][1] sale del mismo
        # BY_SKU que puebla BY_MODEL); protege solo ante datos inconsistentes futuros
        default = next((i for i, v in enumerate(variants) if v["sku"] == best[m][1]), 0)
        rep = variants[default]
        cards.append({"model": m, "title": rep.get("title"),
                      "collection": rep.get("collection"), "category": rep.get("category"),
                      "default": default,
                      "variants": [variant_summary(v) for v in variants]})
    return len(order), cards


@app.post("/search/image")
def search_image(images: list[UploadFile] = File(...),
                 q: str = Form(""), mode: str = Form("same")):
    """Busca productos por foto(s) vía el endpoint DINOv2. mode='same' fusiona las
    fotos en un ranking (mismo producto, varios ángulos); mode='distinct' devuelve
    un ranking por foto. `q` opcional refina (filtra) los matches visuales."""
    if image_search is None or not image_search.ready():
        raise HTTPException(503, "Búsqueda por imagen no configurada (falta IMAGE_SEARCH_API_KEY)")
    if not 1 <= len(images) <= MAX_PHOTOS:
        raise HTTPException(400, f"Sube entre 1 y {MAX_PHOTOS} fotos")
    blobs = []
    for i, up in enumerate(images, 1):
        if up.content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(400, f"Foto {i}: formato no soportado ({up.content_type})")
        data = up.file.read()
        if not data:
            raise HTTPException(400, f"Foto {i}: fichero vacío")
        if len(data) > MAX_PHOTO_MB * 1024 * 1024:
            raise HTTPException(400, f"Foto {i}: supera {MAX_PHOTO_MB}MB")
        blobs.append(data)

    try:
        rankings = image_search.query_images(blobs, top_k=IMAGE_TOP_K)
    except image_search.EndpointError as e:
        raise HTTPException(400, f"El servicio no pudo procesar una foto: {e}")
    except Exception as e:  # noqa: BLE001 — endpoint caído/timeout: error legible
        print(f"[/search/image] DINO FALLO ({e!r})", flush=True)
        raise HTTPException(502, "El servicio de búsqueda por imagen no responde; inténtalo de nuevo")

    q_clean = q.strip()
    if mode == "distinct":
        groups = []
        for i, rk in enumerate(rankings, 1):
            cand = _filter_by_text(rk, q_clean) if q_clean else rk
            total, cards = _image_cards(cand)
            groups.append({"photo": i, "total": total, "results": cards})
        print(f"[/search/image] fotos={len(blobs)} q={q_clean!r} mode=distinct "
              f"-> {[g['total'] for g in groups]}", flush=True)
        return {"query": q, "mode": "distinct", "groups": groups}

    fused = image_search.fuse_same(rankings)
    cand = _filter_by_text(fused, q_clean) if q_clean else fused
    total, cards = _image_cards(cand)
    print(f"[/search/image] fotos={len(blobs)} q={q_clean!r} candidatos={len(fused)} "
          f"-> modelos={total}", flush=True)
    return {"query": q, "total": total, "results": cards, "facets": None}


# ---- chat IA (opcional): usa la MISMA search() de arriba como fuente de verdad ----
if chat is not None:
    chat.configure(search)

# ---- "Diseña tu baño" (opcional): renders IA sobre los índices del catálogo ----
if design is not None:
    design.configure(BY_SKU, IMAGES, summary, search)


@app.post("/api/design")
async def api_design(body: dict):
    """Render 'Diseña tu baño' (ver design.py). Tarda ~30-90 s por imagen."""
    if design is None:
        raise HTTPException(503, "Diseño IA no disponible en este backend.")
    try:
        return await design.render(body)
    except design.DesignError as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/design/analyze")
async def api_design_analyze(body: dict):
    """Renueva tu baño: foto -> elementos detectados + candidatos Roca por elemento."""
    if design is None:
        raise HTTPException(503, "Diseño IA no disponible en este backend.")
    try:
        return await design.analyze(body)
    except design.DesignError as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/chat")
async def api_chat(body: dict):
    """Stream NDJSON de eventos del agente (text/tool/grid/done/error). Ver chat.stream_turn."""
    async def gen():
        if chat is None:
            yield json.dumps({"type": "error",
                              "message": f"Chat IA no disponible en el backend ({_chat_import_error}). "
                                         "Instala las dependencias (pip install -r requirements.txt) y reinicia."},
                             ensure_ascii=False) + "\n"
            return
        async for ev in chat.stream_turn(body.get("text", ""),
                                         session_id=body.get("session_id"),
                                         view=body.get("view")):
            yield json.dumps(ev, ensure_ascii=False) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
        "model": p.get("model"),   # para el contexto del chat (manuales por modelo)
        "subcategory": p.get("subcategory"),
        "desc": p.get("desc") or {"marketing": None, "extended": None},
        "variants": [variant_summary(v) for v in BY_MODEL.get(p.get("model"), [])],
        "relations": grouped,
    }

# ---------------------------------------------------------------- compra offline
import math

def _haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en km entre dos coordenadas (formula del semiverseno)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/suppliers/nearby")
def suppliers_nearby(lat: float, lon: float, limit: int = 8,
                     exposition_only: bool = False):
    """Puntos de venta ordenados por cercanía a (lat, lon). Para la compra offline:
    el frontend pide la geolocalización del usuario y llama aquí."""
    pool = [s for s in SUPPLIERS if s.get("exposition")] if exposition_only else SUPPLIERS
    ranked = []
    for s in pool:
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        ranked.append({**s, "distance_km": round(d, 1)})
    ranked.sort(key=lambda s: s["distance_km"])
    return {"origin": {"lat": lat, "lon": lon}, "count": len(ranked),
            "suppliers": ranked[:limit]}


@app.get("/suppliers")
def suppliers_all():
    """Red completa (para mostrar todos los puntos si el usuario deniega la ubicación)."""
    return {"count": len(SUPPLIERS), "suppliers": SUPPLIERS}


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
