# -*- coding: utf-8 -*-
"""
Modelos 3D de producto para Realidad Aumentada ("Visualizar en tu habitación").

Los CAD viven en Blob Storage (contenedor privado):
    data/products/products_3d/<model>/<model>__<codigo>__<coleccion>__..__CAD3D.(fbx|3ds)

Fuente de verdad de qué hay utilizable: backend/data/models3d_manifest.json
(construido con build_models3d.py: valida cada fichero con los loaders de
three.js — muchos FBX del CAD son v3000/6100, que el navegador NO puede leer,
y ahí el .3ds pasa a ser el formato principal). Para carpetas subidas después
del manifest hay un fallback de listado en vivo con TTL corto: se eligen los
ficheros sin validar y el visor del frontend ya cae de FBX a 3DS en runtime.

El contenedor es privado -> el backend hace de proxy (get_bytes) con una caché
en disco en data/.cache3d para no descargar el mismo blob en cada visita.
"""
import json
import os
import re
import threading
import time

try:  # credenciales en backend/.env (mismo patrón que el resto de módulos)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, "data", "models3d_manifest.json")
CACHE_DIR = os.path.join(HERE, "data", ".cache3d")

CONTAINER = "data"
PREFIX = "products/products_3d/"
LIVE_TTL = 300  # s: las carpetas no manifestadas se relistan como mucho cada 5 min

_lock = threading.Lock()
_manifest: dict | None = None
_live: dict[str, tuple[float, list[str] | None]] = {}  # model -> (ts, nombres de blob)
_container = None


def _load_manifest() -> dict:
    global _manifest
    if _manifest is None:
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as f:
                _manifest = json.load(f)
            print(f"[models3d] manifest: {len(_manifest)} modelos con 3D validado")
        except FileNotFoundError:
            _manifest = {}
            print("[models3d] sin manifest (data/models3d_manifest.json); solo listado en vivo")
        except Exception as e:  # noqa: BLE001
            _manifest = {}
            print(f"[models3d] manifest ilegible: {e}")
    return _manifest


def _get_container():
    global _container
    if _container is None:
        cs = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
        if not cs:
            return None
        from azure.storage.blob import BlobServiceClient
        _container = BlobServiceClient.from_connection_string(cs).get_container_client(CONTAINER)
    return _container


# mismo emparejado fbx/3ds que build_models3d.choose(), sobre nombres sin validar
def _variant_key(name: str) -> str:
    stem = os.path.splitext(name)[0]
    return re.sub(r"(__|_)(fbx|threeds)(_|__)?", "__", stem, flags=re.I)


def _choose_unvalidated(names: list[str]) -> dict | None:
    glb = sorted(n for n in names if n.lower().endswith(".glb"))
    fbx = sorted(n for n in names if n.lower().endswith(".fbx"))
    tds = sorted(n for n in names if n.lower().endswith(".3ds"))
    if not glb and not fbx and not tds:
        return None
    chosen_tds = None
    if fbx and tds:
        want = _variant_key(fbx[0])
        chosen_tds = next((t for t in tds if _variant_key(t) == want), None)
    return {
        "glb": glb[0] if glb else None,
        "fbx": fbx[0] if fbx else None,
        "tds": chosen_tds or (tds[0] if tds else None),
        "files": [{"name": n} for n in sorted(names)],
        "validated": False,
    }


def _live_names(model: str) -> list[str] | None:
    """Nombres de blob de la carpeta del modelo (cache con TTL corto)."""
    with _lock:
        hit = _live.get(model)
        if hit and time.time() - hit[0] < LIVE_TTL:
            return hit[1]
    names: list[str] | None
    try:
        cont = _get_container()
        if cont is None:
            names = None
        else:
            prefix = f"{PREFIX}{model}/"
            names = [b.name[len(prefix):] for b in cont.list_blobs(name_starts_with=prefix)]
            names = [n for n in names if "/" not in n]
    except Exception as ex:  # noqa: BLE001
        print(f"[models3d] error listando {model}: {ex}")
        names = None
    with _lock:
        _live[model] = (time.time(), names)
    return names


def entry(model: str) -> dict | None:
    """Ficha 3D de un modelo del catálogo, o None si no tiene."""
    if not model or "/" in model or "\\" in model:
        return None
    man = _load_manifest()
    names = _live_names(model)
    if model in man:
        e = {**man[model], "validated": True}
        # los .glb son posteriores al manifest: se detectan en vivo. Es el
        # mejor formato web (glTF binario: PBR, metros, Y-up) y tiene prioridad
        glb = sorted(n for n in (names or []) if n.lower().endswith(".glb"))
        if glb:
            e["glb"] = glb[0]
            if not any(f["name"] == glb[0] for f in e["files"]):
                e = {**e, "files": e["files"] + [{"name": g} for g in glb]}
        return e
    if names is None:
        return None
    return _choose_unvalidated(names)


def get_bytes(model: str, name: str) -> bytes | None:
    """Bytes de un fichero 3D. Solo sirve nombres que existan en la ficha del
    modelo (evita rutas arbitrarias). Cachea en disco."""
    e = entry(model)
    if not e or not any(f["name"] == name for f in e["files"]):
        return None
    safe = re.sub(r"[^A-Za-z0-9._()\- ]", "_", f"{model}__{name}")
    cached = os.path.join(CACHE_DIR, safe)
    if os.path.exists(cached):
        with open(cached, "rb") as fh:
            return fh.read()
    cont = _get_container()
    if cont is None:
        return None
    try:
        data = cont.download_blob(f"{PREFIX}{model}/{name}").readall()
    except Exception as ex:  # noqa: BLE001
        print(f"[models3d] error descargando {model}/{name}: {ex}")
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cached + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, cached)
    except OSError:
        pass  # sin caché en disco seguimos sirviendo
    return data


# --- USDZ efímeros para AR Quick Look (iOS) -------------------------------
# Safari/Quick Look es poco fiable con blob: URLs generados en memoria; el
# frontend sube aquí el USDZ que exporta y lo referencia por una URL real con
# extensión .usdz. Almacén en memoria con TTL: es material de sesión de demo.
USDZ_TTL = 3600
_usdz_lock = threading.Lock()
_usdz_store: dict[str, tuple[float, bytes]] = {}


def usdz_key(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model) or "model"


def put_usdz(model: str, data: bytes) -> str:
    key = usdz_key(model)
    now = time.time()
    with _usdz_lock:
        for k in [k for k, (t, _) in _usdz_store.items() if now - t > USDZ_TTL]:
            _usdz_store.pop(k, None)
        _usdz_store[key] = (now, data)
    return key


def get_usdz(key: str) -> bytes | None:
    with _usdz_lock:
        hit = _usdz_store.get(key)
    if not hit or time.time() - hit[0] > USDZ_TTL:
        return None
    return hit[1]


def ready() -> bool:
    return bool(_load_manifest()) or bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))
