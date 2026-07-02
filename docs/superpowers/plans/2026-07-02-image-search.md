# Búsqueda por Imagen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir búsqueda por foto (1–6 imágenes, drag & drop) a la barra del buscador: el backend consulta el endpoint DINOv2 deployado en Azure ML, fusiona rankings multi-foto y devuelve ModelCards que la parrilla existente pinta tal cual.

**Architecture:** Proxy en el backend (`POST /search/image`, multipart). Un módulo nuevo `backend/image_search.py` aísla el cliente HTTP del endpoint DINO y la fusión de rankings (mismo patrón que `azure_search.py`). `main.py` mapea SKU→modelo con sus índices en memoria y aplica el filtro de texto reutilizando `parse_query()`/`matches()`. El frontend añade icono de cámara, panel dropzone y chips de foto en la barra; los resultados van a la parrilla actual.

**Tech Stack:** FastAPI + requests (backend), React 18 + Vite + TS (frontend), pytest + fastapi TestClient (tests). Sin librerías nuevas de frontend.

**Spec:** `docs/superpowers/specs/2026-07-02-image-search-design.md`

## Global Constraints

- La `IMAGE_SEARCH_API_KEY` vive SOLO en `backend/.env` — nunca en el bundle JS ni en el repo.
- Feature opcional como todo en este proyecto: sin API key la app arranca y funciona; `/health` expone `image_ready: false` y el frontend oculta la cámara.
- Copy de UI en español.
- Los recambios (`is_spare_part`) SÍ entran en resultados de imagen (identificar un recambio por foto es el caso de uso principal; `/search` de texto los excluye, aquí no).
- El texto **filtra**, el score visual **ordena**. Sin re-ranking híbrido. Sin % visible en la UI.
- Response `mode=same` = mismo shape que `/search` (`facets: null`); el frontend reutiliza la parrilla sin adaptación.
- Comandos para Windows PowerShell (el venv del backend es `backend\.venv`).
- Endpoint DINO: `POST {"image_b64", "top_k"}` → `{"results": [{"sku","score","rank","image"}], "elapsed_ms"}`, auth `Bearer`. Referencia viva: `tools/test_dino_embedder.py`.

---

### Task 1: Módulo `backend/image_search.py` (cliente DINO + fusión)

**Files:**
- Create: `backend/image_search.py`
- Test: `backend/test_image_search.py`

**Interfaces:**
- Consumes: nada del proyecto (módulo hoja; solo env vars + requests).
- Produces (usado por Task 2):
  - `ready() -> bool` — hay API key.
  - `query_images(images: list[bytes], top_k: int = 50) -> list[dict[str, float]]` — un `{sku: score}` por foto, llamadas en paralelo. Lanza `EndpointError` (respuesta con `error`, p.ej. imagen corrupta) o excepciones de `requests` (endpoint caído/timeout).
  - `fuse_same(rankings: list[dict[str, float]]) -> dict[str, float]` — media sobre el total de fotos (ausente = 0).
  - `EndpointError(RuntimeError)`.

- [ ] **Step 1: Instalar pytest en el venv del backend (solo dev)**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pip install pytest
```

- [ ] **Step 2: Escribir el test de fusión (que falla)**

Crear `backend/test_image_search.py`:

```python
"""Pruebas de búsqueda por imagen: fusión multi-foto, /search/image y cruce con texto."""
import pytest

import image_search


def test_fuse_same_media_sobre_total_de_fotos():
    fused = image_search.fuse_same([{"A": 0.8, "B": 0.6}, {"A": 0.7}])
    assert fused["A"] == pytest.approx(0.75)   # (0.8 + 0.7) / 2
    assert fused["B"] == pytest.approx(0.30)   # (0.6 + 0.0) / 2 — ausente cuenta 0
    assert fused["A"] > fused["B"]


def test_fuse_same_una_foto_es_identidad():
    fused = image_search.fuse_same([{"A": 0.9, "B": 0.5}])
    assert fused == {"A": 0.9, "B": 0.5}
```

- [ ] **Step 3: Verificar que falla**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pytest test_image_search.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'image_search'`.

- [ ] **Step 4: Implementar el módulo**

Crear `backend/image_search.py`:

```python
"""
Cliente del endpoint DINOv2 (Azure ML Online Endpoint) para búsqueda por imagen.

Aísla la dependencia del servicio visual (mismo patrón que azure_search.py).
Dada una foto, el endpoint devuelve los SKUs del catálogo visualmente más
parecidos con su similitud coseno. Expone:

  ready()                    -> bool: hay API key configurada
  query_images([bytes], k)   -> [{sku: score}] — una llamada por foto, en paralelo
  fuse_same([rankings])      -> {sku: score} — fusión multi-foto (mismo producto)

Config (backend/.env): IMAGE_SEARCH_API_KEY (obligatoria), IMAGE_SEARCH_SCORING_URI.
"""
import base64
import os
from concurrent.futures import ThreadPoolExecutor

# Carga backend/.env con ruta absoluta (independiente del cwd). load_dotenv no pisa
# variables ya definidas: en producción mandan las del panel (Railway).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import requests

SCORING_URI = os.getenv(
    "IMAGE_SEARCH_SCORING_URI",
    "https://dino-embedder-roca.spaincentral.inference.ml.azure.com/score",
)
API_KEY = os.getenv("IMAGE_SEARCH_API_KEY", "")
TIMEOUT_S = 60


class EndpointError(RuntimeError):
    """El endpoint respondió pero con un error propio (p.ej. imagen ilegible)."""


def ready() -> bool:
    return bool(API_KEY)


def query_image(image_bytes: bytes, top_k: int = 50) -> dict[str, float]:
    """Una foto -> {sku: score coseno}. Lanza EndpointError o excepciones de requests."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    r = requests.post(
        SCORING_URI,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"image_b64": b64, "top_k": top_k},
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise EndpointError(data["error"])
    return {x["sku"]: float(x["score"]) for x in data.get("results", [])}


def query_images(images: list[bytes], top_k: int = 50) -> list[dict[str, float]]:
    """Una llamada por foto, en paralelo (máx. 6 fotos: concurrencia trivial)."""
    with ThreadPoolExecutor(max_workers=max(1, len(images))) as ex:
        return list(ex.map(lambda b: query_image(b, top_k), images))


def fuse_same(rankings: list[dict[str, float]]) -> dict[str, float]:
    """Fusión multi-foto (mismo producto): media del score sobre el TOTAL de fotos,
    contando 0 cuando el SKU no aparece en el top-k de una foto. Premia SKUs
    consistentes entre ángulos y hunde los matches espurios de una sola foto."""
    n = len(rankings)
    acc: dict[str, float] = {}
    for rk in rankings:
        for sku, s in rk.items():
            acc[sku] = acc.get(sku, 0.0) + s
    return {sku: s / n for sku, s in acc.items()}
```

- [ ] **Step 5: Verificar que pasa**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pytest test_image_search.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add backend/image_search.py backend/test_image_search.py; git commit -m "feat(backend): cliente DINOv2 + fusion multi-foto para busqueda por imagen"
```

---

### Task 2: Endpoint `POST /search/image` en `main.py`

**Files:**
- Modify: `backend/requirements.txt` (añadir `python-multipart`, `requests` explícito)
- Modify: `backend/main.py` (import guardado, endpoint, `image_ready` en `/health`)
- Modify: `backend/.env.example` (documentar las 2 variables nuevas)
- Test: `backend/test_image_search.py` (añadir tests de endpoint con TestClient)

**Interfaces:**
- Consumes (de Task 1): `image_search.ready()`, `image_search.query_images(blobs, top_k=50)`, `image_search.fuse_same(rankings)`, `image_search.EndpointError`.
- Consumes (ya existen en `main.py`): `parse_query(q)` (devuelve `{"filters": [...], "intent_phrase": str}`), `matches(p, sel)` (sel con claves `categories/collections/finishes` y tuplas `price/length/width/height`), `BY_SKU`, `BY_MODEL`, `SEARCH_INDEX`, `variant_summary(p)`.
- Produces (usado por Tasks 4–6):
  - `POST /search/image` multipart: `images` (1–6 ficheros), `q` (Form, default `""`), `mode` (Form, `"same"`|`"distinct"`, default `"same"`).
  - `mode=same` → `{"query": str, "total": int, "results": ModelCard[], "facets": null}`.
  - `mode=distinct` → `{"query": str, "mode": "distinct", "groups": [{"photo": int, "total": int, "results": ModelCard[]}]}` (photo = 1-based, mismo orden que las fotos subidas).
  - Errores: `503` sin key, `400` validación/foto ilegible, `502` DINO caído. Cuerpo FastAPI estándar `{"detail": str}`.
  - `GET /health` incluye `"image_ready": bool`.

- [ ] **Step 1: Añadir dependencias**

En `backend/requirements.txt` añadir al final:

```
python-multipart>=0.0.9    # subida multipart de fotos en /search/image
requests>=2.31             # cliente del endpoint DINO (image_search.py)
```

Instalar:

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- [ ] **Step 2: Escribir los tests de endpoint (que fallan)**

Añadir al final de `backend/test_image_search.py`:

```python
# ---------------------------------------------------------------- /search/image
import io

import requests as requests_lib
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _two_models():
    """Dos (modelo, sku, producto) de modelos distintos del catálogo real."""
    out, seen = [], set()
    for p in main.PRODUCTS:
        m = p.get("model")
        if m and m not in seen:
            seen.add(m)
            out.append((m, p["sku"], p))
        if len(out) == 2:
            return out
    raise RuntimeError("catálogo sin 2 modelos")


def _post(monkeypatch, rankings, n_photos=1, q="", mode="same"):
    monkeypatch.setattr(image_search, "ready", lambda: True)
    monkeypatch.setattr(image_search, "query_images", lambda blobs, top_k=50: rankings)
    files = [("images", (f"f{i}.jpg", io.BytesIO(b"xx"), "image/jpeg"))
             for i in range(n_photos)]
    return client.post("/search/image", files=files, data={"q": q, "mode": mode})


def test_same_ordena_por_score_y_default_es_mejor_sku(monkeypatch):
    (m1, sku1, _), (m2, sku2, _) = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9, sku2: 0.5}, {sku1: 0.8, sku2: 0.7}], n_photos=2)
    assert r.status_code == 200
    body = r.json()
    assert body["facets"] is None
    models = [c["model"] for c in body["results"]]
    assert models[:2] == [m1, m2]                      # 0.85 > 0.6
    card = body["results"][0]
    assert card["variants"][card["default"]]["sku"] == sku1


def test_distinct_un_ranking_por_foto(monkeypatch):
    (m1, sku1, _), (m2, sku2, _) = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9}, {sku2: 0.8}], n_photos=2, mode="distinct")
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert [g["photo"] for g in groups] == [1, 2]
    assert groups[0]["results"][0]["model"] == m1
    assert groups[1]["results"][0]["model"] == m2


def _pair_con_token_distintivo():
    """(sku1, sku2, token): token aparece en el blob de sku2 y NO en el de sku1."""
    pairs = _two_models()
    for _, sku2, p2 in [pairs[1], pairs[0]]:
        other = pairs[0] if sku2 == pairs[1][1] else pairs[1]
        blob_other = main.SEARCH_INDEX[other[1]]
        for tok in (p2.get("title") or "").lower().split():
            if len(tok) >= 4 and tok not in blob_other:
                return other[1], sku2, tok
    pytest.skip("no hay token distintivo entre los dos primeros modelos")


def test_texto_filtra_candidatos_sin_reordenar(monkeypatch):
    sku1, sku2, token = _pair_con_token_distintivo()
    m2 = main.BY_SKU[sku2]["model"]
    r = _post(monkeypatch, [{sku1: 0.9, sku2: 0.5}], q=token)
    models = [c["model"] for c in r.json()["results"]]
    assert m2 in models
    assert main.BY_SKU[sku1]["model"] not in models


def test_texto_sin_matches_devuelve_cero(monkeypatch):
    (_, sku1, _), _ = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9}], q="zzzznoexiste")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_sin_api_key_503(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: False)
    files = [("images", ("f.jpg", io.BytesIO(b"xx"), "image/jpeg"))]
    assert client.post("/search/image", files=files).status_code == 503


def test_endpoint_caido_502(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: True)

    def boom(blobs, top_k=50):
        raise requests_lib.ConnectionError("down")

    monkeypatch.setattr(image_search, "query_images", boom)
    files = [("images", ("f.jpg", io.BytesIO(b"xx"), "image/jpeg"))]
    assert client.post("/search/image", files=files).status_code == 502


def test_demasiadas_fotos_400(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: True)
    files = [("images", (f"f{i}.jpg", io.BytesIO(b"xx"), "image/jpeg")) for i in range(7)]
    assert client.post("/search/image", files=files).status_code == 400


def test_health_expone_image_ready():
    r = client.get("/health")
    assert "image_ready" in r.json()
```

- [ ] **Step 3: Verificar que fallan**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pytest test_image_search.py -v
```
Expected: los 2 de fusión PASS; los nuevos FAIL con 404 (`/search/image` no existe) y KeyError `image_ready`.

- [ ] **Step 4: Implementar en `main.py`**

4a. Ampliar el import de FastAPI (línea 12):

```python
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
```

4b. Tras el bloque `try: import azure_search ...` (líneas 24-27), añadir:

```python
# búsqueda por imagen (endpoint DINOv2 en Azure ML). Opcional: sin el módulo o sin
# API key, la app funciona igual y /health expone image_ready=false.
try:
    import image_search
except Exception:  # noqa: BLE001
    image_search = None
```

4c. En `health()` (línea 151), añadir la clave al dict devuelto:

```python
@app.get("/health")
def health():
    return {"status": "ok", "products": len(PRODUCTS),
            "relations_models": len(RELATIONS),
            "chat_ready": bool(chat and chat.API_KEY),
            "image_ready": bool(image_search and image_search.ready())}
```

4d. Después de la función `search()` (tras la línea 478, antes del bloque de chat), añadir:

```python
# ---------------------------------------------------------------- búsqueda por imagen
MAX_PHOTOS = 6
MAX_PHOTO_MB = 10
IMAGE_TOP_K = 50      # candidatos por foto: alto para que el filtro de texto tenga material


def _sel_from_filters(filters):
    """Filtros de parse_query (acabado/precio) -> formato 'sel' que entiende matches()."""
    sel = {"categories": [], "collections": [], "finishes": [],
           "price": (None, None), "length": (None, None),
           "width": (None, None), "height": (None, None)}
    for f in filters:
        if f["type"] == "finish":
            sel["finishes"] = f.get("values") or []
        elif f["type"] == "price":
            sel["price"] = (f.get("min_price"), f.get("max_price"))
    return sel


def _filter_by_text(sku_scores, q):
    """El texto FILTRA los candidatos visuales (el score visual sigue ordenando):
    filtros estructurados de parse_query (acabado/banda de precio) + los tokens de
    intención restantes por substring (AND) contra SEARCH_INDEX."""
    parsed = parse_query(q)
    sel = _sel_from_filters(parsed["filters"])
    tokens = [t for t in parsed["intent_phrase"].lower().split() if t]
    out = {}
    for sku, score in sku_scores.items():
        p = BY_SKU.get(sku)
        if not p or not matches(p, sel):
            continue
        blob = SEARCH_INDEX.get(sku, "")
        if tokens and not all(t in blob for t in tokens):
            continue
        out[sku] = score
    return out


def _image_cards(sku_scores, limit=30):
    """{sku: score} -> (total_modelos, ModelCards ordenadas por score). Score de modelo =
    máx de sus SKUs; default = la variante con mejor score. Los recambios SÍ entran
    (identificar un recambio por foto es el caso de uso principal)."""
    best = {}                                # model -> (score, sku)
    for sku, score in sku_scores.items():
        p = BY_SKU.get(sku)
        m = p.get("model") if p else None
        if not m:
            continue
        if m not in best or score > best[m][0]:
            best[m] = (score, sku)
    order = sorted(best, key=lambda m: -best[m][0])
    cards = []
    for m in order[:limit]:
        variants = BY_MODEL[m]
        default = next((i for i, v in enumerate(variants) if v["sku"] == best[m][1]), 0)
        rep = variants[default]
        cards.append({"model": m, "title": rep.get("title"),
                      "collection": rep.get("collection"), "category": rep.get("category"),
                      "default": default,
                      "variants": [variant_summary(v) for v in variants]})
    return len(order), cards


@app.post("/search/image")
def search_image(images: list[UploadFile] = File(...),
                 q: str = Form(""), mode: str = Form("same")):
    """Busca productos por foto(s) vía el endpoint DINOv2. mode='same' fusiona las
    fotos en un ranking (mismo producto, varios ángulos); mode='distinct' devuelve
    un ranking por foto. `q` opcional refina (filtra) los matches visuales."""
    if image_search is None or not image_search.ready():
        raise HTTPException(503, "Búsqueda por imagen no configurada (falta IMAGE_SEARCH_API_KEY)")
    if not 1 <= len(images) <= MAX_PHOTOS:
        raise HTTPException(400, f"Sube entre 1 y {MAX_PHOTOS} fotos")
    blobs = []
    for i, up in enumerate(images, 1):
        if up.content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(400, f"Foto {i}: formato no soportado ({up.content_type})")
        data = up.file.read()
        if not data:
            raise HTTPException(400, f"Foto {i}: fichero vacío")
        if len(data) > MAX_PHOTO_MB * 1024 * 1024:
            raise HTTPException(400, f"Foto {i}: supera {MAX_PHOTO_MB}MB")
        blobs.append(data)

    try:
        rankings = image_search.query_images(blobs, top_k=IMAGE_TOP_K)
    except image_search.EndpointError as e:
        raise HTTPException(400, f"El servicio no pudo procesar una foto: {e}")
    except Exception as e:  # noqa: BLE001 — endpoint caído/timeout: error legible
        print(f"[/search/image] DINO FALLO ({e!r})", flush=True)
        raise HTTPException(502, "El servicio de búsqueda por imagen no responde; inténtalo de nuevo")

    q_clean = q.strip()
    if mode == "distinct":
        groups = []
        for i, rk in enumerate(rankings, 1):
            cand = _filter_by_text(rk, q_clean) if q_clean else rk
            total, cards = _image_cards(cand)
            groups.append({"photo": i, "total": total, "results": cards})
        print(f"[/search/image] fotos={len(blobs)} q={q_clean!r} mode=distinct "
              f"-> {[g['total'] for g in groups]}", flush=True)
        return {"query": q, "mode": "distinct", "groups": groups}

    fused = image_search.fuse_same(rankings)
    cand = _filter_by_text(fused, q_clean) if q_clean else fused
    total, cards = _image_cards(cand)
    print(f"[/search/image] fotos={len(blobs)} q={q_clean!r} candidatos={len(fused)} "
          f"-> modelos={total}", flush=True)
    return {"query": q, "total": total, "results": cards, "facets": None}
```

4e. En `backend/.env.example`, añadir al final:

```
# Búsqueda por imagen (endpoint DINOv2 en Azure ML) — /search/image
IMAGE_SEARCH_API_KEY=<api-key-del-endpoint>
IMAGE_SEARCH_SCORING_URI=https://dino-embedder-roca.spaincentral.inference.ml.azure.com/score
```

- [ ] **Step 5: Verificar que todos los tests pasan (los nuevos y los existentes)**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev\backend; .\.venv\Scripts\python.exe -m pytest test_image_search.py test_facets.py -v
```
Expected: todos PASS.

- [ ] **Step 6: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add backend/main.py backend/requirements.txt backend/.env.example backend/test_image_search.py; git commit -m "feat(backend): endpoint /search/image con fusion multi-foto y filtro por texto"
```

---

### Task 3: Smoke test real contra el endpoint deployado

**Files:**
- Create: `tools/smoke_search_image.py`

**Interfaces:**
- Consumes: `POST http://localhost:8000/search/image` (Task 2) con fotos reales.
- Produces: script manual de verificación; no es parte de la suite pytest.

- [ ] **Step 1: Escribir el script**

Crear `tools/smoke_search_image.py`:

```python
"""
Smoke test manual de /search/image contra el backend local y el endpoint DINO real.

Requiere: backend corriendo en :8000 con IMAGE_SEARCH_API_KEY en backend/.env.
Uso:  python tools/smoke_search_image.py [carpeta_con_fotos] [q opcional]
      (por defecto: C:/Users/parand01/Desktop/IA/hackaton_dino/imgtest, sin texto)
"""
import glob
import os
import sys

import requests

API = os.getenv("API_URL", "http://localhost:8000")
folder = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\parand01\Desktop\IA\hackaton_dino\imgtest"
q = sys.argv[2] if len(sys.argv) > 2 else ""

paths = sorted(glob.glob(os.path.join(folder, "*.jpg")))[:3]
if not paths:
    sys.exit(f"No hay .jpg en {folder}")
print(f"Fotos: {[os.path.basename(p) for p in paths]}  q={q!r}")

files = [("images", (os.path.basename(p), open(p, "rb"), "image/jpeg")) for p in paths]
r = requests.post(f"{API}/search/image", files=files, data={"q": q, "mode": "same"}, timeout=120)
print(f"HTTP {r.status_code}")
r.raise_for_status()
body = r.json()
print(f"total modelos: {body['total']}")
for c in body["results"][:9]:
    v = c["variants"][c["default"]]
    print(f"  {c['model']:<14} {v['sku']:<14} {c['title']}")
```

- [ ] **Step 2: Ejecutarlo (backend levantado, key en .env)**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; backend\.venv\Scripts\python.exe tools\smoke_search_image.py
```
Expected: `HTTP 200`, `total modelos > 0`, y los títulos/SKUs listados corresponden al producto fotografiado. Si devuelve 503, falta `IMAGE_SEARCH_API_KEY` en `backend/.env`. Repetir con texto: `... tools\smoke_search_image.py C:\Users\parand01\Desktop\IA\hackaton_dino\imgtest lavabo` y comprobar que filtra.

- [ ] **Step 3: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add tools/smoke_search_image.py; git commit -m "test(tools): smoke manual de /search/image con fotos reales"
```

---

### Task 4: Frontend — API client, downscale y componentes de imagen

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/imageUtils.ts`
- Create: `frontend/src/ImageSearch.tsx`

**Interfaces:**
- Consumes: `POST /search/image` y `GET /health` (Task 2); tipos `ModelCard`, `SearchResponse` existentes en `api.ts`.
- Produces (usado por Tasks 5–6):
  - `api.ts`: `type ImageMode = "same" | "distinct"`, `interface ImageSearchGroup { photo: number; total: number; results: ModelCard[] }`, `interface ImageSearchSameResponse { query: string; total: number; results: ModelCard[]; facets: null }`, `interface ImageSearchDistinctResponse { query: string; mode: "distinct"; groups: ImageSearchGroup[] }`, `searchByImage(photos: Blob[], q: string, mode: ImageMode): Promise<ImageSearchSameResponse | ImageSearchDistinctResponse>`, `getHealth(): Promise<{ chat_ready: boolean; image_ready?: boolean }>`.
  - `imageUtils.ts`: `downscalePhoto(file: File, maxSide?: number, quality?: number): Promise<Blob>`.
  - `ImageSearch.tsx`: `interface Photo { id: string; blob: Blob; url: string }`, `CameraIcon()`, `ImageDropPanel(props)` con props `{ photos: Photo[]; sameProduct: boolean; busy: boolean; onAdd: (files: FileList | File[]) => void; onRemove: (id: string) => void; onToggleSame: (v: boolean) => void; onSearch: () => void }`.

- [ ] **Step 1: Añadir tipos y `searchByImage` a `api.ts`**

En `frontend/src/api.ts`, cambiar el tipo de retorno de `getHealth` (línea 195):

```ts
export async function getHealth(): Promise<{ chat_ready: boolean; image_ready?: boolean }> {
```

Y añadir al final del fichero:

```ts
// ---- Búsqueda por imagen (endpoint DINOv2 vía backend) ----
export type ImageMode = "same" | "distinct";

export interface ImageSearchGroup { photo: number; total: number; results: ModelCard[]; }
export interface ImageSearchSameResponse {
  query: string; total: number; results: ModelCard[]; facets: null;
}
export interface ImageSearchDistinctResponse {
  query: string; mode: "distinct"; groups: ImageSearchGroup[];
}

export async function searchByImage(
  photos: Blob[], q: string, mode: ImageMode
): Promise<ImageSearchSameResponse | ImageSearchDistinctResponse> {
  const fd = new FormData();
  photos.forEach((b, i) => fd.append("images", b, `foto-${i + 1}.jpg`));
  if (q) fd.set("q", q);
  fd.set("mode", mode);
  const r = await fetch(`${API}/search/image`, { method: "POST", body: fd });
  if (!r.ok) {
    const detail = await r.json().then((j) => j.detail).catch(() => null);
    throw new Error(detail || "Error en la búsqueda por imagen");
  }
  return r.json();
}
```

- [ ] **Step 2: Crear `imageUtils.ts`**

Crear `frontend/src/imageUtils.ts`:

```ts
// Reescala una foto en el navegador antes de subirla: máx. 1024px de lado, JPEG 0.85.
// (~1.5MB de móvil -> ~150KB; el modelo reescala de todos modos, no se pierde señal útil)
export async function downscalePhoto(
  file: File, maxSide = 1024, quality = 0.85
): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  if (scale === 1 && file.type === "image/jpeg") { bmp.close(); return file; }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  bmp.close();
  return await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("No se pudo procesar la foto"))),
      "image/jpeg", quality,
    )
  );
}
```

- [ ] **Step 3: Crear `ImageSearch.tsx`**

Crear `frontend/src/ImageSearch.tsx`:

```tsx
import { useRef, type DragEvent } from "react";

// Una foto añadida al buscador: blob reescalado + objectURL para la preview.
// El dueño del estado (App) debe revocar `url` al quitar la foto.
export interface Photo { id: string; blob: Blob; url: string; }

export function CameraIcon({ small = false }: { small?: boolean }) {
  const s = small ? 16 : 20;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"
            strokeLinejoin="round" />
      <circle cx="12" cy="14" r="3.4" />
    </svg>
  );
}

// Panel dropzone bajo la barra (mismo patrón visual que el panel de sugerencias):
// zona de drop + selector de ficheros, miniaturas con borrado, toggle de modo y CTA.
export function ImageDropPanel({
  photos, sameProduct, busy, onAdd, onRemove, onToggleSame, onSearch,
}: {
  photos: Photo[];
  sameProduct: boolean;
  busy: boolean;
  onAdd: (files: FileList | File[]) => void;
  onRemove: (id: string) => void;
  onToggleSame: (v: boolean) => void;
  onSearch: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.files.length) onAdd(e.dataTransfer.files);
  }

  return (
    <div className="rs-suggest rs-imgpanel">
      <div
        className="rs-dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <CameraIcon />
        <p>Arrastra tus fotos aquí o <u>haz click para elegir</u></p>
        <p className="rs-dropzone-hint">Hasta 6 fotos · varios ángulos mejoran el resultado</p>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => { if (e.target.files) onAdd(e.target.files); e.target.value = ""; }}
        />
      </div>

      {photos.length > 0 && (
        <div className="rs-drop-thumbs">
          {photos.map((p) => (
            <span key={p.id} className="rs-drop-thumb">
              <img src={p.url} alt="" />
              <button type="button" aria-label="Quitar foto" onClick={() => onRemove(p.id)}>×</button>
            </span>
          ))}
        </div>
      )}

      {photos.length >= 2 && (
        <label className="rs-imgpanel-toggle">
          <input
            type="checkbox"
            checked={sameProduct}
            onChange={(e) => onToggleSame(e.target.checked)}
          />
          Las fotos son del mismo producto
        </label>
      )}

      <button
        type="button"
        className="rs-imgpanel-cta"
        disabled={photos.length === 0 || busy}
        onClick={onSearch}
      >
        {busy ? "Buscando…" : "Buscar por imagen"}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Verificar que compila**

```powershell
$env:Path = "C:\Program Files\nodejs;" + $env:Path; cd c:\Users\parand01\Desktop\IA\HackatonDev\frontend; npx tsc --noEmit
```
Expected: sin errores (los ficheros nuevos aún no se usan desde App; TS los compila igualmente).

- [ ] **Step 5: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add frontend/src/api.ts frontend/src/imageUtils.ts frontend/src/ImageSearch.tsx; git commit -m "feat(frontend): cliente /search/image, downscale de fotos y panel dropzone"
```

---

### Task 5: Frontend — cablear la búsqueda por imagen en `App.tsx` (modo same) + estilos

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: todo lo de Task 4; estado/funciones existentes de App (`runSearch`, `doSearch`, `results/total/facets/submitted/loading/error`).
- Produces: estado `photos`/`imageGroups`/`sameProduct`/`imageReady` y función `runImageSearch(text: string)` que Task 6 reutiliza para pintar el modo distinct.

- [ ] **Step 1: Imports y estado en `App.tsx`**

Añadir a los imports (tras la línea 14):

```tsx
import { ImageDropPanel, CameraIcon, type Photo } from "./ImageSearch";
import { downscalePhoto } from "./imageUtils";
import { searchByImage, type ImageSearchGroup } from "./api";
```

(`searchByImage` y `ImageSearchGroup` pueden fusionarse en el import existente de `./api`.)

Dentro de `App()`, junto al resto de estado (tras la línea 111):

```tsx
// --- búsqueda por imagen ---
const [photos, setPhotos] = useState<Photo[]>([]);
const [imgPanelOpen, setImgPanelOpen] = useState(false);
const [sameProduct, setSameProduct] = useState(true);
const [imageGroups, setImageGroups] = useState<ImageSearchGroup[] | null>(null);
const [imageReady, setImageReady] = useState(false);
const [dragOver, setDragOver] = useState(false);
```

- [ ] **Step 2: Handlers de fotos y búsqueda por imagen**

Añadir dentro de `App()` (después de `runSearch`, línea 195):

```tsx
const MAX_PHOTOS = 6;

async function addPhotos(files: FileList | File[]) {
  const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
  const room = MAX_PHOTOS - photos.length;
  const add: Photo[] = [];
  for (const f of list.slice(0, room)) {
    try {
      const blob = await downscalePhoto(f);
      add.push({ id: `${f.name}-${f.size}-${Math.random()}`, blob, url: URL.createObjectURL(blob) });
    } catch { /* foto ilegible: se ignora */ }
  }
  if (add.length) { setPhotos((p) => [...p, ...add]); setImgPanelOpen(true); }
}

function removePhoto(id: string) {
  setPhotos((p) => {
    const ph = p.find((x) => x.id === id);
    if (ph) URL.revokeObjectURL(ph.url);
    return p.filter((x) => x.id !== id);
  });
}

// Búsqueda por imagen: las fotos mandan; el texto (si hay) filtra los matches visuales.
async function runImageSearch(text: string) {
  setLoading(true); setError(null); setDetail(null); setOpen(false); setImgPanelOpen(false);
  try {
    const mode = photos.length > 1 && !sameProduct ? "distinct" : "same";
    const r = await searchByImage(photos.map((p) => p.blob), text.trim(), mode);
    const nf = `${photos.length} foto${photos.length > 1 ? "s" : ""}`;
    setSubmitted(text.trim() ? `${text.trim()} · ${nf}` : `Búsqueda por imagen (${nf})`);
    setFacets(null);                       // sin sidebar en modo imagen
    if ("groups" in r) {
      setImageGroups(r.groups);
      setResults([]);
      setTotal(r.groups.reduce((n, g) => n + g.total, 0));
    } else {
      setImageGroups(null);
      setResults(r.results);
      setTotal(r.total);
    }
  } catch (err) {
    setError(String(err));
  } finally {
    setLoading(false);
  }
}
```

- [ ] **Step 3: Integrar con el flujo existente**

3a. En `runSearch` (línea 185), limpiar el modo imagen al inicio de la función:

```tsx
async function runSearch(text: string, sc: string | null, s: Selected) {
  setLoading(true); setError(null); setDetail(null); setOpen(false);
  setImageGroups(null);                    // una búsqueda de texto sale del modo imagen
  try {
```

3b. En `doSearch` (línea 198), desviar a imagen cuando hay fotos:

```tsx
function doSearch(e: FormEvent) {
  e.preventDefault();
  if (photos.length > 0) { runImageSearch(q); return; }
  if (!q.trim()) return;
  const s = withAutoFilters(EMPTY_SELECTED, autoFilters);
  setBaseText(q); setSubcat(null); setSel(s); setSubmitted(q);
  clearTimeout(debounce.current);
  runSearch(q, null, s);
}
```

3c. En el `useEffect` de health (línea 250):

```tsx
useEffect(() => {
  getHealth().then((h) => {
    setChatReady(!!h.chat_ready);
    setImageReady(!!h.image_ready);
  }).catch(() => {});
}, []);
```

- [ ] **Step 4: UI en el JSX de la barra**

4a. En el `<div className="rs-searchbox">` (línea 349), añadir drag&drop y la clase de resaltado:

```tsx
<div
  className={`rs-searchbox${dragOver ? " is-dragover" : ""}`}
  ref={boxRef}
  onDragOver={(e) => { if (imageReady) { e.preventDefault(); setDragOver(true); } }}
  onDragLeave={() => setDragOver(false)}
  onDrop={(e) => {
    if (!imageReady) return;
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files.length) addPhotos(e.dataTransfer.files);
  }}
>
```

4b. Chips de fotos: justo antes del `<input>` (línea 351):

```tsx
{photos.map((p) => (
  <span key={p.id} className="rs-photo-chip">
    <img src={p.url} alt="" />
    <button type="button" aria-label="Quitar foto" onClick={() => removePhoto(p.id)}>×</button>
  </span>
))}
```

4c. Placeholder condicional en el `<input>`:

```tsx
placeholder={photos.length ? "Añade texto para refinar (opcional)" : "Introduce tu búsqueda"}
```

4d. Botón cámara: tras el botón de borrar (`rs-clear`, línea 367), dentro del searchbox:

```tsx
{imageReady && (
  <button
    type="button"
    className="rs-cam-btn"
    aria-label="Buscar por imagen"
    title="Buscar por imagen"
    onClick={() => { setImgPanelOpen((v) => !v); setOpen(false); }}
  >
    <CameraIcon />
  </button>
)}
```

4e. Panel dropzone: junto a los paneles `rs-suggest` existentes (tras la línea 428), con prioridad sobre ellos:

```tsx
{imgPanelOpen && (
  <ImageDropPanel
    photos={photos}
    sameProduct={sameProduct}
    busy={loading}
    onAdd={addPhotos}
    onRemove={removePhoto}
    onToggleSame={setSameProduct}
    onSearch={() => runImageSearch(q)}
  />
)}
```

Y condicionar los dos paneles de sugerencias existentes para que no compitan con él: cambiar `{open && q.trim() === "" && (` por `{!imgPanelOpen && open && q.trim() === "" && (` y `{open && q.trim() !== "" && (` por `{!imgPanelOpen && open && q.trim() !== "" && (`.

- [ ] **Step 5: Estilos en `styles.css`**

Añadir al final de `frontend/src/styles.css`:

```css
/* ---------- Búsqueda por imagen ---------- */
.rs-searchbox.is-dragover { outline: 2px dashed #c00; outline-offset: -2px; }

.rs-cam-btn {
  display: inline-flex; align-items: center; border: 0; background: none;
  padding: 4px; cursor: pointer; color: #1a1a1a;
}
.rs-cam-btn:hover { color: #c00; }

.rs-photo-chip {
  position: relative; display: inline-flex; flex: 0 0 auto;
  width: 34px; height: 34px; border-radius: 6px; overflow: hidden;
  border: 1px solid #ddd; margin-right: 4px;
}
.rs-photo-chip img { width: 100%; height: 100%; object-fit: cover; }
.rs-photo-chip button {
  position: absolute; top: 0; right: 0; width: 14px; height: 14px;
  border: 0; border-radius: 0 0 0 6px; background: rgba(0,0,0,.55); color: #fff;
  font-size: 11px; line-height: 1; cursor: pointer; padding: 0;
}

.rs-imgpanel { padding: 14px; }
.rs-dropzone {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 22px 14px; border: 2px dashed #ccc; border-radius: 10px;
  cursor: pointer; text-align: center; color: #444;
}
.rs-dropzone:hover { border-color: #c00; color: #c00; }
.rs-dropzone p { margin: 0; font-size: 14px; }
.rs-dropzone-hint { font-size: 12px; color: #999; }

.rs-drop-thumbs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.rs-drop-thumb {
  position: relative; width: 64px; height: 64px; border-radius: 8px;
  overflow: hidden; border: 1px solid #ddd;
}
.rs-drop-thumb img { width: 100%; height: 100%; object-fit: cover; }
.rs-drop-thumb button {
  position: absolute; top: 2px; right: 2px; width: 18px; height: 18px;
  border: 0; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff;
  font-size: 12px; line-height: 1; cursor: pointer; padding: 0;
}

.rs-imgpanel-toggle {
  display: flex; align-items: center; gap: 8px; margin-top: 12px;
  font-size: 13px; color: #333; cursor: pointer;
}

.rs-imgpanel-cta {
  margin-top: 12px; width: 100%; padding: 10px 0; border: 0; border-radius: 8px;
  background: #c00; color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
}
.rs-imgpanel-cta:disabled { background: #ddd; cursor: default; }

/* Secciones por foto (modo "productos distintos") */
.rs-imgsec { margin-bottom: 28px; }
.rs-imgsec-head {
  display: flex; align-items: center; gap: 10px;
  font-size: 16px; font-weight: 600; margin: 0 0 12px;
}
.rs-imgsec-head img {
  width: 44px; height: 44px; object-fit: cover; border-radius: 8px; border: 1px solid #ddd;
}
```

Nota: si el rojo corporativo del proyecto usa otra variable/tono en `styles.css` (p.ej. `var(--brand)` o un hex concreto), usa ese en lugar de `#c00` para mantener consistencia.

- [ ] **Step 6: Verificar que compila**

```powershell
$env:Path = "C:\Program Files\nodejs;" + $env:Path; cd c:\Users\parand01\Desktop\IA\HackatonDev\frontend; npx tsc --noEmit
```
Expected: sin errores.

- [ ] **Step 7: Prueba manual en navegador (modo same)**

Con backend (`:8000`, con API key) y frontend (`:5173`) corriendo:
1. La cámara aparece en la barra (si `/health` da `image_ready: true`).
2. Click cámara → panel; arrastrar 2 fotos de `C:\Users\parand01\Desktop\IA\hackaton_dino\imgtest` → miniaturas + toggle visible.
3. "Buscar por imagen" → parrilla con resultados, sin sidebar de facetas, heading "Búsqueda por imagen (2 fotos)".
4. Escribir texto (p.ej. "lavabo") y pulsar Buscar → resultados filtrados, heading "lavabo · 2 fotos".
5. Quitar las fotos (chips ×) y buscar texto → comportamiento clásico intacto.

- [ ] **Step 8: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add frontend/src/App.tsx frontend/src/styles.css; git commit -m "feat(frontend): busqueda por imagen en la barra (drag&drop, chips, modo fusion)"
```

---

### Task 6: Frontend — modo "productos distintos" (secciones por foto)

**Files:**
- Modify: `frontend/src/App.tsx` (render del grid)

**Interfaces:**
- Consumes: `imageGroups: ImageSearchGroup[] | null` y `photos: Photo[]` (Task 5). `groups[i].photo` es 1-based y se corresponde con `photos[i]` (mismo orden de subida).
- Produces: render final; nada nuevo para otros.

- [ ] **Step 1: Render por secciones**

En `App.tsx`, sustituir el bloque del grid (líneas 467-477):

```tsx
<div className="rs-grid">
  {results.map((c) => (
    <ProductCard
      key={c.model}
      card={c}
      onOpen={openProduct}
      onBuyOnline={addToCart}
      onFindLocal={openLocalSuppliers}
    />
  ))}
</div>
```

por:

```tsx
{imageGroups ? (
  imageGroups.map((g, i) => (
    <section key={g.photo} className="rs-imgsec">
      <h2 className="rs-imgsec-head">
        {photos[i] && <img src={photos[i].url} alt={`Foto ${g.photo}`} />}
        <span>Foto {g.photo} · {g.total} resultado{g.total === 1 ? "" : "s"}</span>
      </h2>
      {g.total === 0 && <p className="rs-state">Sin resultados para esta foto.</p>}
      <div className="rs-grid">
        {g.results.map((c) => (
          <ProductCard
            key={`${g.photo}-${c.model}`}
            card={c}
            onOpen={openProduct}
            onBuyOnline={addToCart}
            onFindLocal={openLocalSuppliers}
          />
        ))}
      </div>
    </section>
  ))
) : (
  <div className="rs-grid">
    {results.map((c) => (
      <ProductCard
        key={c.model}
        card={c}
        onOpen={openProduct}
        onBuyOnline={addToCart}
        onFindLocal={openLocalSuppliers}
      />
    ))}
  </div>
)}
```

- [ ] **Step 2: Compilar + prueba manual del modo distinct**

```powershell
$env:Path = "C:\Program Files\nodejs;" + $env:Path; cd c:\Users\parand01\Desktop\IA\HackatonDev\frontend; npx tsc --noEmit
```
Expected: sin errores.

Manual: subir 2 fotos de productos DIFERENTES de `imgtest`, desmarcar "Las fotos son del mismo producto", buscar → dos secciones, cada una con la miniatura de su foto y sus propios resultados. El botón "Añadir a la cesta" y el detalle de producto funcionan dentro de cada sección.

- [ ] **Step 3: Prueba manual de regresión**

1. Búsqueda de texto normal (sin fotos) → parrilla + facetas + sugerencias como siempre.
2. Chat IA → sigue actualizando la parrilla.
3. Parar el backend sin `IMAGE_SEARCH_API_KEY` → la cámara no aparece; todo lo demás intacto.

- [ ] **Step 4: Commit**

```powershell
cd c:\Users\parand01\Desktop\IA\HackatonDev; git add frontend/src/App.tsx; git commit -m "feat(frontend): secciones por foto en modo productos distintos"
```

---

## Self-review (hecho al escribir el plan)

- **Cobertura del spec:** entrada por cámara + drag&drop sobre la barra (T5), panel con miniaturas/toggle/CTA (T4-T5), límite 6 fotos y downscale cliente (T4-T5), chips en barra + texto que refina (T5), parrilla sin % ordenada por score (T2), modo distinct por secciones (T6), facetas ocultas (T5: `setFacets(null)`), `/search/image` + fusión media-sobre-total + filtro texto con `parse_query` (T2), config opcional + `image_ready` (T2), errores 400/502/503 (T2), tests unit + smoke real + manual (T1-T3, pasos manuales en T5-T6). Sin huecos.
- **Placeholders:** ninguno; todo el código está inline.
- **Consistencia de tipos:** `query_images/fuse_same/ready/EndpointError` (T1) = lo que consume T2; shape de respuestas (T2) = tipos TS (T4) = render (T5-T6); `Photo`/props de `ImageDropPanel` (T4) = uso en T5.
