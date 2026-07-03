"""
Indexa en Azure AI Search las GUÍAS DE ESTILO de Roca (artículos editoriales de
RocaLife) que viven ya en Markdown en Blob Storage, con metadatos (título, keywords,
URL pública) + vector para RAG.

Es el MISMO patrón que index_ocr_aisearch.py (los manuales), pero SIN OCR: estos
documentos ya son Markdown (no PDFs escaneados), así que se leen directos del blob, se
trocean por secciones y se suben. Cada artículo produce varios documentos (chunks); todos
comparten `slug`/`url` para poder agrupar/citar.

Estructura esperada de cada .md (consistente en todo el contenedor):
    # <título>

    - **Fecha:** <ISO8601>
    - **Keywords:** <kw1, kw2, ...>          (en inglés; sirven de faceta)
    - **URL:** <https://www.roca.es/rocalife/...>   (pública y clicable)

    > <resumen>
    <cuerpo con secciones ## / ### y enlaces a productos>

Config por variables de entorno (backend/.env):
  # Blob Storage (origen de los artículos)
  AZURE_STORAGE_CONNECTION_STRING   cadena de conexión de la cuenta de almacenamiento
  STYLE_SOURCE_CONTAINER            contenedor con los .md (def: makrdown-articles)
  STYLE_SOURCE_PREFIX               prefijo opcional dentro del contenedor (def: "")
  # Azure AI Search
  AZURE_SEARCH_ENDPOINT             https://<servicio>.search.windows.net
  AZURE_SEARCH_ADMIN_KEY            admin key (crear índice + subir docs). Si falta, usa
                                    AZURE_SEARCH_KEY (que en runtime suele ser query-only).
  STYLE_INDEX_NAME                  índice a crear/usar (def: guias-estilo)
  # Azure OpenAI (embeddings) — reutiliza las mismas de azure_search.py / index_ocr
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION
  AZURE_EMBED_DEPLOYMENT (def: text-embedding-3-large-2)
  AZURE_EMBED_DIMENSIONS (def: 3072)
  # Troceado
  STYLE_CHUNK_CHARS (def 1400)      tamaño objetivo de cada chunk en caracteres
  STYLE_CHUNK_OVERLAP (def 200)     solape entre chunks contiguos

Uso:
  python index_style_aisearch.py                       # indexa TODO el contenedor
  python index_style_aisearch.py acabados-en-oro-en-bano.md ...   # solo esos blobs
  python index_style_aisearch.py --recreate            # borra y recrea el índice antes

Dependencias: pip install azure-search-documents azure-storage-blob openai python-dotenv
"""
import os
import re
import sys
import posixpath

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchField, SearchFieldDataType,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
)

# --- Config ---
CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
SOURCE_CONTAINER  = os.getenv("STYLE_SOURCE_CONTAINER", "makrdown-articles")
SOURCE_PREFIX     = os.getenv("STYLE_SOURCE_PREFIX", "")

SEARCH_ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT", "")
# admin para crear el índice/subir; cae a la key genérica si no hay una admin explícita.
SEARCH_API_KEY   = os.getenv("AZURE_SEARCH_ADMIN_KEY") or os.getenv("AZURE_SEARCH_KEY", "")
INDEX_NAME       = os.getenv("STYLE_INDEX_NAME", "guias-estilo")

AOAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_API_KEY     = os.getenv("AZURE_OPENAI_KEY", "")
AOAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBED_DEPLOYMENT", "text-embedding-3-large-2")
EMBED_DIMENSIONS = int(os.getenv("AZURE_EMBED_DIMENSIONS") or "3072")

CHUNK_CHARS   = int(os.getenv("STYLE_CHUNK_CHARS") or "1400")
CHUNK_OVERLAP = int(os.getenv("STYLE_CHUNK_OVERLAP") or "200")

_aoai = None


def _embeddings_client() -> AzureOpenAI:
    global _aoai
    if _aoai is None:
        _aoai = AzureOpenAI(azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_API_KEY,
                            api_version=AOAI_API_VERSION)
    return _aoai


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embeddings de varios textos en UNA llamada (el API acepta listas y conserva el
    orden; se reordena por .index por seguridad). Un artículo = una llamada."""
    resp = _embeddings_client().embeddings.create(
        model=EMBED_DEPLOYMENT, input=texts, dimensions=EMBED_DIMENSIONS
    )
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def get_container_client(container_name: str):
    if not CONNECTION_STRING:
        raise RuntimeError("Falta AZURE_STORAGE_CONNECTION_STRING en el entorno o backend/.env")
    service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    return service.get_container_client(container_name)


def ensure_index(recreate: bool = False) -> SearchIndexClient:
    """Crea el índice si no existe (idempotente). Con recreate=True lo borra antes."""
    client = SearchIndexClient(SEARCH_ENDPOINT, AzureKeyCredential(SEARCH_API_KEY))
    existing = {i.name for i in client.list_indexes()}
    if INDEX_NAME in existing and recreate:
        client.delete_index(INDEX_NAME)
        print(f"Índice '{INDEX_NAME}' borrado (--recreate).")
        existing.discard(INDEX_NAME)
    if INDEX_NAME in existing:
        return client

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        # slug = nombre del artículo (blob sin .md): agrupa sus chunks y permite filtrar.
        SearchableField(name="slug", type=SearchFieldDataType.String,
                        filterable=True, facetable=True, sortable=True),
        SearchableField(name="title", type=SearchFieldDataType.String,
                        analyzer_name="es.microsoft"),
        # keywords: colección (una entrada por etiqueta), facetable para navegar por tema.
        SearchField(name="keywords", type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    searchable=True, filterable=True, facetable=True),
        SimpleField(name="url", type=SearchFieldDataType.String),   # pública y clicable (roca.es)
        SimpleField(name="date", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, sortable=True),
        SimpleField(name="total_chunks", type=SearchFieldDataType.Int32),
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


# --- Parseo de un artículo Markdown -> (metadatos, cuerpo) ---
_RE_TITLE = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)
_RE_FECHA = re.compile(r"\*\*Fecha:\*\*\s*(.+)")
_RE_KEYWORDS = re.compile(r"\*\*Keywords:\*\*\s*(.+)")
_RE_URL = re.compile(r"\*\*URL:\*\*\s*(\S+)")


def parse_article(md: str, slug: str) -> dict:
    """Extrae título, fecha, keywords, URL y cuerpo (sin la cabecera de metadatos)."""
    title_m = _RE_TITLE.search(md)
    title = title_m.group(1).strip() if title_m else slug.replace("-", " ").strip()
    date = (m.group(1).strip() if (m := _RE_FECHA.search(md)) else "")
    url = (m.group(1).strip() if (m := _RE_URL.search(md)) else "")
    kw_raw = (m.group(1).strip() if (m := _RE_KEYWORDS.search(md)) else "")
    keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]

    # Cuerpo = todo menos las líneas de metadatos (Fecha/Keywords/URL) y el título #.
    body_lines = []
    for line in md.splitlines():
        s = line.strip()
        if title_m and s == f"# {title}":
            continue
        if _RE_FECHA.search(line) or _RE_KEYWORDS.search(line) or _RE_URL.search(line):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    return {"title": title, "date": date, "url": url, "keywords": keywords, "body": body}


def chunk_text(body: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Trocea respetando límites de párrafo cuando puede; si un párrafo excede `size`, lo
    corta con solape. Evita chunks minúsculos fusionando párrafos cortos consecutivos."""
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        return []
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > size:  # párrafo enorme: trocéalo por ventanas con solape
            if buf:
                chunks.append(buf)
                buf = ""
            start = 0
            while start < len(p):
                chunks.append(p[start:start + size])
                start += max(1, size - overlap)
            continue
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= size:
            buf += "\n\n" + p
        else:
            chunks.append(buf)
            # arranca el siguiente chunk con una cola del anterior (solape) para no perder contexto
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = (tail + "\n\n" + p).strip() if tail else p
    if buf:
        chunks.append(buf)
    return chunks


def _doc_id(slug: str, i: int) -> str:
    """Clave válida para Azure AI Search: solo letras, dígitos, _ - = ."""
    safe = re.sub(r"[^A-Za-z0-9_\-=]", "_", slug)
    return f"{safe}-c{i}"


def docs_from_blob(name: str, md: str):
    """Convierte un artículo .md en documentos del índice (uno por chunk). Los embeddings
    de todos los chunks del artículo se piden en UNA llamada (batch)."""
    slug = posixpath.splitext(posixpath.basename(name))[0]
    art = parse_article(md, slug)
    chunks = chunk_text(art["body"])
    if not chunks:
        return
    # Embebemos "título + fragmento": mantiene cada chunk en contexto aunque se recupere solo.
    vectors = embed_many([f"{art['title']}\n\n{c}" for c in chunks])
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        yield {
            "id": _doc_id(slug, i),
            "slug": slug,
            "title": art["title"],
            "keywords": art["keywords"],
            "url": art["url"],
            "date": art["date"],
            "source": f"{SOURCE_CONTAINER}/{name}",
            "chunk_index": i,
            "total_chunks": len(chunks),
            "chunk": chunk,
            "text_vector": vec,
        }


def list_target_blobs(container, args: list[str]) -> list[str]:
    """Blobs .md a indexar: los pasados como argumento, o todo el contenedor/prefijo."""
    explicit = [a for a in args if not a.startswith("-")]
    if explicit:
        return explicit
    return [b.name for b in container.list_blobs(name_starts_with=SOURCE_PREFIX)
            if b.name.lower().endswith(".md")]


def main(argv: list[str]) -> int:
    if not (SEARCH_ENDPOINT and SEARCH_API_KEY):
        print("Faltan AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_ADMIN_KEY (admin) en el entorno o backend/.env")
        return 1

    recreate = "--recreate" in argv[1:]
    container = get_container_client(SOURCE_CONTAINER)
    blobs = list_target_blobs(container, argv[1:])
    if not blobs:
        print(f"No hay .md en '{SOURCE_CONTAINER}/{SOURCE_PREFIX}'.")
        return 1

    print(f"Índice: {INDEX_NAME} @ {SEARCH_ENDPOINT}")
    print(f"Origen: {SOURCE_CONTAINER}/{SOURCE_PREFIX or ''}  ({len(blobs)} artículo(s))\n")
    ensure_index(recreate=recreate)
    search = SearchClient(SEARCH_ENDPOINT, INDEX_NAME, AzureKeyCredential(SEARCH_API_KEY))

    total_docs, total_arts, errores = 0, 0, 0
    batch: list[dict] = []
    for i, name in enumerate(blobs, 1):
        try:
            md = container.download_blob(name).readall().decode("utf-8")
        except ResourceNotFoundError:
            print(f"[{i}/{len(blobs)}] {name}: NO EXISTE, salta")
            errores += 1
            continue
        docs = list(docs_from_blob(name, md))
        if not docs:
            print(f"[{i}/{len(blobs)}] {name}: 0 chunks útiles")
            continue
        batch.extend(docs)
        total_docs += len(docs)
        total_arts += 1
        print(f"[{i}/{len(blobs)}] {name}: {len(docs)} chunks")
        # subimos en lotes de 100 documentos (límite práctico de mergeOrUpload)
        while len(batch) >= 100:
            search.upload_documents(documents=batch[:100])
            batch = batch[100:]
    if batch:
        search.upload_documents(documents=batch)

    print(f"\nHecho. {total_docs} chunks de {total_arts} artículos indexados en '{INDEX_NAME}'"
          f"{f' ({errores} con error)' if errores else ''}.")
    return 0 if errores == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
