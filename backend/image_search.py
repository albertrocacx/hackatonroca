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
import time
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

# El endpoint de Azure ML (una instancia) rechaza ráfagas: con 3+ peticiones
# simultáneas devuelve timeout/errores. Concurrencia limitada + un reintento
# por foto ante fallos de transporte.
MAX_CONCURRENCY = 2


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
    """Una llamada por foto, con concurrencia limitada y un reintento por foto
    ante fallos de transporte (EndpointError no se reintenta: la foto es mala)."""
    def one(b: bytes) -> dict[str, float]:
        try:
            return query_image(b, top_k)
        except EndpointError:
            raise
        except Exception:  # noqa: BLE001 — timeout/red: un unico reintento
            time.sleep(1.5)
            return query_image(b, top_k)

    workers = min(MAX_CONCURRENCY, max(1, len(images)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, images))


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
