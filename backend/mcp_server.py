"""MCP server del buscador Roca sobre stdio, para Claude Desktop u otros clientes MCP.

Expone la MISMA búsqueda que la app (main.search: Azure AI Search híbrido + refinado
LLM de la query + facetas/agrupación por modelo), de modo que el motor sigue siendo
la única fuente de verdad. No reimplementa nada de búsqueda.

Arranque (Claude Desktop lo lanza así, ver claude_desktop_config.json):
    backend/.venv/bin/python backend/mcp_server.py

Lección aprendida del prototipo anterior: los colores/materiales van en el TEXTO de la
query (la búsqueda semántica los resuelve de forma fiable); el filtro `finish` es exacto
y solo debe usarse con valores literales devueltos por las facetas.
"""
# El protocolo MCP viaja por stdout: cualquier print() de la app lo corrompería.
# Redirige TODOS los print() a stderr ANTES de importar la app (main/azure_search
# loguean con print en import y en cada búsqueda).
import builtins
import logging
import sys

_print = builtins.print
builtins.print = lambda *a, **k: _print(*a, **{**k, "file": sys.stderr})

# los SDKs de Azure/OpenAI loguean cada request a nivel INFO: fuera del stdio
for _n in ("azure", "azure.core.pipeline.policies.http_logging_policy", "httpx", "openai"):
    logging.getLogger(_n).setLevel(logging.WARNING)

import asyncio
import base64
import hashlib
import html
import os
import re
import tempfile
from collections import OrderedDict
from typing import Optional

import httpx
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP, Image

import main  # carga el catálogo en memoria y configura chat/design si hay credenciales

try:
    import chat  # solo para search_manual_impl (no usa la API de Anthropic)
except Exception:  # noqa: BLE001 — sin el paquete anthropic el resto sigue funcionando
    chat = None

mcp = FastMCP("roca-catalog")

SEARCH_TOP = 12       # tarjetas-modelo devueltas por búsqueda
FACET_TOP = 20        # valores por faceta (finish se devuelve entero: es el vocabulario)
VARIANT_FINISHES = 30  # acabados listados por tarjeta
IMAGE_TOP = 8          # máximo de imágenes embebidas en base64 por respuesta
IMAGE_WIDTH = 320      # miniatura Cloudinary (w_320,c_limit,q_auto ≈ decenas de KB)


# --- Imágenes como bloques nativos del protocolo -----------------------------------
# Con include_images=true la tool devuelve, además del JSON, bloques ImageContent MCP:
# el cliente (Claude Desktop) los renderiza directamente en el resultado de la tool.
# NUNCA volcamos base64 dentro del JSON: eso obligaría al modelo a transcribirlo a mano
# en un artifact (minutos de generación y base64 corrupto — lección aprendida).

_IMG_CACHE: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()  # url -> (bytes, formato)
_IMG_CACHE_MAX = 256


def _thumb_url(url: str) -> str:
    """Encadena una miniatura a la URL de Cloudinary: inserta 'w_320,c_limit,q_auto'
    tras las transformaciones existentes, justo antes del segmento de versión v<num>
    (…/upload/t_Download_72_dpi/v163…/x.jpg -> …/t_Download_72_dpi/w_320,c_limit,q_auto/v163…/x.jpg)."""
    head, marker, tail = url.partition("/upload/")
    if not tail:
        return url
    parts = tail.split("/")
    idx = 0
    while idx < len(parts) and not re.fullmatch(r"v\d+", parts[idx]):
        idx += 1
    parts.insert(idx, f"w_{IMAGE_WIDTH},c_limit,q_auto")
    return f"{head}{marker}{'/'.join(parts)}"


async def _fetch_thumb(client: httpx.AsyncClient, url: str) -> Optional[tuple[bytes, str]]:
    """Descarga la miniatura -> (bytes, formato). None si falla (fail-open: la tarjeta
    conserva su URL normal en el JSON)."""
    if not url:
        return None
    if url in _IMG_CACHE:
        _IMG_CACHE.move_to_end(url)
        return _IMG_CACHE[url]
    try:
        r = await client.get(_thumb_url(url))
        r.raise_for_status()
        mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0]
        res = (r.content, mime.split("/")[-1] or "jpeg")
    except Exception:  # noqa: BLE001 — sin red/404: seguimos sin esa imagen
        return None
    _IMG_CACHE[url] = res
    _IMG_CACHE.move_to_end(url)
    if len(_IMG_CACHE) > _IMG_CACHE_MAX:
        _IMG_CACHE.popitem(last=False)
    return res


async def _image_blocks(items: list[dict], max_n: int = IMAGE_TOP) -> list:
    """[etiqueta, Image, etiqueta, Image, …] para los primeros max_n items con imagen.
    Cada imagen va precedida de una línea que la identifica (modelo · título · precio)."""
    targets = [it for it in items if it.get("image")][:max_n]
    if not targets:
        return []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        thumbs = await asyncio.gather(*(_fetch_thumb(client, it["image"]) for it in targets))
    blocks = []
    for it, thumb in zip(targets, thumbs):
        if thumb is None:
            continue
        data, fmt = thumb
        label = " · ".join(str(x) for x in (it.get("model") or it.get("sku"),
                                            it.get("title"), it.get("finish")) if x)
        blocks.append(label)
        blocks.append(Image(data=data, format=fmt))
    return blocks


# --- Galería HTML autocontenida (para publicar como Artifact) ----------------------
# El MCP construye un HTML con las miniaturas YA embebidas como data URIs base64 y lo
# escribe a disco; la tool devuelve solo la RUTA. Así el base64 nunca entra en el
# contexto del modelo (no hay que transcribirlo) y el cliente solo tiene que abrir ese
# fichero con la herramienta Artifact. El sandbox del Artifact bloquea las URLs de
# Cloudinary pero SÍ permite data: URIs — por eso se embeben.

GALLERY_DIR = os.path.join(tempfile.gettempdir(), "roca_galleries")
GALLERY_MAX_CARDS = 24  # tope de tarjetas embebidas (controla el tamaño del fichero)

_GALLERY_STYLE = """
  :root {
    --ground:#EEF0F1; --surface:#FFFFFF; --ink:#181B1D; --muted:#6A7176;
    --hair:#DCE0E2; --accent:#155E63; --accent-soft:#E4EEEE; --chrome:#8A969C;
    --shadow:0 1px 2px rgba(20,30,35,.04), 0 8px 24px rgba(20,30,35,.05);
  }
  * { box-sizing:border-box; }
  .wrap { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:var(--ink); max-width:1120px; margin:0 auto; padding:2.5rem 1.5rem 4rem; }
  .eyebrow { font-size:.72rem; letter-spacing:.18em; text-transform:uppercase; color:var(--chrome);
    font-weight:600; margin:0 0 .5rem; }
  .h1 { font-size:clamp(1.7rem,3.5vw,2.4rem); line-height:1.1; font-weight:650; margin:0 0 .6rem;
    letter-spacing:-.02em; text-wrap:balance; }
  .lede { color:var(--muted); font-size:1.02rem; line-height:1.6; margin:0; max-width:60ch; }
  .stats { display:flex; gap:2rem; flex-wrap:wrap; margin:1.8rem 0 0; padding:1.1rem 0 0;
    border-top:1px solid var(--hair); }
  .stat .n { font-size:1.5rem; font-weight:650; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
  .stat .l { font-size:.78rem; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
  .filters { display:flex; gap:.5rem; flex-wrap:wrap; margin:2rem 0 1.6rem; }
  .chip { font:inherit; font-size:.85rem; font-weight:550; padding:.44rem .95rem; border-radius:999px;
    border:1px solid var(--hair); background:var(--surface); color:var(--muted); cursor:pointer;
    transition:all .15s ease; }
  .chip:hover { border-color:var(--chrome); color:var(--ink); }
  .chip[aria-pressed="true"] { background:var(--ink); border-color:var(--ink); color:#fff; }
  .chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:1.1rem; }
  .card { background:var(--surface); border:1px solid var(--hair); border-radius:14px; overflow:hidden;
    display:flex; flex-direction:column; transition:box-shadow .2s ease, transform .2s ease; }
  .card:hover { box-shadow:var(--shadow); transform:translateY(-2px); }
  .ph { background:linear-gradient(160deg,#F7F8F9,#EDEFF1); aspect-ratio:4/3; display:flex;
    align-items:center; justify-content:center; padding:1.1rem; }
  .ph img { max-width:100%; max-height:100%; object-fit:contain; mix-blend-mode:multiply; }
  .noimg { color:var(--chrome); font-size:.8rem; }
  .body { padding:1rem 1.1rem 1.15rem; display:flex; flex-direction:column; gap:.35rem; flex:1; }
  .tag { align-self:flex-start; font-size:.68rem; font-weight:650; letter-spacing:.06em; text-transform:uppercase;
    padding:.22rem .55rem; border-radius:6px; background:var(--accent-soft); color:var(--accent); }
  .card h2 { font-size:.98rem; font-weight:600; line-height:1.25; margin:.15rem 0 0; letter-spacing:-.01em; }
  .sub { font-size:.83rem; color:var(--muted); margin:0; line-height:1.4; }
  .foot { display:flex; align-items:baseline; justify-content:space-between; margin-top:auto; padding-top:.7rem; }
  .price { font-size:1.28rem; font-weight:680; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
  .sku { font-size:.72rem; color:var(--chrome); font-family:ui-monospace,"SF Mono",Menlo,monospace; letter-spacing:.02em; }
  .empty { display:none; color:var(--muted); padding:3rem 0; text-align:center; }
  .foothint { margin-top:2.5rem; font-size:.8rem; color:var(--chrome); text-align:center; }
"""

_GALLERY_SCRIPT = """
  const chips = document.querySelectorAll('.chip');
  const cards = document.querySelectorAll('.card');
  const empty = document.getElementById('empty');
  chips.forEach(c => c.addEventListener('click', () => {
    chips.forEach(x => x.setAttribute('aria-pressed', x === c ? 'true' : 'false'));
    const f = c.dataset.f;
    let shown = 0;
    cards.forEach(card => {
      const ok = f === 'all' || card.dataset.ci === f;
      card.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    empty.style.display = shown ? 'none' : 'block';
  }));
"""


def _price_label(card: dict) -> str:
    pr = card.get("price_range")
    p = card.get("price_rrp")
    if pr and pr[0] is not None and pr[1] is not None and pr[0] != pr[1]:
        return f"{pr[0]:0.2f}&nbsp;–&nbsp;{pr[1]:0.2f}&nbsp;€"
    if p is not None:
        return f"{p:0.2f}&nbsp;€"
    return "—"


def _gallery_html(cards: list[dict], title: str, uris: dict) -> str:
    """HTML autocontenido (data URIs ya resueltas en `uris`: url -> data:image/...)."""
    esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    colls: list[str] = []
    for c in cards:
        cl = c.get("collection")
        if cl and cl not in colls:
            colls.append(cl)
    show_filter = 2 <= len(colls) <= 8

    card_html = []
    for c in cards:
        ci = colls.index(c["collection"]) if c.get("collection") in colls else -1
        tag = c.get("collection") or c.get("category") or ""
        bits = [x for x in (c.get("finish"), c.get("subtitle")) if x]
        sub = " · ".join(bits)
        img = uris.get(c.get("image"))
        ph = (f'<img loading="lazy" src="{img}" alt="{esc(c.get("title"))}">'
              if img else '<div class="noimg">Sin imagen</div>')
        card_html.append(f'''      <article class="card" data-ci="{ci}">
        <div class="ph">{ph}</div>
        <div class="body">
          {f'<span class="tag">{esc(tag)}</span>' if tag else ''}
          <h2>{esc(c.get("title") or "")}</h2>
          {f'<p class="sub">{esc(sub)}</p>' if sub else ''}
          <div class="foot">
            <span class="price">{_price_label(c)}</span>
            <span class="sku">{esc(c.get("sku") or c.get("model") or "")}</span>
          </div>
        </div>
      </article>''')

    chips = ['<button class="chip" aria-pressed="true" data-f="all">Todos</button>']
    if show_filter:
        for i, cl in enumerate(colls):
            chips.append(f'<button class="chip" aria-pressed="false" data-f="{i}">{esc(cl)}</button>')
    filters = (f'  <div class="filters" role="group" aria-label="Filtrar por colección">\n    '
               + "\n    ".join(chips) + "\n  </div>") if show_filter else ""

    prices = [c["price_rrp"] for c in cards if c.get("price_rrp") is not None]
    stats = [f'<div class="stat"><div class="n">{len(cards)}</div><div class="l">modelos</div></div>']
    if prices:
        stats.append(f'<div class="stat"><div class="n">{min(prices):0.2f}&nbsp;€</div><div class="l">desde</div></div>')
        stats.append(f'<div class="stat"><div class="n">{max(prices):0.2f}&nbsp;€</div><div class="l">hasta</div></div>')

    # Fragmento (sin <!doctype>/<html>/<head>/<body>): la herramienta Artifact lo envuelve
    # en su propio esqueleto. Los navegadores también renderizan este fragmento si se abre
    # el fichero directamente (autodetectan UTF-8).
    return f'''<style>{_GALLERY_STYLE}</style>
<main class="wrap">
  <p class="eyebrow">Catálogo Roca · España</p>
  <h1 class="h1">{esc(title)}</h1>
  <p class="lede">{len(cards)} productos del catálogo Roca. Fotografía, acabado y PVPR de cada modelo.</p>
  <div class="stats">
    {''.join(stats)}
  </div>
{filters}
  <section class="grid" id="grid">
{chr(10).join(card_html)}
  </section>
  <p class="empty" id="empty">No hay productos de esta colección en la selección.</p>
  <p class="foothint">PVPR orientativo. Fuente: catálogo Roca España.</p>
</main>
<script>{_GALLERY_SCRIPT}</script>'''


async def _write_gallery(cards: list[dict], title: str) -> Optional[str]:
    """Descarga las miniaturas, las embebe en base64, escribe el HTML a disco y devuelve
    la ruta absoluta (o None si no hay nada que mostrar). Fail-open: las tarjetas sin
    imagen se muestran igualmente con un marcador."""
    cards = cards[:GALLERY_MAX_CARDS]
    if not cards:
        return None
    targets = [c for c in cards if c.get("image")]
    uris: dict[str, str] = {}
    if targets:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            thumbs = await asyncio.gather(*(_fetch_thumb(client, c["image"]) for c in targets))
        for c, thumb in zip(targets, thumbs):
            if thumb is None:
                continue
            data, fmt = thumb
            uris[c["image"]] = f"data:image/{fmt};base64,{base64.b64encode(data).decode()}"
    page = _gallery_html(cards, title, uris)
    os.makedirs(GALLERY_DIR, exist_ok=True)
    key = "|".join(str(c.get("sku") or c.get("model")) for c in cards) + "|" + title
    path = os.path.join(GALLERY_DIR, "roca_" + hashlib.md5(key.encode()).hexdigest()[:12] + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def _slim_card(card: dict) -> dict:
    """Tarjeta-modelo de main.search() -> resumen compacto para el LLM."""
    variants = card.get("variants") or []
    v = variants[card.get("default", 0)] if variants else {}
    prices = [x.get("price_rrp") for x in variants if x.get("price_rrp") is not None]
    return {
        "model": card.get("model"), "title": card.get("title"),
        "category": card.get("category"), "collection": card.get("collection"),
        "sku": v.get("sku"), "finish": v.get("finish"),
        "price_rrp": v.get("price_rrp"),
        "price_range": [min(prices), max(prices)] if prices else None,
        "dims": v.get("dims"), "image": v.get("image"),
        "finishes": [x.get("finish") for x in variants][:VARIANT_FINISHES],
        "variant_count": len(variants),
    }


def _slim_facets(facets: dict) -> dict:
    return {
        "category": (facets.get("category") or [])[:FACET_TOP],
        "collection": (facets.get("collection") or [])[:FACET_TOP],
        # el vocabulario COMPLETO de acabados del scope: son los únicos valores
        # válidos para el filtro exacto `finish`
        "finish": facets.get("finish") or [],
        "price": facets.get("price"),
        "dims": facets.get("dims"),
    }


@mcp.tool()
async def search_catalog(
    query: str,
    category: Optional[str] = None,
    collection: Optional[str] = None,
    subcategory: Optional[str] = None,
    finish: Optional[list[str]] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_length: Optional[float] = None,
    max_length: Optional[float] = None,
    min_width: Optional[float] = None,
    max_width: Optional[float] = None,
    min_height: Optional[float] = None,
    max_height: Optional[float] = None,
    top: int = SEARCH_TOP,
    as_gallery: bool = True,
    include_images: bool = False,
):
    """Busca productos en el catálogo Roca (España) por lenguaje natural + filtros opcionales.
    Búsqueda híbrida (semántica + keyword) con corrección de erratas; agrupa por modelo
    (cada tarjeta lista sus variantes de acabado) y devuelve facetas para refinar.

    IMPORTANTE: colores, materiales, formas y estilos van en el TEXTO de `query`
    (p. ej. "grifo lavabo negro mate"), NO como filtro: la búsqueda de texto los resuelve
    de forma fiable. El filtro `finish` es EXACTO y case-sensitive; úsalo solo con valores
    literales devueltos en `facets.finish` (p. ej. "Negro mate", no "negro").

    CÓMO PRESENTAR LOS RESULTADOS (por defecto, as_gallery=true):
    El resultado incluye `gallery_artifact_path`: la ruta a un HTML autocontenido con las
    fotos ya embebidas y filtros por colección. SIEMPRE debes publicar ese fichero con la
    herramienta Artifact (file_path = gallery_artifact_path) — esa galería visual es la
    forma estándar de mostrar productos al usuario. No listes los productos como texto ni
    como enlaces de imagen sueltos; acompaña la galería, como mucho, con un resumen breve.
    El fichero es autocontenido (data URIs base64), así que NO tienes que reconstruir nada
    en base64 tú: solo pásale la ruta a Artifact.

    Args:
        query: Descripción en lenguaje natural (español), p. ej. "grifo cocina negro mate".
        category: Nombre exacto de categoría (de facets.category) para filtrar.
        collection: Nombre exacto de colección/serie (de facets.collection).
        subcategory: Subcategoría exacta.
        finish: Acabados EXACTOS (valores literales de facets.finish).
        min_price: Precio mínimo en EUR (PVPR).
        max_price: Precio máximo en EUR (PVPR).
        min_length: Largo mínimo en MILÍMETROS (100 cm = 1000 mm).
        max_length: Largo máximo en mm.
        min_width: Ancho mínimo en mm.
        max_width: Ancho máximo en mm.
        min_height: Alto mínimo en mm.
        max_height: Alto máximo en mm.
        top: Máximo de tarjetas-modelo a devolver (por defecto 12).
        as_gallery: Si true (por defecto) genera la galería HTML y devuelve su ruta en
            `gallery_artifact_path` para publicarla como Artifact.
        include_images: Alternativa/complemento: adjunta las fotos como imágenes nativas
            MCP dentro del bloque del tool call (solo si necesitas verlas ahí).
    """
    data = main.search(
        q=query or "", limit=top, subcategory=subcategory,
        category=[category] if category else None,
        collection=[collection] if collection else None,
        finish=finish or None,
        min_price=min_price, max_price=max_price,
        min_length=min_length, max_length=max_length,
        min_width=min_width, max_width=max_width,
        min_height=min_height, max_height=max_height,
    )
    cards = [_slim_card(c) for c in data.get("results", [])]
    payload = {"total": data.get("total", 0), "results": cards,
               "facets": _slim_facets(data.get("facets") or {})}
    if as_gallery:
        path = await _write_gallery(cards, title=(query.strip() or "Catálogo Roca"))
        if path:
            payload["gallery_artifact_path"] = path
    if not include_images:
        return payload
    return [payload] + await _image_blocks(cards)


def _find_product(code: str):
    """Resuelve un código como SKU exacto o, si no, como código de modelo
    (devuelve su primera variante)."""
    code = (code or "").strip()
    p = main.BY_SKU.get(code)
    if p is None and code in main.BY_MODEL:
        p = main.BY_MODEL[code][0]
    return p


@mcp.tool()
async def get_product(code: str, as_gallery: bool = True, include_images: bool = False):
    """Ficha completa de un producto por SKU (p. ej. "A8S6090000") o código de modelo
    (p. ej. "5A3C9EC00"). Devuelve descripción, precio PVPR, dimensiones, todas las
    variantes de acabado (SKU + precio + imagen) y productos relacionados
    (compatibles / opcionales / incluidos / repuestos).

    CÓMO PRESENTARLO (por defecto, as_gallery=true): el resultado incluye
    `gallery_artifact_path`, un HTML autocontenido con una tarjeta por acabado disponible.
    SIEMPRE publícalo con la herramienta Artifact (file_path = gallery_artifact_path): es
    la forma estándar de mostrar el producto y sus variantes. El fichero ya trae las fotos
    embebidas en base64; solo pásale la ruta.

    include_images=true es una alternativa que adjunta las fotos como imágenes nativas MCP
    dentro del bloque del tool call."""
    p = _find_product(code)
    if p is None:
        return {"error": f"No existe producto con SKU o modelo '{code}'."}
    try:
        detail = main.product_detail(p["sku"])
    except HTTPException as e:
        return {"error": str(e.detail)}
    if as_gallery:
        # una tarjeta por variante de acabado (comparten título/colección de la ficha)
        variants = detail.get("variants") or [detail]
        cards = [{
            "title": detail.get("title"), "collection": detail.get("collection"),
            "category": detail.get("category"), "finish": v.get("finish"),
            "price_rrp": v.get("price_rrp"), "image": v.get("image"),
            "sku": v.get("sku"), "model": detail.get("model"),
        } for v in variants]
        path = await _write_gallery(cards, title=detail.get("title") or code)
        if path:
            detail = {**detail, "gallery_artifact_path": path}
    if not include_images:
        return detail
    # la ficha y sus variantes comparten el presupuesto de IMAGE_TOP miniaturas;
    # dedup por URL para no adjuntar dos veces la misma foto
    seen, items = set(), []
    for it in [detail] + list(detail.get("variants") or []):
        url = it.get("image")
        if url and url not in seen:
            seen.add(url)
            items.append(it)
    return [detail] + await _image_blocks(items)


@mcp.tool()
def compare_products(codes: list[str]) -> dict:
    """Compara 2 o más productos lado a lado por SKU o código de modelo. Devuelve una
    tabla alineada de atributos clave (categoría, colección, acabado, precio PVPR,
    dimensiones, nº de acabados disponibles) e imágenes."""
    products, missing = [], []
    for code in codes:
        p = _find_product(code)
        (products if p is not None else missing).append(p if p is not None else code)
    if len(products) < 2:
        return {"error": "Hacen falta al menos 2 códigos válidos para comparar.",
                "not_found": missing}
    rows = [
        ("Título", lambda p: p.get("title")),
        ("Categoría", lambda p: p.get("category")),
        ("Colección", lambda p: p.get("collection")),
        ("Acabado", lambda p: p.get("finish")),
        ("Precio PVPR (EUR)", lambda p: p.get("price_rrp")),
        ("Dimensiones (mm)", main.dims_str),
        ("Acabados disponibles", lambda p: len(main.BY_MODEL.get(p.get("model"), [])) or 1),
    ]
    return {
        "skus": [p["sku"] for p in products],
        "rows": [{"label": lab, "values": [fn(p) for p in products]} for lab, fn in rows],
        "images": [{"sku": p["sku"], "image": main.IMAGES.get(p["sku"])} for p in products],
        "not_found": missing,
    }


@mcp.tool()
def search_manual(sku: str, doctype: str, question: str = "", model: str = "") -> dict:
    """Consulta la documentación de UN producto concreto: manual de usuario, guía de
    instalación o ficha técnica. Con `question` devuelve los fragmentos relevantes;
    sin ella devuelve el documento COMPLETO (todas sus páginas). Ambos incluyen la
    URL del PDF para citarla como enlace.

    Args:
        sku: SKU del producto, p. ej. "A812429000" o "8S6090000".
        doctype: "UserManual" (uso/limpieza/mantenimiento), "InstallationManual"
            (instalación/montaje/fijación) o "TechnicalFactSheet" (ficha técnica).
        question: Pregunta concreta en lenguaje natural; vacía = documento completo.
        model: Modelo del catálogo tal cual (puede llevar puntos, p. ej. "212106..1").
    """
    if chat is None:
        return {"error": "Módulo de manuales no disponible en este entorno."}
    try:
        return chat.search_manual_impl(sku, doctype, question or None, model or None)
    except Exception as e:  # noqa: BLE001 — índice de manuales caído: error legible
        return {"error": f"No se pudo consultar la documentación: {e}"}


@mcp.tool()
def find_stores(lat: float, lon: float, limit: int = 8,
                exposition_only: bool = False) -> dict:
    """Puntos de venta físicos Roca en España ordenados por cercanía a unas coordenadas
    (compra offline). Con exposition_only=true, solo distribuidores con exposición."""
    return main.suppliers_nearby(lat=lat, lon=lon, limit=limit,
                                 exposition_only=exposition_only)


if __name__ == "__main__":
    mcp.run()
