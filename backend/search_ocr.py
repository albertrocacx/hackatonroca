"""
Búsqueda híbrida (texto + vector) con FILTRADO por metadatos sobre el índice de
manuales OCR en Azure AI Search.

Mismo patrón que backend/azure_search.py (embeddings de Azure OpenAI + SearchClient con
VectorizedQuery), pero añadiendo filtros OData por `sku` y `doctype`, y devolviendo la
`pdf_url` de cada resultado para poder pintar enlaces clicables en el chat.

Config (backend/.env):
  AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY
  AZURE_SEARCH_OCR_INDEX     nombre del índice creado en la web (p.ej. manuales-index)
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION
  AZURE_EMBED_DEPLOYMENT (def text-embedding-3-large-2), AZURE_EMBED_DIMENSIONS (def 3072)
  # Nombres de campos del índice (ajústalos a los que cree el asistente web):
  OCR_CONTENT_FIELD (def chunk), OCR_VECTOR_FIELD (def text_vector),
  OCR_SKU_FIELD (def sku), OCR_DOCTYPE_FIELD (def doctype), OCR_URL_FIELD (def pdf_url)

Uso:
  python search_ocr.py "cómo regular las patas de la bañera"
  python search_ocr.py "medidas de instalación" --doctype InstallationManual
  python search_ocr.py "desagüe" --sku 212106..1 --top 5
  python search_ocr.py "montaje" --doctype UserManual,InstallationManual   # varios valores

Como módulo:
  from search_ocr import search_ocr
  search_ocr("desagüe", sku="212106..1", doctype=["InstallationManual"], top=5)

Dependencias: pip install azure-search-documents openai python-dotenv
"""
import os
import re
import sys
import argparse
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, unquote

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from openai import AzureOpenAI, OpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import generate_blob_sas, BlobSasPermissions

# --- Config ---
SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME       = os.getenv("AZURE_SEARCH_OCR_INDEX", "manuales-index")

AOAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_API_KEY     = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBED_DEPLOYMENT", "text-embedding-3-large-2")
EMBED_DIMENSIONS = int(os.getenv("AZURE_EMBED_DIMENSIONS") or "3072")

# LLM para el modo --chat (mismo Foundry que el OCR).
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "https://aihackathonfoundry.services.ai.azure.com/openai/v1")
FOUNDRY_API_KEY  = os.getenv("FOUNDRY_API_KEY") or os.getenv("OPENAI_API_KEY", "")
CHAT_DEPLOYMENT  = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-5.4")

# Storage: para firmar los pdf_url con SAS (leer un blob de un contenedor privado).
STORAGE_CONN  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
SAS_TTL_HOURS = int(os.getenv("OCR_SAS_TTL_HOURS") or "2")


def _storage_creds() -> tuple[str, str]:
    name = re.search(r"AccountName=([^;]+)", STORAGE_CONN)
    key = re.search(r"AccountKey=([^;]+)", STORAGE_CONN)
    return (name.group(1) if name else "", key.group(1) if key else "")


def with_sas(pdf_url: str | None) -> str:
    """Añade un token SAS de solo-lectura y temporal a la URL del PDF para que abra desde el chat.
    Si no hay credenciales de storage, devuelve la URL sin firmar (no rompe)."""
    if not pdf_url:
        return pdf_url or ""
    acct, key = _storage_creds()
    if not (acct and key):
        return pdf_url
    try:
        parts = urlparse(pdf_url).path.lstrip("/").split("/", 1)
        if len(parts) != 2:
            return pdf_url
        container, blob = parts[0], unquote(parts[1])
        token = generate_blob_sas(
            account_name=acct, container_name=container, blob_name=blob, account_key=key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=SAS_TTL_HOURS),
        )
        return f"{pdf_url}?{token}"
    except Exception:
        return pdf_url

# Nombres de campos (ajusta a los que genere el asistente de la web).
CONTENT_FIELD = os.getenv("OCR_CONTENT_FIELD", "chunk")
VECTOR_FIELD  = os.getenv("OCR_VECTOR_FIELD", "text_vector")
SKU_FIELD     = os.getenv("OCR_SKU_FIELD", "sku")
DOCTYPE_FIELD = os.getenv("OCR_DOCTYPE_FIELD", "doctype")
URL_FIELD     = os.getenv("OCR_URL_FIELD", "pdf_url")
PAGE_FIELD    = os.getenv("OCR_PAGE_FIELD", "page")
SOURCE_FIELD  = os.getenv("OCR_SOURCE_FIELD", "source")
TOTAL_FIELD   = os.getenv("OCR_TOTAL_PAGES_FIELD", "total_pages")

# Tope de páginas al recuperar un documento completo (modo "dame el manual").
FULL_MAX_PAGES = int(os.getenv("OCR_FULL_MAX_PAGES") or "40")

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


def build_filter(sku: str | None = None, doctype=None) -> str | None:
    """Compone el filtro OData a partir de los metadatos. `doctype` admite str, lista o 'a,b'."""
    parts = []
    if sku:
        parts.append(f"{SKU_FIELD} eq {_odata_lit(sku)}")
    if doctype:
        vals = doctype.split(",") if isinstance(doctype, str) else list(doctype)
        vals = [v.strip() for v in vals if v.strip()]
        if len(vals) == 1:
            parts.append(f"{DOCTYPE_FIELD} eq {_odata_lit(vals[0])}")
        elif vals:
            # search.in(campo, 'a,b,c', ',') -> equivale a doctype in (a,b,c)
            joined = ",".join(vals)
            parts.append(f"search.in({DOCTYPE_FIELD}, {_odata_lit(joined)}, ',')")
    return " and ".join(parts) if parts else None


def search_ocr(query: str, sku: str | None = None, doctype=None, top: int = 5,
               hybrid: bool = True) -> list[dict]:
    """Búsqueda híbrida (texto+vector) con filtro por metadatos. Devuelve lista de dicts."""
    _, search = _clients()
    vq = VectorizedQuery(vector=_embed(query), k_nearest_neighbors=max(top, 50),
                         fields=VECTOR_FIELD)
    results = search.search(
        search_text=query if hybrid else None,
        vector_queries=[vq],
        filter=build_filter(sku, doctype),
        select=[CONTENT_FIELD, SKU_FIELD, DOCTYPE_FIELD, URL_FIELD],
        top=top,
    )
    out = []
    for r in results:
        out.append({
            "score": r.get("@search.score"),
            "sku": r.get(SKU_FIELD),
            "doctype": r.get(DOCTYPE_FIELD),
            "pdf_url": with_sas(r.get(URL_FIELD)),  # URL firmada (abre desde el chat)
            "text": r.get(CONTENT_FIELD) or "",
        })
    return out


def fetch_manual(sku: str, doctype=None, max_pages: int = FULL_MAX_PAGES) -> dict:
    """Recupera un documento COMPLETO (todas sus páginas, en orden) filtrando por
    sku+doctype, sin búsqueda vectorial. Una carpeta puede tener varios PDFs del mismo
    doctype (p. ej. versiones ES/EN): se devuelve una entrada por PDF con sus páginas.

    Devuelve {"documents": [{sku, doctype, source, pdf_url, total_pages, pages: [...]}, ...],
    "truncated": bool} (truncated=True si se alcanzó el tope global de páginas)."""
    _, search = _clients()
    results = search.search(
        search_text="*",
        filter=build_filter(sku, doctype),
        select=[CONTENT_FIELD, SKU_FIELD, DOCTYPE_FIELD, URL_FIELD,
                PAGE_FIELD, SOURCE_FIELD, TOTAL_FIELD],
        order_by=[f"{PAGE_FIELD} asc"],
        top=max_pages,
    )
    docs: dict[str, dict] = {}   # pdf_url -> documento agrupado
    n = 0
    for r in results:
        n += 1
        url = r.get(URL_FIELD) or ""
        d = docs.setdefault(url, {
            "sku": r.get(SKU_FIELD), "doctype": r.get(DOCTYPE_FIELD),
            "source": r.get(SOURCE_FIELD), "pdf_url": "",
            "total_pages": r.get(TOTAL_FIELD), "pages": [],
        })
        d["pages"].append({"page": r.get(PAGE_FIELD), "text": r.get(CONTENT_FIELD) or ""})
    for url, d in docs.items():
        d["pages"].sort(key=lambda p: p["page"] or 0)
        d["pdf_url"] = with_sas(url)   # URL firmada (abre desde el chat)
    return {"documents": list(docs.values()), "truncated": n >= max_pages}


def answer_ocr(query: str, hits: list[dict]) -> str:
    """RAG: redacta una respuesta a partir de los fragmentos recuperados, citando los enlaces."""
    if not hits:
        return "No he encontrado documentación relevante para esa consulta."
    contexto = "\n\n".join(
        f"[{i}] (sku={h['sku']}, doctype={h['doctype']}, url={h['pdf_url']})\n{h['text']}"
        for i, h in enumerate(hits, 1)
    )
    system = (
        "Eres el asistente de manuales de ROCA. Responde en español SOLO con la información "
        "de los fragmentos aportados. Sé claro y práctico (pasos si aplica). Cita las fuentes "
        "usadas como enlaces Markdown al final, con el formato [doctype – sku](url). Si la "
        "información no está en los fragmentos, dilo."
    )
    resp = _llm_client().responses.create(
        model=CHAT_DEPLOYMENT,
        max_output_tokens=1200,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Pregunta: {query}\n\nFragmentos:\n{contexto}"},
        ],
    )
    return (resp.output_text or "").strip()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Búsqueda con filtros de metadatos en AI Search.")
    p.add_argument("query", nargs="+", help="texto de la consulta")
    p.add_argument("--sku", help="filtrar por SKU (carpeta del producto)")
    p.add_argument("--doctype", help="UserManual | InstallationManual | TechnicalFactSheet (coma para varios)")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--chat", action="store_true", help="genera una respuesta RAG citando los enlaces")
    args = p.parse_args(argv[1:])

    if not (SEARCH_ENDPOINT and SEARCH_API_KEY):
        print("Faltan AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY en el entorno o backend/.env")
        return 1

    query = " ".join(args.query)
    print(f"Índice: {INDEX_NAME} | filtro: {build_filter(args.sku, args.doctype) or '(ninguno)'}\n")
    hits = search_ocr(query, sku=args.sku, doctype=args.doctype, top=args.top)
    if not hits:
        print("Sin resultados.")
        return 0

    if args.chat:
        print("=== Respuesta ===\n")
        print(answer_ocr(query, hits))
        print("\n=== Fuentes recuperadas ===")
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h["text"].split())[:300]
        print(f"[{i}] score={h['score']:.3f}  sku={h['sku']}  doctype={h['doctype']}")
        print(f"    {h['pdf_url']}")
        print(f"    {snippet}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
