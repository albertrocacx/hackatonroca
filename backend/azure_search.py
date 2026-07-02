"""
Búsqueda semántica/híbrida contra Azure AI Search + embeddings de Azure OpenAI.

Aísla toda la dependencia de Azure. Expone `search_models(q, k)` que devuelve un
dict {model -> score} ordenable, para que main.search() reutilice su maquinaria de
facetas/agrupación por modelo tal cual.

El índice (rag-test4) tiene un chunk por producto; su campo `title` es el código de
modelo con sufijo '.md' (p.ej. '5A208EC00.md' -> model '5A208EC00'), que coincide
exactamente con el campo `model` de products.json.

Config por variables de entorno (con fallback a los valores del PoC). ROTAR las keys
y moverlas a entorno en producción:
  AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_SEARCH_INDEX
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION
  AZURE_EMBED_DEPLOYMENT, AZURE_EMBED_DIMENSIONS
"""
import json
import os
import re
import time
import unicodedata
from collections import OrderedDict

# Carga variables desde backend/.env (ruta absoluta: independiente del cwd desde el que
# se arranque uvicorn). load_dotenv NO pisa variables ya definidas en el entorno, así que
# en producción (Railway) mandan las vars del panel. Si python-dotenv no está instalado,
# se usan las variables que ya haya en el entorno.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

# --- Configuración: TODO viene de entorno / .env (sin secretos en el código) ---
SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME       = os.getenv("AZURE_SEARCH_INDEX", "rag-test4")

AOAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_API_KEY     = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBED_DEPLOYMENT", "text-embedding-3-large-2")
EMBED_DIMENSIONS = int(os.getenv("AZURE_EMBED_DIMENSIONS") or "3072")
VECTOR_FIELD     = os.getenv("AZURE_VECTOR_FIELD", "text_vector")

# LLM "por delante" de la query (Foundry, Responses API): corrige erratas y extrae
# keywords ANTES de embeber/buscar. Mismo deployment/claves que el OCR y el chat.
# Desactivable con AZURE_LLM_REFINE=0.
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "https://aihackathonfoundry.services.ai.azure.com/openai/v1")
FOUNDRY_API_KEY  = os.getenv("FOUNDRY_API_KEY") or os.getenv("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT  = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-5.4")
LLM_REFINE       = (os.getenv("AZURE_LLM_REFINE", "1") != "0")

# --- Clientes (perezosos: no bloquear el import ni el arranque) ---
_aoai = None
_search = None
_llm = None


def _clients():
    global _aoai, _search
    if _aoai is None:
        _aoai = AzureOpenAI(azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_API_KEY,
                            api_version=AOAI_API_VERSION)
    if _search is None:
        _search = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME,
                               credential=AzureKeyCredential(SEARCH_API_KEY))
    return _aoai, _search


def _model_from_title(title: str) -> str | None:
    """'5A208EC00.md' -> '5A208EC00' (== campo model del catálogo). Soporta comodines '..'."""
    if not title:
        return None
    return title[:-3] if title.endswith(".md") else title


# =============================== Interpretación de la query ===============================
# analyze_query: un LLM (gpt-5.4) "por delante" que corrige erratas Y separa la consulta en
# QUÉ producto se busca (texto para embeber/buscar) y QUÉ atributos son filtros del catálogo
# (acabado, categoría, colección, precio). Los valores se validan contra el vocabulario REAL
# del catálogo (inyectado por main.py con set_vocab), así el frontend puede marcar los
# filtros tal cual en su sidebar. Fail-open: sin LLM, la query sigue tal cual y sin filtros.

_VOCAB: dict[str, list[str]] = {"finishes": [], "categories": [], "collections": []}
_VOCAB_NORM: dict[str, dict[str, str]] = {}   # campo -> {valor normalizado -> valor canónico}
_ANALYZE_SYS: str | None = None               # prompt construido con el vocabulario


def _norm_txt(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def set_vocab(finishes=(), categories=(), collections=()):
    """main.py inyecta los valores reales del catálogo: van al prompt del LLM y sirven
    para validar su salida (un valor que no exista en el catálogo se descarta)."""
    global _ANALYZE_SYS
    _VOCAB["finishes"] = list(finishes)
    _VOCAB["categories"] = list(categories)
    _VOCAB["collections"] = list(collections)
    for field, values in _VOCAB.items():
        _VOCAB_NORM[field] = {_norm_txt(v): v for v in values}
    _ANALYZE_SYS = None       # el prompt depende del vocabulario
    _ANALYZE_CACHE.clear()
    print(f"[analyze] vocabulario: {len(_VOCAB['finishes'])} acabados, "
          f"{len(_VOCAB['categories'])} categorías, {len(_VOCAB['collections'])} colecciones",
          flush=True)


_ANALYZE_RULES = """Eres el intérprete de consultas de un buscador de productos de baño y cocina (Roca).
Corrige las erratas y separa QUÉ producto se busca de QUÉ atributos filtrables se piden.
Responde SOLO con un JSON válido, sin markdown ni explicaciones, con EXACTAMENTE esta forma:
{"keywords": "...", "corrected_query": "...", "search_text": "...", "finish_terms": [], "attr_terms": [], "finish": [], "category": [], "collection": [], "min_price": null, "max_price": null, "price_band": null, "size": null, "sort": null}

- keywords: TODAS las palabras clave corregidas (producto, color, material, forma), en minúsculas ("lababo negro" -> "lavabo negro").
- corrected_query: la frase COMPLETA del usuario con las erratas corregidas, mismas palabras y orden, sin traducir ("patos de ducha blancos" -> "platos de ducha blancos"). Si no hay erratas, la frase tal cual.
- search_text: SOLO el producto y sus cualidades NO mapeadas a filtros (tipo, forma, uso), en minúsculas. NUNCA vacío: como mínimo el tipo de producto. Si un color/material no encaja con ningún ACABADO de la lista, consérvalo aquí.
- finish_terms: las expresiones de color/material/textura que mencione el usuario, corregidas, en singular y minúsculas ("negros" -> "negro"); si color y textura van juntos, júntalos en una ("negro mate"). Si no menciona: [].
- attr_terms: características técnicas o funcionales pedidas que NO son color/acabado ni el tipo de producto: "antideslizante", "extraplano", "rimless", "termostática", "empotrable"... corregidas, en singular y minúsculas. DÉJALAS también en keywords y search_text. Si no pide ninguna: [].
- finish: los ACABADOS de la lista que encajen con lo mencionado aunque NO contengan la palabra (p. ej. "rojo" -> terracotas y Passion Red; "blanco" -> también White e Ice White; "madera" -> robles, fresnos, nogales...). El sistema añadirá aparte los que contengan la palabra literal. Si no menciona color/material: [].
- category: las CATEGORÍAS de la lista que correspondan al tipo de producto buscado (varias si más de una encaja, p. ej. "muebles" -> "Muebles" y "Muebles de baño"); si no es evidente: [].
- collection: colecciones de la lista SOLO si el usuario las nombra explícitamente ("T-500", "Ona"...); si no: [].
- min_price / max_price: números solo si menciona precio en euros ("por menos de 200" -> max_price 200; "entre 100 y 300" -> ambos); si no: null.
- price_band: precio CUALITATIVO sin número: "barato/económico/asequible/básico" -> "cheap"; "caro/premium/lujo/gama alta/exclusivo" -> "expensive"; "gama media" -> "mid". Con número explícito usa min/max y deja null. Esas palabras NO van en search_text. Si no lo menciona: null.
- size: tamaño RELATIVO sin medidas: "pequeño/compacto/mini" -> "small"; "mediano" -> "medium"; "grande/amplio/XL" -> "large". NO va en search_text. Con medidas explícitas ("de 80 cm") déjalo null y conserva la medida en search_text. Si no lo menciona: null.
- sort: SOLO si pide un orden explícito: "price_desc" ("descendente por precio", "de mayor a menor precio", "los más caros primero"), "price_asc" ("de menor a mayor", "ordenado por precio ascendente"), "alpha_asc" (alfabético), "alpha_desc" (alfabético inverso). Un adjetivo suelto como "barato" o "caro" es price_band, NO sort. Las palabras de orden NO van en keywords ni en search_text. Si no lo pide: null.
- Copia los valores EXACTOS de las listas (mayúsculas, acentos y espacios incluidos)."""


def _analyze_sys() -> str:
    global _ANALYZE_SYS
    if _ANALYZE_SYS is None:
        _ANALYZE_SYS = (_ANALYZE_RULES
                        + "\n\nACABADOS: " + " | ".join(_VOCAB["finishes"])
                        + "\nCATEGORÍAS: " + " | ".join(_VOCAB["categories"])
                        + "\nCOLECCIONES: " + " | ".join(_VOCAB["collections"]))
    return _ANALYZE_SYS


_ANALYZE_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_ANALYZE_CACHE_MAX = 256


def _llm_client():
    global _llm
    if _llm is None:
        from openai import OpenAI  # cliente OpenAI apuntando al endpoint Foundry (/openai/v1)
        _llm = OpenAI(base_url=FOUNDRY_ENDPOINT, api_key=FOUNDRY_API_KEY)
    return _llm


_VALID_SORTS = {"price_asc", "price_desc", "alpha_asc", "alpha_desc"}


def _empty_analysis(q: str) -> dict:
    return {"keywords": q, "corrected_query": q, "search_text": q, "finish_terms": [],
            "attr_terms": [], "finish": [], "category": [], "collection": [],
            "min_price": None, "max_price": None, "price_band": None, "size": None,
            "sort": None}


def _local_analysis(q: str) -> dict:
    """Fallback SIN LLM (content filter de Azure —bloquea p. ej. "inodoros negros"—,
    timeouts, cuota...): un token es de color/acabado si casa con algún acabado del
    catálogo; los consecutivos se agrupan ("negro mate"). Deriva el filtro con
    _expand_finish_terms y limpia el texto. Erratas/categoría/precio/orden solo los
    da el LLM, aquí quedan sin detectar."""
    terms, rest, cur = [], [], []
    for t in q.split():
        if _expand_finish_terms([t]):
            cur.append(t)
        else:
            if cur:
                terms.append(" ".join(cur))
                cur = []
            rest.append(t)
    if cur:
        terms.append(" ".join(cur))
    a = _empty_analysis(q)
    finish = _expand_finish_terms(terms) if terms else []
    if finish:
        a["finish_terms"] = [_norm_txt(t) for t in terms]
        a["finish"] = finish
        a["search_text"] = " ".join(rest).lower() or q
        print(f"[analyze] fallback local {q!r} -> texto={a['search_text']!r} "
              f"terms={a['finish_terms']} finish={len(finish)} acabados", flush=True)
    return a


def _copy_analysis(a: dict) -> dict:
    """Copia defensiva: la caché comparte el dict y los llamadores reciben listas propias."""
    return {**a, "finish_terms": list(a["finish_terms"]), "attr_terms": list(a["attr_terms"]),
            "finish": list(a["finish"]), "category": list(a["category"]),
            "collection": list(a["collection"])}


def _parse_llm_json(raw: str) -> dict | None:
    """Extrae el primer objeto JSON del texto (tolera ```json ... ``` y prosa alrededor)."""
    s = (raw or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _color_stem(w: str) -> str:
    """Raíz de una palabra de color sin plural ni género: 'negros'/'negra' -> 'negr',
    para casar 'Negro mate', 'Porcelana negra', 'Perfil negro mate'... ('oro' queda 'oro')."""
    base = re.sub(r"(?:os|as|es)$", "", w)
    if len(base) < 3:
        base = w
    stem = base[:-1] if base[-1:] in ("o", "a") else base
    return stem if len(stem) >= 3 else base


def _expand_finish_terms(terms: list[str]) -> list[str]:
    """Derivación determinística contra el catálogo: para cada término de color/textura,
    TODOS los acabados cuyo nombre contiene su raíz (los términos compuestos exigen todas:
    'negro mate' -> solo los negros mate). Complementa a la elección semántica del LLM,
    que cubre los no literales ('rojo' -> Terracota); esto garantiza los literales."""
    out = []
    for term in terms:
        stems = [_color_stem(w) for w in _norm_txt(str(term)).split()]
        stems = [s for s in stems if s]
        if not stems:
            continue
        # sobre la lista completa, NO sobre el mapa normalizado: el catálogo tiene
        # duplicados con distinta caja ('Negro mate' / 'Negro Mate') y el filtro de
        # /search compara texto exacto, así que hay que incluir TODAS las variantes.
        for canon in _VOCAB.get("finishes") or []:
            norm = _norm_txt(canon)
            if all(s in norm for s in stems) and canon not in out:
                out.append(canon)
    return out


def _valid_values(field: str, values) -> list[str]:
    """Filtra la salida del LLM contra el vocabulario real (comparación sin acentos/caja)."""
    if isinstance(values, str):
        values = [values]
    lookup = _VOCAB_NORM.get(field) or {}
    out, seen = [], set()
    for v in values if isinstance(values, list) else []:
        canon = lookup.get(_norm_txt(str(v)))
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def analyze_query(q: str) -> dict:
    """LLM por delante de la búsqueda. Devuelve:
      keywords    -> query corregida completa (incluye color/material): pata híbrida normal.
      search_text -> solo el producto (sin atributos mapeados): más peso al "nombre".
      finish/category/collection/min_price/max_price -> filtros con valores DEL catálogo.
    Fail-open: desactivado o con error devuelve la query tal cual y sin filtros."""
    if not q.strip():
        return _empty_analysis(q)
    if not LLM_REFINE:
        print(f"[analyze] desactivado (AZURE_LLM_REFINE=0) -> análisis local {q!r}", flush=True)
        return _local_analysis(q)
    if q in _ANALYZE_CACHE:
        _ANALYZE_CACHE.move_to_end(q)
        return _copy_analysis(_ANALYZE_CACHE[q])
    try:
        t0 = time.perf_counter()
        # El content filter de Azure corta a veces la respuesta a mitad de JSON
        # (status=incomplete, reason=content_filter) de forma NO determinista: la misma
        # query suele salir limpia al reintentar. Un segundo intento antes del fallback.
        d = None
        for _ in range(2):
            r = _llm_client().responses.create(
                model=CHAT_DEPLOYMENT, max_output_tokens=8000,
                input=[{"role": "system", "content": _analyze_sys()},
                       {"role": "user", "content": q}],
            )
            d = _parse_llm_json(r.output_text or "")
            if d is not None:
                break
        dt = (time.perf_counter() - t0) * 1000
    except Exception as e:  # noqa: BLE001 — LLM caído/filtrado/timeout: análisis local
        print(f"[analyze] LLM FALLO ({e!r}) -> fallback local {q!r}", flush=True)
        return _local_analysis(q)
    if d is None:
        print(f"[analyze] JSON ilegible -> fallback local {q!r}", flush=True)
        return _local_analysis(q)
    raw_terms = d.get("finish_terms")
    finish_terms = [str(t).strip() for t in raw_terms if str(t).strip()] \
        if isinstance(raw_terms, list) else []
    raw_attrs = d.get("attr_terms")
    attr_terms = [str(t).strip().lower()[:40] for t in raw_attrs if str(t).strip()][:4] \
        if isinstance(raw_attrs, list) else []
    # acabados = elección semántica del LLM (validada) ∪ derivación literal por término
    finish = _valid_values("finishes", d.get("finish"))
    for extra in _expand_finish_terms(finish_terms):
        if extra not in finish:
            finish.append(extra)
    a = {
        "keywords": (str(d.get("keywords") or "").strip() or q),
        "corrected_query": (str(d.get("corrected_query") or "").strip() or q),
        "search_text": (str(d.get("search_text") or "").strip() or q),
        "finish_terms": finish_terms,
        "attr_terms": attr_terms,
        "finish": finish,
        "category": _valid_values("categories", d.get("category")),
        "collection": _valid_values("collections", d.get("collection")),
        "min_price": _to_num(d.get("min_price")),
        "max_price": _to_num(d.get("max_price")),
        "price_band": d.get("price_band") if d.get("price_band") in ("cheap", "mid", "expensive") else None,
        "size": d.get("size") if d.get("size") in ("small", "medium", "large") else None,
        "sort": d.get("sort") if d.get("sort") in _VALID_SORTS else None,
    }
    print(f"[analyze] {q!r} -> texto={a['search_text']!r} terms={finish_terms} "
          f"attrs={attr_terms} finish={a['finish']} cat={a['category']} col={a['collection']} "
          f"precio=({a['min_price']},{a['max_price']}) banda={a['price_band']} "
          f"tam={a['size']} sort={a['sort']} ({CHAT_DEPLOYMENT}, {dt:.0f}ms)", flush=True)
    _ANALYZE_CACHE[q] = a
    if len(_ANALYZE_CACHE) > _ANALYZE_CACHE_MAX:
        _ANALYZE_CACHE.popitem(last=False)
    return _copy_analysis(a)


def refine_query(q: str) -> str:
    """Texto efectivo para la búsqueda híbrida normal: la query corregida COMPLETA
    (con color/material incluidos). La usan los llamadores sin auto-filtros (chat,
    re-consultas de facetas), que necesitan que el color siga en el texto."""
    return analyze_query(q)["keywords"]


def embed_query(text: str) -> list[float]:
    aoai, _ = _clients()
    resp = aoai.embeddings.create(model=EMBED_DEPLOYMENT, input=text,
                                  dimensions=EMBED_DIMENSIONS)
    return resp.data[0].embedding


# nº de caracteres del fragmento de cada chunk que se vuelca al log (0 = chunk completo)
_CHUNK_PREVIEW = int(os.getenv("AZURE_LOG_CHUNK_CHARS", "160")) or None
# nº de modelos del ranking final que se listan en el log (0 = todos)
_TOP_MODELS_LOG = int(os.getenv("AZURE_LOG_TOP_MODELS", "15"))


def search_models(q: str, k: int = 120, hybrid: bool = True,
                  q_eff: str | None = None) -> dict[str, float]:
    """Devuelve {model -> score} ordenado por relevancia (score Azure desc).

    Un modelo puede aparecer en varios chunks; nos quedamos con su mejor score.
    Lanza excepción si Azure falla (el llamador decide el fallback).

    q_eff: texto de búsqueda ya interpretado (lo pasa /search cuando analyze_query movió
    los atributos a filtros: se busca solo el producto). Si no llega, refine_query(q).

    SIN CACHÉ: cada llamada golpea Azure de nuevo (embed + búsqueda). Las facetas
    re-consultan /search con el mismo texto en cada clic, así que esto multiplica
    las llamadas a Azure; a cambio la búsqueda es siempre fresca y depurable."""
    _, search = _clients()
    # El LLM corrige erratas y extrae keywords; el texto resultante alimenta AMBAS patas
    # (se embebe -> pata vectorial; se busca -> pata keyword). Así 'lababo' -> 'lavabo'.
    q_eff = q_eff or refine_query(q)
    print(f"[azure] ── búsqueda q={q!r} q_eff={q_eff!r} k={k} hybrid={hybrid} índice={INDEX_NAME}",
          flush=True)
    t0 = time.perf_counter()
    vector = embed_query(q_eff)
    t_embed = time.perf_counter() - t0
    print(f"[azure] embed ok dims={len(vector)} deployment={EMBED_DEPLOYMENT} "
          f"({t_embed*1000:.0f}ms)", flush=True)
    vq = VectorizedQuery(vector=vector, k_nearest_neighbors=k, fields=VECTOR_FIELD)
    t1 = time.perf_counter()
    results = search.search(
        search_text=q_eff if hybrid else None,   # None = solo vectorial; texto = híbrido
        vector_queries=[vq],
        select=["parent_id", "title", "chunk"],
        top=k,
    )

    scores: dict[str, float] = {}
    n_chunks = 0
    print(f"[azure] q_eff={q_eff!r} chunks recuperados (rank | score | model | fragmento):", flush=True)
    for r in results:
        n_chunks += 1
        title = r.get("title")
        model = _model_from_title(title)
        s = r.get("@search.score") or 0.0
        # muestra CADA chunk que devuelve Azure: score, modelo y un fragmento del texto en una línea
        snippet = " ".join((r.get("chunk") or "").split())[:_CHUNK_PREVIEW]
        print(f"[azure]   #{n_chunks:>3} {s:6.3f}  {model or '??'}  {snippet!r}", flush=True)
        if not model:
            continue
        if s > scores.get(model, -1.0):       # mejor score por modelo
            scores[model] = s
    t_query = time.perf_counter() - t1

    if n_chunks == 0:
        print(f"[azure] ⚠ q_eff={q_eff!r} Azure NO devolvió chunks (0 resultados)", flush=True)
    # ranking final de modelos (mejor score por modelo), que es lo que consume /search
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ranked if _TOP_MODELS_LOG == 0 else ranked[:_TOP_MODELS_LOG]
    listing = ", ".join(f"{m}={s:.3f}" for m, s in top)
    print(f"[azure] ranking modelos (top {len(top)}/{len(ranked)}): {listing}", flush=True)
    print(f"[azure] ── resumen q={q!r} chunks={n_chunks} modelos={len(scores)} "
          f"embed={t_embed*1000:.0f}ms query={t_query*1000:.0f}ms", flush=True)
    return scores
