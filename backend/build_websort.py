"""
Genera data/websort.json: { model -> websort } extraído del índice de Azure AI Search.

Websort es el orden de escaparate de roca.es (menor = antes). NO viene en el Excel de
productos: solo viaja DENTRO del texto de cada chunk del índice semántico (una línea
'Websort: <número>' por producto). Este script recorre el índice completo (paginación
determinista por chunk_id), parsea esa línea y resuelve el título de cada documento
('857853.md', modelo COMPACTADO sin los '..' comodín) al modelo canónico de
products.json — la misma resolución que hace main.resolve_azure_models en runtime.

Los modelos sin posición real llevan el centinela 20000000009999: se guarda tal cual
(en orden ascendente cae solo al final, igual que los modelos que no están en el índice).

Config: AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY / AZURE_SEARCH_INDEX (backend/.env).

Uso: python build_websort.py   ->   data/websort.json
"""
import json
import os
import re
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
API_KEY = os.getenv("AZURE_SEARCH_KEY", "")
INDEX = os.getenv("AZURE_SEARCH_INDEX", "rag-test4")

WEBSORT_RE = re.compile(r"^Websort:\s*(\d+)\s*$", re.M)


def main():
    if not ENDPOINT or not API_KEY:
        print("Faltan AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_KEY (copia .env.example a .env)")
        return 1
    with open(os.path.join(DATA, "products.json"), encoding="utf-8") as f:
        products = json.load(f)
    models = {p["model"] for p in products if p.get("model")}
    compact = {}
    for m in sorted(models):
        compact.setdefault(m.replace(".", ""), m)

    client = SearchClient(endpoint=ENDPOINT, index_name=INDEX,
                          credential=AzureKeyCredential(API_KEY))
    # order_by chunk_id: paginación estable. SIN top: en el SDK `top` limita el TOTAL
    # (no el tamaño de página); omitido, el iterador recorre el índice entero.
    results = client.search(search_text="*", select=["title", "chunk"],
                            order_by=["chunk_id asc"])

    websort, sin_websort, sin_modelo = {}, [], []
    n_docs = 0
    for doc in results:
        n_docs += 1
        title = doc.get("title") or ""
        model = title[:-3] if title.endswith(".md") else title
        m = WEBSORT_RE.search(doc.get("chunk") or "")
        if not m:
            sin_websort.append(model)
            continue
        value = int(m.group(1))
        canon = model if model in models else compact.get(model.replace(".", ""))
        if canon is None:
            sin_modelo.append(model)
            continue
        # un doc por modelo; si el índice trajera duplicados, gana la mejor posición
        if canon not in websort or value < websort[canon]:
            websort[canon] = value

    out = os.path.join(DATA, "websort.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(websort, f, ensure_ascii=False)
    print(f"websort.json: {len(websort)} modelos (docs={n_docs}, "
          f"sin Websort={len(sin_websort)}, sin modelo en catálogo={len(sin_modelo)})")
    if sin_modelo:
        print(f"  sin match en products.json (primeros 10): {sin_modelo[:10]}")
    print(f"Salida -> {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
