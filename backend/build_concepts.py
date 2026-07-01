"""
Offline: construye el vocabulario de conceptos + lexicos + embeddings para el
autocompletado semantico del buscador.

Genera en data/:
  - concepts.json         conceptos de intencion (categoria/subcategoria/coleccion)
                          con recuento, lexico de color, lexico de calificadores
                          y umbrales de precio por categoria.
  - concept_vectors.npy   matriz de embeddings alineada con concepts["intent"].

Ejecutar:  ./.venv/bin/python build_concepts.py
"""
import json, os, unicodedata
from collections import defaultdict
import numpy as np
from model2vec import StaticModel

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MODEL = "minishlab/M2V_multilingual_output"

SPARE = "spare parts"


def norm(s: str) -> str:
    """minusculas sin acentos, para matching robusto"""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def split_field(raw: str):
    """campo con posibles multi-valores separados por '|', limpia 'Spare Parts'"""
    for part in (raw or "").split("|"):
        p = part.strip()
        if p and p.lower() != SPARE:
            yield p


# ---------------------------------------------------------------- carga
with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
    PRODUCTS = json.load(f)

# solo productos vendibles cuentan para ranking/percentiles (no recambios)
SELLABLE = [p for p in PRODUCTS if not p.get("is_spare_part")]

# ---------------------------------------------------------------- conceptos de intencion
# type -> {term -> count}  (contamos sobre productos vendibles)
counts = {"category": defaultdict(int), "subcategory": defaultdict(int),
          "collection": defaultdict(int)}
FIELD = {"category": "category_base", "subcategory": "subcategory",
         "collection": "collection"}

for p in SELLABLE:
    for typ, field in FIELD.items():
        for term in split_field(p.get(field) or ""):
            counts[typ][term] += 1

intent = []
for typ in ("category", "subcategory", "collection"):
    for term, c in counts[typ].items():
        intent.append({"term": term, "type": typ, "count": c})
# orden estable por recuento desc (util para prefijo)
intent.sort(key=lambda x: -x["count"])

# ---------------------------------------------------------------- lexico de acabados / color
ALL_FINISHES = sorted({f for p in PRODUCTS for f in split_field(p.get("finish") or "")})

# raiz de color (normalizada) -> raices que deben aparecer en el acabado
COLOR_ROOTS = {
    "negro": ["negro"], "negra": ["negro"],
    "blanco": ["blanco"], "blanca": ["blanco"],
    "gris": ["gris"], "antracita": ["antracita"],
    "dorado": ["oro", "dorado"], "dorada": ["oro", "dorado"], "oro": ["oro", "dorado"],
    "cromo": ["cromado"], "cromado": ["cromado"], "cromada": ["cromado"],
    "plata": ["plata"], "plateado": ["plata"], "niquel": ["niquel"],
    "bronce": ["bronce"], "cobre": ["cobre"], "beige": ["beige"],
    "azul": ["azul"], "verde": ["verde"], "rojo": ["rojo"], "roja": ["rojo"],
    "rosa": ["rosa"], "amarillo": ["amarillo"], "onix": ["onix"], "onice": ["onix"],
    "marron": ["marron", "nogal", "roble", "fresno", "abedul"],
    "madera": ["nogal", "roble", "fresno", "abedul", "aliso", "madera"],
}
TEXTURES = ["mate", "brillo", "satinado", "pulido", "texturizado"]

# color_word (normalizado) -> lista de valores de acabado reales del catalogo
color_lexicon = {}
for word, roots in COLOR_ROOTS.items():
    matches = [f for f in ALL_FINISHES if any(r in norm(f) for r in roots)]
    if matches:
        color_lexicon[word] = matches

# ---------------------------------------------------------------- calificadores de precio
QUALIFIERS = {
    "lujo": "high", "premium": "high", "exclusivo": "high", "exclusiva": "high",
    "gama alta": "high", "alta gama": "high", "top": "high",
    "barato": "low", "barata": "low", "economico": "low", "economica": "low",
    "basico": "low", "basica": "low", "asequible": "low",
}

# umbrales p25/p75 de RRP por categoria (category_base) + global
by_cat_prices = defaultdict(list)
all_prices = []
for p in SELLABLE:
    rrp = p.get("price_rrp")
    if not rrp:
        continue
    all_prices.append(rrp)
    for term in split_field(p.get("category_base") or ""):
        by_cat_prices[term].append(rrp)

def band(prices):
    a = np.array(prices)
    return {"p25": round(float(np.percentile(a, 25)), 2),
            "p75": round(float(np.percentile(a, 75)), 2)}

price_bands = {"__global__": band(all_prices)}
for cat, prices in by_cat_prices.items():
    if len(prices) >= 8:                     # evita percentiles de muestras minimas
        price_bands[cat] = band(prices)

# ---------------------------------------------------------------- embeddings
print(f"conceptos de intencion: {len(intent)} | acabados: {len(ALL_FINISHES)}")
print(f"cargando modelo {MODEL} ...")
model = StaticModel.from_pretrained(MODEL)
vectors = model.encode([c["term"] for c in intent], show_progress_bar=False)
vectors = np.asarray(vectors, dtype=np.float32)
# normaliza para que coseno == producto punto
vectors /= (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)

np.save(os.path.join(DATA, "concept_vectors.npy"), vectors)
with open(os.path.join(DATA, "concepts.json"), "w", encoding="utf-8") as f:
    json.dump({
        "model": MODEL,
        "intent": intent,
        "finishes": ALL_FINISHES,
        "color_lexicon": color_lexicon,
        "qualifiers": QUALIFIERS,
        "price_bands": price_bands,
    }, f, ensure_ascii=False)

print(f"guardado concepts.json + concept_vectors.npy {vectors.shape}")
print(f"lexico color: {len(color_lexicon)} palabras | price_bands: {len(price_bands)} categorias")
