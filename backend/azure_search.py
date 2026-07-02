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
import os
import time
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


# =============================== Normalización de la query ===============================
# refine_query: un LLM (gpt-5.4) "por delante" que corrige erratas y extrae keywords
# ANTES de embeber y buscar, de modo que ambas patas del híbrido (vectorial + keyword)
# trabajan sobre el texto ya corregido ('lababo' -> 'lavabo'). Fail-open.

_REFINE_SYS = (
    "Eres un normalizador de consultas para un buscador de productos de baño y cocina "
    "(Roca). Corrige faltas de ortografía y extrae SOLO las palabras clave de búsqueda "
    "(producto, color, material, forma). Responde ÚNICAMENTE con las keywords corregidas "
    "separadas por espacios, en minúsculas, sin explicaciones ni puntuación."
)
_REFINE_CACHE: "OrderedDict[str, str]" = OrderedDict()


def _llm_client():
    global _llm
    if _llm is None:
        from openai import OpenAI  # cliente OpenAI apuntando al endpoint Foundry (/openai/v1)
        _llm = OpenAI(base_url=FOUNDRY_ENDPOINT, api_key=FOUNDRY_API_KEY)
    return _llm


def refine_query(q: str) -> str:
    """LLM por delante: corrige erratas + extrae keywords. Fail-open: si está desactivado
    o el LLM falla, devuelve la query original (nunca rompe la búsqueda)."""
    if not LLM_REFINE or not q.strip():
        return q
    if q in _REFINE_CACHE:
        _REFINE_CACHE.move_to_end(q)
        return _REFINE_CACHE[q]
    try:
        r = _llm_client().responses.create(
            model=CHAT_DEPLOYMENT, max_output_tokens=8000,
            input=[{"role": "system", "content": _REFINE_SYS},
                   {"role": "user", "content": q}],
        )
        refined = (r.output_text or "").strip() or q
    except Exception as e:  # noqa: BLE001 — LLM caído/timeout: seguimos con la query original
        print(f"[refine] LLM FALLO ({e!r}) -> query original {q!r}", flush=True)
        refined = q
    if refined != q:
        print(f"[refine] {q!r} -> {refined!r}", flush=True)
    _REFINE_CACHE[q] = refined
    _REFINE_CACHE.move_to_end(q)
    if len(_REFINE_CACHE) > _CACHE_MAX:
        _REFINE_CACHE.popitem(last=False)
    return refined


def embed_query(text: str) -> list[float]:
    aoai, _ = _clients()
    resp = aoai.embeddings.create(model=EMBED_DEPLOYMENT, input=text,
                                  dimensions=EMBED_DIMENSIONS)
    return resp.data[0].embedding


# --- Caché LRU sencilla por (query, k): las facetas re-consultan /search con el mismo
#     texto en cada clic; sin caché eso golpearía Azure una vez por clic. ---
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 256

# nº de caracteres del fragmento de cada chunk que se vuelca al log (0 = chunk completo)
_CHUNK_PREVIEW = int(os.getenv("AZURE_LOG_CHUNK_CHARS", "160")) or None


def search_models(q: str, k: int = 120, hybrid: bool = True) -> dict[str, float]:
    """Devuelve {model -> score} ordenado por relevancia (score Azure desc).

    Un modelo puede aparecer en varios chunks; nos quedamos con su mejor score.
    Lanza excepción si Azure falla (el llamador decide el fallback)."""
    key = (q, k, hybrid)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        print(f"[azure] q={q!r} CACHE HIT modelos={len(_CACHE[key])}", flush=True)
        return _CACHE[key]

    _, search = _clients()
    # El LLM corrige erratas y extrae keywords; el texto resultante alimenta AMBAS patas
    # (se embebe -> pata vectorial; se busca -> pata keyword). Así 'lababo' -> 'lavabo'.
    q_eff = refine_query(q)
    t0 = time.perf_counter()
    vector = embed_query(q_eff)
    t_embed = time.perf_counter() - t0
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
    print(f"[azure] q={q!r} chunks recuperados (rank | score | model | fragmento):", flush=True)
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
    print(f"[azure] q={q!r} q_eff={q_eff!r} k={k} hybrid={hybrid} chunks={n_chunks} "
          f"modelos={len(scores)} embed={t_embed*1000:.0f}ms query={t_query*1000:.0f}ms", flush=True)

    _CACHE[key] = scores
    _CACHE.move_to_end(key)
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return scores
