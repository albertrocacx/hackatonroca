"""
OCR de manuales ROCA alojados en Azure Blob Storage con el LLM de Azure AI Foundry
(cliente OpenAI v1 + Responses API), página a página, con metadatos para RAG.

Estructura esperada en el contenedor (p.ej. 'data'):
    products/products_documents/<CARPETA_PRODUCTO>/<varios>.pdf

De cada carpeta procesa SOLO los PDFs cuyo nombre contenga uno de estos tipos:
    UserManual | InstallationManual | TechnicalFactSheet
(puede haber 0, 1 o varios; si no hay ninguno, la carpeta se ignora).

Por cada PDF que procesa:
  1. Lo descarga.
  2. Renderiza cada página a imagen PNG (PyMuPDF).
  3. OCR + descripción paso a paso con el modelo de visión (Responses API).
  4. Vuelca en local un .md (con metadatos) + un .jsonl (un chunk por página) y SUBE el
     .md a la MISMA RUTA/carpeta del PDF en el contenedor, con METADATOS de blob:
        sku       = nombre de la carpeta del producto (agrupa sus documentos)
        doctype   = UserManual | InstallationManual | TechnicalFactSheet
        pdf_url   = URL https del PDF original (para enlaces clicables en el chat)

Config por variables de entorno (backend/.env). NO metas la API key en el código.
  # --- Blob Storage (origen y destino del .md) ---
  AZURE_STORAGE_CONNECTION_STRING   cadena de conexión de la cuenta de almacenamiento
  OCR_SOURCE_CONTAINER              contenedor (def: data)
  OCR_SOURCE_PREFIX                 prefijo donde están las carpetas (def: products/products_documents/)
  OCR_SKIP_EXISTING                 "1" (def) salta PDFs cuyo .md ya existe en el blob; "0" reprocesa
  # --- LLM (Azure AI Foundry, cliente OpenAI v1) ---
  FOUNDRY_ENDPOINT / FOUNDRY_API_KEY / FOUNDRY_DEPLOYMENT (def: gpt-5.4)
  OCR_DPI (def 300) / OCR_MAX_TOKENS (def 6000) / OCR_OUT_DIR (def backend/data/ocr)
  OCR_MAX_PAGES                     descarta PDFs con más páginas (def 5; 0 = sin límite)

Uso:
  python ocr_pdf_blob.py                          # recorre TODAS las carpetas del prefijo
  python ocr_pdf_blob.py products/products_documents/212106..1/  # solo esa carpeta (prefijo)
  python ocr_pdf_blob.py ruta/exacta/a/un.pdf     # un PDF concreto (blob)

Dependencias: pip install azure-storage-blob openai pymupdf python-dotenv
"""
import os
import re
import sys
import json
import base64
import posixpath
from urllib.parse import quote

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import fitz  # PyMuPDF: renderizado de páginas de PDF a imagen
from openai import OpenAI
from azure.storage.blob import BlobServiceClient

# --- Configuración: TODO viene de entorno / .env (sin secretos en el código) ---
CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
SOURCE_CONTAINER  = os.getenv("OCR_SOURCE_CONTAINER", "data")
SOURCE_PREFIX     = os.getenv("OCR_SOURCE_PREFIX", "products/products_documents/")
SKIP_EXISTING     = os.getenv("OCR_SKIP_EXISTING", "1") != "0"

FOUNDRY_ENDPOINT  = os.getenv(
    "FOUNDRY_ENDPOINT",
    "https://aihackathonfoundry.services.ai.azure.com/openai/v1",
)
FOUNDRY_API_KEY   = os.getenv("FOUNDRY_API_KEY") or os.getenv("OPENAI_API_KEY", "")
DEPLOYMENT        = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-5.4")

DPI               = int(os.getenv("OCR_DPI") or "300")
MAX_TOKENS        = int(os.getenv("OCR_MAX_TOKENS") or "6000")
MAX_PAGES         = int(os.getenv("OCR_MAX_PAGES") or "5")  # descarta PDFs más largos (0 = sin límite)
OUT_DIR           = os.getenv("OCR_OUT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "ocr"
)

# Tipos de documento que nos interesan. La clave normalizada (sin separadores, en
# minúsculas) se busca dentro del nombre del fichero; el valor es la etiqueta canónica.
DOCTYPES = {
    "usermanual": "UserManual",
    "installationmanual": "InstallationManual",
    "technicalfactsheet": "TechnicalFactSheet",
}

# Instrucción de OCR + descripción (redactor técnico de manuales de instalación ROCA).
SYSTEM_PROMPT = (
    "Eres un redactor técnico experto en manuales de instalación de ROCA (sanitarios: "
    "lavabos, inodoros, bañeras, platos de ducha, griferías y muebles de baño). Conviertes "
    "la imagen de UNA página de manual —normalmente pictogramas, planos acotados y símbolos, "
    "con poco o ningún texto— en contenido claro y accionable en Markdown, en español, para "
    "indexarlo en un buscador semántico (RAG) que ayudará a instaladores y usuarios.\n\n"
    "Interpreta los pictogramas como acciones e información concreta:\n"
    "- Números junto a cotas = medidas en mm (respeta la unidad si aparece).\n"
    "- Icono de broca/taladro = diámetro de broca (p.ej. «broca Ø12 mm»).\n"
    "- Número dentro de una llave = llave fija del nº indicado (p.ej. «llave del nº17»).\n"
    "- Símbolo de par de apriete/Nm = par de apriete (p.ej. «máx. 5 Nm»).\n"
    "- Ángulos (p.ej. 96°), alturas Min./Max. y cantidades «x4» = repeticiones/unidades.\n"
    "- Iconos de materiales/herramientas: silicona, cemento blanco (WHITE CEMENT), jabón "
    "(SOAP), nivel de burbuja, destornillador, taladro, cinta métrica.\n"
    "- Triángulo de advertencia o icono tachado (X) = conviértelo en una línea «Precaución: …».\n"
    "- Códigos alfanuméricos de referencia (p.ej. A506403900) = transcríbelos EXACTOS.\n\n"
    "FIDELIDAD ANTE TODO: describe SOLO lo que se ve en la página. No inventes medidas, "
    "referencias, pares de apriete, herramientas ni pasos que no aparezcan. Si un número o "
    "texto no es legible, escribe «(no legible)» en su lugar. Sin preámbulos ni comentarios."
)

USER_PROMPT_TMPL = (
    "Página {page} de {total} del documento «{name}» (manual de instalación ROCA).\n\n"
    "Devuelve el contenido de ESTA página en Markdown, con la sección que corresponda:\n"
    "- Si muestra el despiece o el conjunto de piezas: «## Componentes» con una lista de cada "
    "pieza y su cantidad (p.ej. «- Espárrago roscado ×4»).\n"
    "- Si muestra pasos de montaje/instalación: «## Instrucciones de instalación (paso a paso)» "
    "y redacta cada paso como una frase completa y accionable, conservando la numeración del "
    "manual (1, 2, 5a, 6b…). Incluye en cada paso, cuando aparezcan: la acción, la herramienta, "
    "la medida/diámetro/par, la cantidad y las precauciones. Ejemplo de estilo: «Paso 5: Colocar "
    "el lavabo sobre los espárragos y apretar las tuercas con llave del nº17 sin forzar "
    "(precaución: no apretar en exceso, riesgo de fractura de la porcelana).»\n"
    "- Transcribe además cualquier texto real (títulos, referencias, tablas de cotas en formato "
    "Markdown, datos del fabricante, teléfonos).\n"
    "Si la página está en blanco responde exactamente «[página en blanco]». No añadas nada fuera "
    "del contenido de la página."
)

# --- Clientes (perezosos) ---
_llm = None


def _client() -> OpenAI:
    global _llm
    if _llm is None:
        if not FOUNDRY_API_KEY:
            raise RuntimeError("Falta FOUNDRY_API_KEY (o OPENAI_API_KEY) en el entorno o backend/.env")
        _llm = OpenAI(base_url=FOUNDRY_ENDPOINT, api_key=FOUNDRY_API_KEY)
    return _llm


def _account_name() -> str:
    """Extrae AccountName de la cadena de conexión (para construir la URL del PDF)."""
    m = re.search(r"AccountName=([^;]+)", CONNECTION_STRING)
    return (m.group(1) if m else os.getenv("AZURE_STORAGE_ACCOUNT", "")).strip()


def blob_url(container: str, blob_name: str) -> str:
    """URL https del blob (clicable). El contenedor es privado: para abrirlo hará falta SAS/permiso."""
    acct = _account_name()
    return f"https://{acct}.blob.core.windows.net/{container}/{quote(blob_name, safe='/')}"


def get_container_client(container_name: str):
    if not CONNECTION_STRING:
        raise RuntimeError("Falta AZURE_STORAGE_CONNECTION_STRING en el entorno o backend/.env")
    service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    return service.get_container_client(container_name)


def detect_doctype(blob_name: str):
    """Devuelve la etiqueta canónica (UserManual/…) si el nombre contiene uno de los tipos, o None."""
    norm = re.sub(r"[^a-z0-9]", "", os.path.basename(blob_name).lower())
    for key, label in DOCTYPES.items():
        if key in norm:
            return label
    return None


def sku_from_blob(blob_name: str) -> str:
    """SKU = nombre de la carpeta que contiene el PDF (agrupa los documentos del producto).
    p.ej. 'products/products_documents/212106..1/x.pdf' -> '212106..1'. Ajusta si tu convención difiere."""
    parent = posixpath.dirname(blob_name)
    return posixpath.basename(parent) if parent else os.path.splitext(os.path.basename(blob_name))[0]


def list_target_pdfs(container) -> list[str]:
    """PDFs bajo SOURCE_PREFIX cuyo nombre contiene uno de los doctypes de interés."""
    out = []
    for b in container.list_blobs(name_starts_with=SOURCE_PREFIX):
        if b.name.lower().endswith(".pdf") and detect_doctype(b.name):
            out.append(b.name)
    return out


def expand_args(container, args: list[str]) -> list[str]:
    """Cada argumento puede ser un PDF concreto o un prefijo/carpeta (termina en '/' o no es .pdf)."""
    blobs = []
    for a in args:
        if a.lower().endswith(".pdf"):
            blobs.append(a)
        else:  # tratar como prefijo/carpeta
            pref = a if a.endswith("/") else a + "/"
            blobs += [b.name for b in container.list_blobs(name_starts_with=pref)
                      if b.name.lower().endswith(".pdf") and detect_doctype(b.name)]
    return blobs


def render_pages(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = DPI / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix)
            yield i, len(doc), pix.tobytes("png")
    finally:
        doc.close()


def ocr_page(name: str, page: int, total: int, png_bytes: bytes) -> str:
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    resp = _client().responses.create(
        model=DEPLOYMENT,
        max_output_tokens=MAX_TOKENS,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "input_text", "text": USER_PROMPT_TMPL.format(page=page, total=total, name=name)},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            ]},
        ],
    )
    return (resp.output_text or "").strip()


def process_blob(container, blob_name: str) -> str | None:
    """OCR de un PDF -> .md + .jsonl locales + .md subido a la misma carpeta con metadatos."""
    doctype = detect_doctype(blob_name) or "Unknown"
    sku = sku_from_blob(blob_name)
    base = os.path.splitext(os.path.basename(blob_name))[0]
    folder = posixpath.dirname(blob_name)
    dest_blob = posixpath.join(folder, f"{base}.md") if folder else f"{base}.md"
    pdf_url = blob_url(SOURCE_CONTAINER, blob_name)

    # Metadatos comunes (blob + frontmatter + cada chunk del jsonl).
    meta = {"sku": sku, "doctype": doctype, "pdf_url": pdf_url,
            "source": f"{SOURCE_CONTAINER}/{blob_name}"}

    if SKIP_EXISTING and container.get_blob_client(dest_blob).exists():
        print(f"      (salta: ya existe {dest_blob})")
        return None

    pdf_bytes = container.download_blob(blob_name).readall()

    # Descarta PDFs largos (por defecto > 5 páginas): no interesan y disparan el coste del OCR.
    _doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_pages = _doc.page_count
    _doc.close()
    if MAX_PAGES and n_pages > MAX_PAGES:
        print(f"      (salta: {n_pages} páginas > máx {MAX_PAGES})")
        return None

    # Salida local, replicando la carpeta del blob para evitar colisiones de nombre.
    local_dir = os.path.join(OUT_DIR, *folder.split("/")) if folder else OUT_DIR
    os.makedirs(local_dir, exist_ok=True)
    md_path = os.path.join(local_dir, f"{base}.md")
    jsonl_path = os.path.join(local_dir, f"{base}.pages.jsonl")

    md_parts = [
        "---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n",
        f"# {base}\n",
    ]
    with open(jsonl_path, "w", encoding="utf-8") as jsonl:
        for page, total, png in render_pages(pdf_bytes):
            try:
                text = ocr_page(blob_name, page, total, png)
                err = None
            except Exception as e:
                text, err = "", str(e)
                print(f"      ! página {page}/{total}: ERROR {e}")

            md_parts.append(f"\n## Página {page}\n\n{text if text else '_[sin contenido]_'}\n")
            record = {**meta, "document": base, "page": page, "total_pages": total,
                      "model": DEPLOYMENT, "text": text}
            if err:
                record["error"] = err
            jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
            if not err:
                print(f"      página {page}/{total} OK ({len(text)} car.)")

    md = "\n".join(md_parts)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # La metadata de blob debe ser ASCII (Azure rechaza acentos/espacios, p.ej. en `source`):
    # subimos solo sku/doctype/pdf_url (todos ASCII). `source` queda en el frontmatter y el jsonl.
    blob_meta = {k: meta[k].encode("ascii", "ignore").decode("ascii")
                 for k in ("sku", "doctype", "pdf_url")}
    container.upload_blob(name=dest_blob, data=md.encode("utf-8"), overwrite=True, metadata=blob_meta)
    print(f"      subido -> {SOURCE_CONTAINER}/{dest_blob}  [sku={sku} doctype={doctype}]")
    return md_path


def main(argv: list[str]) -> int:
    container = get_container_client(SOURCE_CONTAINER)
    blobs = expand_args(container, argv[1:]) if len(argv) > 1 else list_target_pdfs(container)
    if not blobs:
        print(f"No hay PDFs de interés en '{SOURCE_CONTAINER}/{SOURCE_PREFIX}'.")
        return 1

    print(f"LLM: {DEPLOYMENT} @ {FOUNDRY_ENDPOINT}")
    print(f"{len(blobs)} PDF(s) a procesar (UserManual/InstallationManual/TechnicalFactSheet).")
    print(f"Copia local en: {OUT_DIR}\n")

    ok, saltados, errores = 0, 0, 0
    for i, blob_name in enumerate(blobs, 1):
        print(f"[{i}/{len(blobs)}] {blob_name}")
        try:
            res = process_blob(container, blob_name)
            if res is None:
                saltados += 1
            else:
                ok += 1
                print(f"      local -> {res}")
        except Exception as e:
            errores += 1
            print(f"      ERROR {blob_name}: {e}")

    print(f"\nHecho. {ok} procesados, {saltados} saltados (ya existían), {errores} con error.")
    return 0 if errores == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
