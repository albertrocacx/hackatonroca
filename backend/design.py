"""Diseña tu baño: render IA de escenas con productos Roca.

Dos modos sobre la MISMA llamada de imágenes (deployment gpt-image en Azure Foundry):
  a) desde cero      -> solo las fotos de los productos elegidos como referencia
  b) espacio real    -> la foto del usuario va PRIMERA y el prompt ordena conservarla

main.py inyecta con `configure` los índices del catálogo (BY_SKU, IMAGES, summary),
igual que chat.py con la búsqueda: este módulo no carga datos propios. Si faltan las
credenciales, READY=False y /api/design responde con un error claro sin romper la app.

La API es stateless: guardamos la última imagen generada por sesión (en memoria) para
poder iterar ("pon el grifo en negro") re-editando el render anterior.
"""
import asyncio
import base64
import os
import struct
import uuid

import httpx
from anthropic import AsyncAnthropicFoundry


# --- credenciales: env o backend/.env (mismo mini-loader que chat.py) ---
def _load_env():
    if os.environ.get("AZURE_OPENAI_KEY"):
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
# El deployment de imagen puede vivir en OTRO recurso que el resto (hoy: gpt-image-2 en el
# recurso de Claude). IMAGE_ENDPOINT/IMAGE_KEY mandan; si faltan, caen a AZURE_OPENAI_*.
ENDPOINT = (os.environ.get("IMAGE_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
if ENDPOINT.endswith("/openai/v1"):
    ENDPOINT = ENDPOINT[: -len("/openai/v1")]
API_KEY = os.environ.get("IMAGE_KEY") or os.environ.get("AZURE_OPENAI_KEY")
DEPLOYMENT = os.environ.get("IMAGE_DEPLOYMENT", "gpt-image-2")
SIZE = os.environ.get("IMAGE_SIZE", "1536x1024")          # apaisado: escena de baño
QUALITY = os.environ.get("IMAGE_QUALITY", "medium")

READY = bool(ENDPOINT and API_KEY)

# Análisis de la foto del baño ("renueva tu baño"): visión con el MISMO deployment de
# Claude que usa el chat. Si falta la clave, /api/design/analyze da error claro.
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
CLAUDE_ENDPOINT = os.environ.get(
    "CLAUDE_ENDPOINT", "https://hackathon-us-roca-resource.services.ai.azure.com/anthropic")
CLAUDE_MODEL = os.environ.get("CLAUDE_DEPLOYMENT", "claude-sonnet-5")
_claude = AsyncAnthropicFoundry(api_key=CLAUDE_KEY, base_url=CLAUDE_ENDPOINT) if CLAUDE_KEY else None

MAX_REFS = 7          # fotos de producto que se adjuntan como referencia (+1 foto del espacio)
MAX_SESSIONS = 50     # renders en memoria; al superarlo se descarta el más antiguo
TIMEOUT = httpx.Timeout(600.0, connect=20.0)

_SESSIONS: dict[str, dict] = {}   # session_id -> {image: bytes, products: [...]}

_by_sku = None
_images = None
_summary = None
_search = None


class DesignError(Exception):
    """Error de negocio con status HTTP sugerido (main.py lo mapea a HTTPException)."""
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def configure(by_sku: dict, images: dict, summary_fn, search_fn=None):
    """main.py inyecta sus índices en memoria, su summary() y su search()."""
    global _by_sku, _images, _summary, _search
    _by_sku, _images, _summary, _search = by_sku, images, summary_fn, search_fn


# ---------------------------------------------------------------- prompt
def _dims_cm(p) -> str | None:
    d = p.get("dims") or {}
    parts = []
    for key, lab in (("length_mm", "largo"), ("width_mm", "ancho"), ("height_mm", "alto")):
        try:
            parts.append(f"{lab} {float(d.get(key)) / 10:g} cm")
        except (TypeError, ValueError):
            continue
    return ", ".join(parts) or None


def _product_lines(products, first_ref: int) -> str:
    lines = []
    for i, p in enumerate(products):
        bits = [b for b in (p.get("category"), p.get("collection") and f"colección {p['collection']}",
                            p.get("finish") and f"acabado {p['finish']}", _dims_cm(p)) if b]
        lines.append(f"{i + 1}. {p.get('title')} ({'; '.join(bits)}) — imagen de referencia nº {first_ref + i}")
    return "\n".join(lines)


def _build_prompt(products, has_room: bool, style: str | None, instruction: str | None) -> str:
    lines = _product_lines(products, first_ref=2 if has_room else 1)
    if has_room:
        head = ("La PRIMERA imagen adjunta es el espacio real del cliente. Conserva su arquitectura "
                "tal cual: paredes, suelo, techo, ventanas, puertas, perspectiva e iluminación. "
                "Renueva el espacio integrando de forma realista estos productos Roca, colocados "
                "donde tenga sentido funcional:")
    else:
        head = (f"Fotografía fotorrealista de interiorismo de un cuarto de baño completo, "
                f"estilo {style or 'moderno'}, perspectiva a la altura de los ojos. "
                "El baño incluye exactamente estos productos Roca:")
    tail = ("Reproduce cada producto fielmente según su imagen de referencia (forma, material, "
            "color y proporciones reales, respetando las medidas indicadas). No añadas sanitarios, "
            "muebles de baño ni grifería distintos de los listados; sí puedes añadir atrezzo neutro "
            "(toallas, plantas, ambientación). Iluminación natural y materiales coherentes. "
            "Genera UNA única escena que ocupe toda la imagen: nada de collages, dípticos, "
            "comparativas antes/después ni vistas duplicadas.")
    parts = [head, lines, tail]
    if has_room and style:
        parts.append(f"Estilo de la reforma: {style}.")
    if instruction:
        parts.append(f"Indicaciones del cliente: {instruction}")
    return "\n\n".join(x for x in parts if x)


# ---------------------------------------------------------------- llamadas HTTP
def _headers() -> dict:
    return {"api-key": API_KEY, "Authorization": f"Bearer {API_KEY}"}


async def _download(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError:
        return None


def _img_size(data: bytes) -> tuple[int, int] | None:
    """(ancho, alto) de un PNG o JPEG leyendo cabeceras (sin dependencias de imagen)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":                       # JPEG: buscar el marcador SOFn
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker == 0x01 or 0xD0 <= marker <= 0xD9:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _pick_size(room: bytes | None) -> str:
    """El lienzo del render sigue la orientación de la foto del usuario. Si el formato
    no coincide (foto vertical en lienzo apaisado), gpt-image tiende a rellenar el hueco
    duplicando la escena en plan díptico. Sin foto, se usa el tamaño configurado."""
    if room:
        dims = _img_size(room)
        if dims:
            w, h = dims
            if h > w * 1.15:
                return "1024x1536"
            if w > h * 1.15:
                return "1536x1024"
            return "1024x1024"
    return SIZE


def _decode_upload(data: str) -> bytes:
    """Foto del usuario: data-URL ('data:image/jpeg;base64,...') o base64 pelado."""
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data)
    except Exception:
        raise DesignError("La imagen del espacio no es válida (se espera base64).", 400)


async def _call_images(client: httpx.AsyncClient, prompt: str, refs: list[bytes],
                       size: str | None = None) -> bytes:
    """POST /images/edits con imágenes de referencia (o /generations si no hay ninguna)."""
    size = size or SIZE
    if not refs:
        r = await client.post(
            f"{ENDPOINT}/openai/v1/images/generations", headers=_headers(),
            json={"model": DEPLOYMENT, "prompt": prompt, "size": size,
                  "quality": QUALITY, "n": 1},
        )
    else:
        data = {"model": DEPLOYMENT, "prompt": prompt, "size": size,
                "quality": QUALITY, "n": "1", "input_fidelity": "high"}
        files = [("image[]", (f"ref{i}.png", content, "image/png"))
                 for i, content in enumerate(refs)]
        r = await client.post(f"{ENDPOINT}/openai/v1/images/edits",
                              headers=_headers(), data=data, files=files)
        if r.status_code == 400 and "input_fidelity" in r.text:
            data.pop("input_fidelity")
            r = await client.post(f"{ENDPOINT}/openai/v1/images/edits",
                                  headers=_headers(), data=data, files=files)
    if r.status_code >= 400:
        detail = r.text[:300]
        if "unknown_model" in detail or "DeploymentNotFound" in detail:
            raise DesignError(f"El deployment de imagen '{DEPLOYMENT}' no existe en el recurso. "
                              "Despliégalo en Foundry o ajusta IMAGE_DEPLOYMENT en backend/.env.", 503)
        raise DesignError(f"Error del modelo de imagen ({r.status_code}): {detail}", 502)
    d = r.json()["data"][0]
    if d.get("b64_json"):
        return base64.b64decode(d["b64_json"])
    if d.get("url"):
        img = await _download(client, d["url"])
        if img:
            return img
    raise DesignError("El modelo de imagen no devolvió ninguna imagen.", 502)


def _session_store(session_id: str | None, image: bytes, products: list, size: str) -> str:
    sid = session_id or uuid.uuid4().hex
    while len(_SESSIONS) >= MAX_SESSIONS and sid not in _SESSIONS:
        _SESSIONS.pop(next(iter(_SESSIONS)))
    _SESSIONS[sid] = {"image": image, "products": products, "size": size}
    return sid


# ---------------------------------------------------------------- entrada principal
async def render(body: dict) -> dict:
    """{skus, room_image?, style?, instruction?, session_id?} -> {image_b64, products, ...}

    Con session_id + instruction y sin skus, itera sobre el render anterior de la sesión.
    """
    if not READY:
        raise DesignError("Diseño IA no configurado: faltan AZURE_OPENAI_ENDPOINT/KEY.", 503)
    if _by_sku is None:
        raise DesignError("Diseño IA no inicializado: falta design.configure(...).", 503)

    skus = [s for s in (body.get("skus") or []) if isinstance(s, str)]
    style = (body.get("style") or "").strip() or None
    instruction = (body.get("instruction") or "").strip() or None
    session_id = body.get("session_id")
    room_b64 = body.get("room_image")

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        # --- iteración sobre el render anterior ---
        prev = _SESSIONS.get(session_id) if session_id else None
        if instruction and not skus and prev:
            prompt = (f"Edita la imagen aplicando este cambio: {instruction}\n\n"
                      "Mantén el resto de la escena exactamente igual (productos, encuadre, "
                      "iluminación y materiales).")
            size = prev.get("size") or SIZE
            image = await _call_images(client, prompt, [prev["image"]], size)
            sid = _session_store(session_id, image, prev["products"], size)
            return {"session_id": sid, "image_b64": base64.b64encode(image).decode(),
                    "products": prev["products"], "skipped": []}

        # --- render nuevo ---
        seen, products, skipped = set(), [], []
        for sku in skus:
            if sku in seen:
                continue
            seen.add(sku)
            p = _by_sku.get(sku)
            if not p:
                skipped.append(sku)
            elif not _images.get(sku):
                skipped.append(sku)      # sin foto no hay referencia visual fiable
            else:
                products.append(p)
        if not products:
            raise DesignError("Ninguno de los productos elegidos tiene foto de catálogo.", 400)
        products = products[:MAX_REFS]

        downloads = await asyncio.gather(*[_download(client, _images[p["sku"]]) for p in products])
        pairs = [(p, img) for p, img in zip(products, downloads) if img]
        if not pairs:
            raise DesignError("No se pudieron descargar las fotos de los productos.", 502)
        products = [p for p, _ in pairs]
        refs = [img for _, img in pairs]

        room = _decode_upload(room_b64) if room_b64 else None
        if room:
            refs = [room] + refs

        prompt = _build_prompt(products, has_room=bool(room), style=style, instruction=instruction)
        size = _pick_size(room)
        image = await _call_images(client, prompt, refs, size)

    out_products = [_summary(p) for p in products]
    sid = _session_store(session_id, image, out_products, size)
    return {"session_id": sid, "image_b64": base64.b64encode(image).decode(),
            "products": out_products, "skipped": skipped}


# ---------------------------------------------------------------- renueva tu baño
# Foto del baño actual -> Claude (visión) detecta los elementos sustituibles y propone
# una búsqueda por cada uno -> el buscador REAL de la app devuelve los candidatos.

_ANALYZE_TOOL = {
    "name": "report_bathroom_items",
    "description": "Devuelve los elementos del baño detectados en la foto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "Lo que se ve, corto y concreto: "
                                                 "'lavabo de pedestal blanco'."},
                        "query": {"type": "string",
                                  "description": "Búsqueda en español para el catálogo Roca "
                                                 "que encuentre sustitutos: tipo de producto "
                                                 "+ atributos, p. ej. 'lavabo sobre encimera "
                                                 "blanco'."},
                    },
                    "required": ["label", "query"],
                },
            },
        },
        "required": ["items"],
    },
}

_ANALYZE_PROMPT = """Eres un experto en reformas de baño de Roca. Analiza la foto del baño \
del cliente y lista los elementos que podrían sustituirse por productos del catálogo Roca: \
lavabo, inodoro, bidé, bañera, plato de ducha, grifería (de lavabo, ducha o bañera), mueble \
de baño, espejo, mampara y accesorios. Solo elementos realmente visibles en la foto (máximo 6, \
los más relevantes primero). Para la query, describe el TIPO de producto y sus atributos \
visibles (montaje, color, forma), no la marca."""

MAX_ANALYZE_ITEMS = 6
RESULTS_PER_ITEM = 4


def _card_brief(card: dict) -> dict | None:
    """Tarjeta-modelo de search() -> producto plano para la UI (variante por defecto)."""
    variants = card.get("variants") or []
    v = variants[card.get("default", 0)] if variants else {}
    if not v.get("sku"):
        return None
    return {"sku": v["sku"], "title": card.get("title"), "image": v.get("image"),
            "price_rrp": v.get("price_rrp"), "finish": v.get("finish"),
            "collection": card.get("collection")}


async def analyze(body: dict) -> dict:
    """{room_image} -> {items: [{label, query, products: [...]}]}"""
    if _claude is None:
        raise DesignError("Análisis no disponible: falta CLAUDE_API_KEY en el backend.", 503)
    if _search is None:
        raise DesignError("Análisis no inicializado: falta design.configure(..., search).", 503)
    if not body.get("room_image"):
        raise DesignError("Falta la foto del baño (room_image).", 400)

    room = _decode_upload(body["room_image"])
    media = "image/png" if room[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    msg = await _claude.messages.create(
        model=CLAUDE_MODEL, max_tokens=1500,
        tools=[_ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "report_bathroom_items"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(room).decode()}},
            {"type": "text", "text": _ANALYZE_PROMPT},
        ]}],
    )
    detected = next((b.input for b in msg.content if b.type == "tool_use"), {}) or {}
    items = detected.get("items") or []

    out = []
    for it in items[:MAX_ANALYZE_ITEMS]:
        query = (it.get("query") or "").strip()
        label = (it.get("label") or query).strip()
        if not query:
            continue
        try:
            data = _search(q=query, limit=RESULTS_PER_ITEM)
        except Exception as e:  # noqa: BLE001 — un fallo de búsqueda no tumba el análisis
            print(f"[analyze] search fallo para {query!r}: {e!r}", flush=True)
            continue
        products = [b for b in (_card_brief(c) for c in data.get("results", [])) if b]
        out.append({"label": label, "query": query, "products": products})
    return {"items": out}
