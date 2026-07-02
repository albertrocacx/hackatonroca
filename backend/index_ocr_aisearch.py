"""
Indexa en Azure AI Search los .pages.jsonl generados por ocr_pdf_blob.py, con el
SKU (nombre total del modelo) como metadato filtrable/facetable + vector para RAG.

Modelo "push": creamos el índice si no existe, calculamos el embedding de cada página
con Azure OpenAI (el MISMO deployment/claves que backend/azure_search.py) y subimos un
documento por página. Cada doc lleva su `sku`, así puedes filtrar/agrupar por modelo.

Config por variables de entorno (backend/.env):
  # Azure AI Search
  AZURE_SEARCH_ENDPOINT     https://<servicio>.search.windows.net
  AZURE_SEARCH_KEY          admin key (hace falta ADMIN para crear el índice y subir docs)
  OCR_INDEX_NAME            nombre del índice a crear/usar (def: ocr-catalogos)
  # Azure OpenAI (embeddings) — reutiliza las mismas de azure_search.py
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION
  AZURE_EMBED_DEPLOYMENT (def: text-embedding-3-large-2)
  AZURE_EMBED_DIMENSIONS (def: 3072)
  # Entrada
  OCR_OUT_DIR               carpeta con los *.pages.jsonl (def: backend/data/ocr)

Uso:
  python index_ocr_aisearch.py                     # indexa todos los *.pages.jsonl de OCR_OUT_DIR
  python index_ocr_aisearch.py data/ocr/5A208EC00.pages.jsonl ...   # solo esos ficheros

Dependencias:
  pip install azure-search-documents openai python-dotenv
"""
import os
import re
import sys
import glob
import json

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchField, SearchFieldDataType,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
)

# --- Config ---
SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME       = os.getenv("OCR_INDEX_NAME", "ocr-catalogos")

AOAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_API_KEY     = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBED_DEPLOYMENT", "text-embedding-3-large-2")
EMBED_DIMENSIONS = int(os.getenv("AZURE_EMBED_DIMENSIONS") or "3072")

OUT_DIR          = os.getenv("OCR_OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "ocr"
)

_aoai = None


def _embeddings_client() -> AzureOpenAI:
    global _aoai
    if _aoai is None:
        _aoai = AzureOpenAI(azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_API_KEY,
                            api_version=AOAI_API_VERSION)
    return _aoai


def embed(text: str) -> list[float]:
    resp = _embeddings_client().embeddings.create(
        model=EMBED_DEPLOYMENT, input=text, dimensions=EMBED_DIMENSIONS
    )
    return resp.data[0].embedding


def ensure_index() -> SearchIndexClient:
    """Crea el índice si no existe (idempotente)."""
    client = SearchIndexClient(SEARCH_ENDPOINT, AzureKeyCredential(SEARCH_API_KEY))
    existing = {i.name for i in client.list_indexes()}
    if INDEX_NAME in existing:
        return client

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        # SKU = carpeta del producto: buscable, filtrable, facetable y ordenable.
        SearchableField(name="sku", type=SearchFieldDataType.String,
                        filterable=True, facetable=True, sortable=True),
        # doctype = UserManual | InstallationManual | TechnicalFactSheet.
        SearchableField(name="doctype", type=SearchFieldDataType.String,
                        filterable=True, facetable=True),
        SimpleField(name="pdf_url", type=SearchFieldDataType.String),  # para enlaces clicables
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="page", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        SimpleField(name="total_pages", type=SearchFieldDataType.Int32),
        SearchableField(name="chunk", type=SearchFieldDataType.String,
                        analyzer_name="es.microsoft"),
        SearchField(
            name="text_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True, vector_search_dimensions=EMBED_DIMENSIONS,
            vector_search_profile_name="hnsw-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-algo")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-algo")],
    )
    client.create_index(SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vector_search))
    print(f"Índice '{INDEX_NAME}' creado.")
    return client


def _doc_id(sku: str, page: int) -> str:
    """Clave válida para Azure AI Search: solo letras, dígitos, _ - = ."""
    safe = re.sub(r"[^A-Za-z0-9_\-=]", "_", sku)
    return f"{safe}-p{page}"


def docs_from_jsonl(path: str):
    """Convierte cada línea del .pages.jsonl en un documento del índice (con embedding)."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            text = (r.get("text") or "").strip()
            if not text:
                continue  # no indexamos páginas en blanco o con error
            yield {
                # id único por documento+página (varios doctypes comparten sku/carpeta).
                "id": _doc_id(r.get("document") or r["sku"], r["page"]),
                "sku": r["sku"],
                "doctype": r.get("doctype", ""),
                "pdf_url": r.get("pdf_url", ""),
                "source": r.get("source", ""),
                "page": r["page"],
                "total_pages": r.get("total_pages", 0),
                "chunk": text,
                "text_vector": embed(text),
            }


def main(argv: list[str]) -> int:
    if not (SEARCH_ENDPOINT and SEARCH_API_KEY):
        print("Faltan AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY (admin) en el entorno o backend/.env")
        return 1

    files = argv[1:] if len(argv) > 1 else sorted(
        glob.glob(os.path.join(OUT_DIR, "**", "*.pages.jsonl"), recursive=True)
    )
    if not files:
        print(f"No hay *.pages.jsonl en '{OUT_DIR}'. Ejecuta antes ocr_pdf_blob.py.")
        return 1

    ensure_index()
    search = SearchClient(SEARCH_ENDPOINT, INDEX_NAME, AzureKeyCredential(SEARCH_API_KEY))

    total = 0
    for path in files:
        batch = list(docs_from_jsonl(path))
        if not batch:
            print(f"- {os.path.basename(path)}: 0 páginas útiles")
            continue
        # subimos en lotes de 100 (límite práctico de mergeOrUpload)
        for i in range(0, len(batch), 100):
            search.upload_documents(documents=batch[i:i + 100])
        total += len(batch)
        print(f"- {os.path.basename(path)}: {len(batch)} páginas indexadas")

    print(f"\nHecho. {total} documentos (páginas) indexados en '{INDEX_NAME}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
