"""
Búsqueda híbrida (texto + vector) sobre el índice de GUÍAS DE ESTILO de Roca (artículos
editoriales de RocaLife) en Azure AI Search.

Mismo patrón que backend/search_ocr.py (embeddings de Azure OpenAI + SearchClient con
VectorizedQuery), pero sobre el índice de artículos. Devuelve el título y la URL PÚBLICA
del artículo (roca.es) de cada resultado para citarlo como enlace clicable en el chat.
A diferencia de los manuales, aquí NO hace falta firmar con SAS: las URLs son páginas
web públicas.

Config (backend/.env):
  AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY   (la query key vale para buscar)
  AZURE_SEARCH_STYLE_INDEX   nombre del índice de guías (def: guias-estilo)
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION
  AZURE_EMBED_DEPLOYMENT (def text-embedding-3-large-2), AZURE_EMBED_DIMENSIONS (def 3072)
  # Nombres de campos (ajústalos si el índice usa otros):
  STYLE_CONTENT_FIELD (def chunk), STYLE_VECTOR_FIELD (def text_vector),
  STYLE_TITLE_FIELD (def title), STYLE_URL_FIELD (def url),
  STYLE_SLUG_FIELD (def slug), STYLE_KEYWORDS_FIELD (def keywords)

Uso:
  python search_style.py "ideas para un baño pequeño con ducha"
  python search_style.py "acabados en oro" --top 5
  python search_style.py "ahorrar agua" --chat          # respuesta RAG citando enlaces

Como módulo:
  from search_style import search_style
  search_style("baños en negro", top=5)

Dependencias: pip install azure-search-documents openai python-dotenv
"""
import os
import sys
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from openai import AzureOpenAI, OpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

# --- Config ---
SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME       = os.getenv("AZURE_SEARCH_STYLE_INDEX", "guias-estilo")

AOAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_API_KEY     = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBED_DEPLOYMENT", "text-embedding-3-large-2")
EMBED_DIMENSIONS = int(os.getenv("AZURE_EMBED_DIMENSIONS") or "3072")

# LLM para el modo --chat (mismo Foundry que el OCR).
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "https://aihackathonfoundry.services.ai.azure.com/openai/v1")
FOUNDRY_API_KEY  = os.getenv("FOUNDRY_API_KEY") or os.getenv("OPENAI_API_KEY", "")
CHAT_DEPLOYMENT  = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-5.4")

# Nombres de campos del índice (ajustables por si el índice se crea con otros nombres).
CONTENT_FIELD  = os.getenv("STYLE_CONTENT_FIELD", "chunk")
VECTOR_FIELD   = os.getenv("STYLE_VECTOR_FIELD", "text_vector")
TITLE_FIELD    = os.getenv("STYLE_TITLE_FIELD", "title")
URL_FIELD      = os.getenv("STYLE_URL_FIELD", "url")
SLUG_FIELD     = os.getenv("STYLE_SLUG_FIELD", "slug")
KEYWORDS_FIELD = os.getenv("STYLE_KEYWORDS_FIELD", "keywords")

_aoai = _search = _llm = None


def _clients():
    global _aoai, _search
    if _aoai is None:
        _aoai = AzureOpenAI(azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_API_KEY,
                            api_version=AOAI_API_VERSION)
    if _search is None:
        _search = SearchClient(SEARCH_ENDPOINT, INDEX_NAME, AzureKeyCredential(SEARCH_API_KEY))
    return _aoai, _search


def _llm_client() -> OpenAI:
    global _llm
    if _llm is None:
        _llm = OpenAI(base_url=FOUNDRY_ENDPOINT, api_key=FOUNDRY_API_KEY)
    return _llm


def _embed(text: str) -> list[float]:
    aoai, _ = _clients()
    return aoai.embeddings.create(model=EMBED_DEPLOYMENT, input=text,
                                  dimensions=EMBED_DIMENSIONS).data[0].embedding


def _odata_lit(value: str) -> str:
    """Escapa una cadena para un literal OData ('' escapa la comilla simple)."""
    return "'" + value.replace("'", "''") + "'"


def build_filter(slug: str | None = None, keywords=None) -> str | None:
    """Filtro OData opcional por `slug` (un artículo concreto) o por `keywords`
    (colección; admite str, lista o 'a,b'). Con keywords se exige que el artículo
    tenga AL MENOS una de las etiquetas dadas."""
    parts = []
    if slug:
        parts.append(f"{SLUG_FIELD} eq {_odata_lit(slug)}")
    if keywords:
        vals = keywords.split(",") if isinstance(keywords, str) else list(keywords)
        vals = [v.strip() for v in vals if v.strip()]
        if vals:
            joined = ",".join(vals)
            # keywords/any(k: search.in(k, 'a,b', ',')) -> el artículo tiene alguna etiqueta
            parts.append(f"{KEYWORDS_FIELD}/any(k: search.in(k, {_odata_lit(joined)}, ','))")
    return " and ".join(parts) if parts else None


def search_style(query: str, slug: str | None = None, keywords=None, top: int = 5,
                 hybrid: bool = True) -> list[dict]:
    """Búsqueda híbrida (texto+vector) sobre las guías de estilo. Devuelve lista de dicts
    con title, url (pública), slug, keywords y el fragmento de texto."""
    _, search = _clients()
    vq = VectorizedQuery(vector=_embed(query), k_nearest_neighbors=max(top, 50),
                         fields=VECTOR_FIELD)
    results = search.search(
        search_text=query if hybrid else None,
        vector_queries=[vq],
        filter=build_filter(slug, keywords),
        select=[CONTENT_FIELD, TITLE_FIELD, URL_FIELD, SLUG_FIELD, KEYWORDS_FIELD],
        top=top,
    )
    out = []
    for r in results:
        out.append({
            "score": r.get("@search.score"),
            "title": r.get(TITLE_FIELD),
            "url": r.get(URL_FIELD),
            "slug": r.get(SLUG_FIELD),
            "keywords": r.get(KEYWORDS_FIELD) or [],
            "text": r.get(CONTENT_FIELD) or "",
        })
    return out


def answer_style(query: str, hits: list[dict]) -> str:
    """RAG: redacta una respuesta con consejos de estilo a partir de los artículos, citando enlaces."""
    if not hits:
        return "No he encontrado guías de estilo relevantes para esa consulta."
    contexto = "\n\n".join(
        f"[{i}] (título={h['title']!r}, url={h['url']})\n{h['text']}"
        for i, h in enumerate(hits, 1)
    )
    system = (
        "Eres el asistente de estilo y decoración de ROCA. Respondes en español SOLO con la "
        "información de los artículos aportados (guías de estilo de RocaLife). Da consejos "
        "claros y prácticos. Cita los artículos usados como enlaces Markdown al final, con el "
        "formato [Título del artículo](url). Si la información no está en los fragmentos, dilo."
    )
    resp = _llm_client().responses.create(
        model=CHAT_DEPLOYMENT,
        max_output_tokens=1200,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Pregunta: {query}\n\nArtículos:\n{contexto}"},
        ],
    )
    return (resp.output_text or "").strip()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Búsqueda de guías de estilo en Azure AI Search.")
    p.add_argument("query", nargs="+", help="texto de la consulta")
    p.add_argument("--slug", help="filtrar por un artículo concreto (slug)")
    p.add_argument("--keywords", help="filtrar por keywords (coma para varias)")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--chat", action="store_true", help="genera una respuesta RAG citando los enlaces")
    args = p.parse_args(argv[1:])

    if not (SEARCH_ENDPOINT and SEARCH_API_KEY):
        print("Faltan AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY en el entorno o backend/.env")
        return 1

    query = " ".join(args.query)
    print(f"Índice: {INDEX_NAME} | filtro: {build_filter(args.slug, args.keywords) or '(ninguno)'}\n")
    hits = search_style(query, slug=args.slug, keywords=args.keywords, top=args.top)
    if not hits:
        print("Sin resultados.")
        return 0

    if args.chat:
        print("=== Respuesta ===\n")
        print(answer_style(query, hits))
        print("\n=== Artículos recuperados ===")
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h["text"].split())[:300]
        print(f"[{i}] score={h['score']:.3f}  {h['title']}")
        print(f"    {h['url']}")
        print(f"    {snippet}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
