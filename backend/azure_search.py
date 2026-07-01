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

# --- Clientes (perezosos: no bloquear el import ni el arranque) ---
_aoai = None
_search = None


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


def embed_query(text: str) -> list[float]:
    aoai, _ = _clients()
    resp = aoai.embeddings.create(model=EMBED_DEPLOYMENT, input=text,
                                  dimensions=EMBED_DIMENSIONS)
    return resp.data[0].embedding


# --- Caché LRU sencilla por (query, k): las facetas re-consultan /search con el mismo
#     texto en cada clic; sin caché eso golpearía Azure una vez por clic. ---
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 256


def search_models(q: str, k: int = 120, hybrid: bool = True) -> dict[str, float]:
    """Devuelve {model -> score} ordenado por relevancia (score Azure desc).

    Un modelo puede aparecer en varios chunks; nos quedamos con su mejor score.
    Lanza excepción si Azure falla (el llamador decide el fallback)."""
    key = (q, k, hybrid)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]

    _, search = _clients()
    vector = embed_query(q)
    vq = VectorizedQuery(vector=vector, k_nearest_neighbors=k, fields=VECTOR_FIELD)
    results = search.search(
        search_text=q if hybrid else None,   # None = solo vectorial; texto = híbrido
        vector_queries=[vq],
        select=["parent_id", "title", "chunk"],
        top=k,
    )

    scores: dict[str, float] = {}
    for r in results:
        model = _model_from_title(r.get("title"))
        if not model:
            continue
        s = r.get("@search.score") or 0.0
        if s > scores.get(model, -1.0):       # mejor score por modelo
            scores[model] = s

    _CACHE[key] = scores
    _CACHE.move_to_end(key)
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return scores
