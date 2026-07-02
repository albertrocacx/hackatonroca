"""Chat IA opcional sobre los resultados del catálogo.

Transporte-agnóstico: `stream_turn` es un generador asíncrono que produce eventos
{type: text|tool|grid|done|error, ...}; la ruta /api/chat de main.py los serializa
como NDJSON (una línea JSON por evento) sobre un StreamingResponse.

La recuperación va SIEMPRE a través de la búsqueda propia de la app (inyectada con
`configure`), de modo que el motor de búsqueda sigue siendo la única fuente de verdad:
este módulo no reimplementa nada de búsqueda.

Auth: usa la API de Anthropic desplegada en Azure AI Foundry. Necesita CLAUDE_API_KEY
en backend/.env (o en el entorno). Si falta, stream_turn emite un evento de error en
lugar de llamar a Claude. La API es stateless: el historial por sesión se guarda en
memoria en este proceso (suficiente para la demo; se pierde al reiniciar).
"""
import inspect
import json
import os
import types as _types
import uuid
from typing import Union, get_args, get_origin

import anthropic
from anthropic import AsyncAnthropicFoundry


# --- credenciales: env o backend/.env (mini-loader para no depender de python-dotenv) ---
def _load_env():
    if os.environ.get("CLAUDE_API_KEY"):
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
API_KEY = os.environ.get("CLAUDE_API_KEY")
ENDPOINT = os.environ.get(
    "CLAUDE_ENDPOINT", "https://hackathon-us-roca-resource.services.ai.azure.com/anthropic")
MODEL = os.environ.get("CLAUDE_DEPLOYMENT", "claude-sonnet-5")

_client = AsyncAnthropicFoundry(api_key=API_KEY, base_url=ENDPOINT) if API_KEY else None

GRID_TOP = 24     # productos que se vuelcan a la parrilla
MODEL_TOP = 10    # productos que se pasan al modelo (resumir, no listar todo)
MAX_TURNS = 12    # tope de iteraciones del bucle de tools por mensaje de usuario
MAX_TOKENS = 4096
MAX_SESSIONS = 200  # historiales en memoria; al superarlo se descarta el más antiguo


# --- inyección de la búsqueda (el handler /search de main.py) ---
# El schema de la tool se DERIVA de la firma de search(): cualquier filtro que acepte
# (hoy o en el futuro) queda disponible para el agente sin tocar este archivo.
_search_fn = None
_TOOLS = None                     # definiciones anthropic (search_catalog + search_manual)
SEARCH_PARAMS: set[str] = set()   # todos los params de search()
LIST_PARAMS: set[str] = set()     # los que son listas (deben enviarse como lista)
INTERNAL_PARAMS = {"q", "limit", "include_spare", "auto"}  # no son filtros de usuario

_SESSIONS: dict[str, list] = {}   # session_id -> messages (historial API)


def _unwrap_optional(ann):
    """Optional[X] / X|None -> X."""
    if get_origin(ann) in (Union, getattr(_types, "UnionType", None)):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return ann


def _is_list_ann(ann) -> bool:
    inner = _unwrap_optional(ann)
    return get_origin(inner) is list or inner is list


def _json_type(ann) -> dict:
    inner = _unwrap_optional(ann)
    if get_origin(inner) is list or inner is list:
        return {"type": "array", "items": {"type": "string"}}
    if inner in (int, float):
        return {"type": "number"}
    if inner is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def _describe(name: str) -> str:
    """Descripción heurística por nombre de parámetro (las unidades también van en el system prompt)."""
    if name in ("category", "collection", "subcategory"):
        return f"Filtra por {name} exacta (opcional)."
    if name == "finish":
        return "Acabados/colores exactos a filtrar (opcional)."
    if name == "sort":
        return ("Orden de resultados si el usuario lo pide: 'price_asc', 'price_desc', "
                "'alpha_asc' o 'alpha_desc'. Omítelo para orden por relevancia.")
    bound = "mínimo" if name.startswith("min") else "máximo" if name.startswith("max") else ""
    if "price" in name:
        return f"Precio {bound} en EUR (opcional).".replace("  ", " ")
    for dim, lab in (("length", "Largo"), ("width", "Ancho"), ("height", "Alto")):
        if dim in name:
            return f"{lab} {bound} en MILÍMETROS (opcional).".replace("  ", " ")
    return f"Filtro '{name}' (opcional)."


def _brief_from_card(c: dict) -> dict:
    """Los resultados son tarjetas-modelo (agrupadas por modelo con variantes);
    resume cada una por su variante por defecto."""
    variants = c.get("variants") or []
    v = variants[c.get("default", 0)] if variants else {}
    return {"sku": v.get("sku"), "title": c.get("title"), "collection": c.get("collection"),
            "finish": v.get("finish"), "price_rrp": v.get("price_rrp"),
            # "OnlineFrom" => comprable online (carrito); "PVPR" => solo distribuidor
            "price_type": v.get("price_type")}


# --- Tool de documentación: preguntas sobre el manual/guía de UN producto (SKU) concreto ---
# Recupera del índice de manuales (search_ocr) filtrando por sku + doctype, y devuelve
# fragmentos + pdf_url (firmada con SAS) para que Claude cite un enlace clicable.
MANUAL_TOP = 6
DOCTYPE_MAP = {
    "usermanual": "UserManual", "user_manual": "UserManual", "manual_usuario": "UserManual",
    "manual_de_usuario": "UserManual", "usuario": "UserManual", "user": "UserManual",
    "installationmanual": "InstallationManual", "installation_manual": "InstallationManual",
    "installation_guide": "InstallationManual", "guia_instalacion": "InstallationManual",
    "guía_instalación": "InstallationManual", "instalacion": "InstallationManual",
    "installation": "InstallationManual",
    "technicalfactsheet": "TechnicalFactSheet", "technical_fact_sheet": "TechnicalFactSheet",
    "ficha_tecnica": "TechnicalFactSheet", "ficha": "TechnicalFactSheet",
}

_MANUAL_TOOL = {
    "name": "search_manual",
    "description": ("Consulta la documentación de UN producto concreto: manual de usuario, guía "
                    "de instalación o ficha técnica. Dos modos: CON `question` devuelve los "
                    "fragmentos relevantes para responderla; SIN `question` devuelve el documento "
                    "COMPLETO (todas sus páginas) para entregarlo entero. Ambos incluyen la URL "
                    "del PDF para citarla como enlace."),
    "input_schema": {
        "type": "object",
        "properties": {
            "sku": {"type": "string",
                    "description": "SKU/código del producto (carpeta del manual), p.ej. '8S6090000'."},
            "model": {"type": "string",
                      "description": "Modelo del catálogo TAL CUAL, conservando sus puntos comodín "
                                     "si los tiene (p.ej. '212106..1'). Pásalo siempre que lo "
                                     "conozcas (p.ej. del producto seleccionado en [contexto])."},
            "doctype": {"type": "string",
                        "enum": ["UserManual", "InstallationManual", "TechnicalFactSheet"],
                        "description": "Documento a consultar: UserManual (uso/mantenimiento), "
                                       "InstallationManual (instalación/montaje) o TechnicalFactSheet (ficha técnica)."},
            "question": {"type": "string",
                         "description": "Pregunta concreta del usuario. OMÍTELA si pide el "
                                        "documento entero (p.ej. 'dame el manual')."},
        },
        "required": ["sku", "doctype"],
    },
}


def _strip_leading_char(sku: str) -> str:
    """Quita el primer elemento (letra o dígito) empezando por la izquierda.
    '18S6090000' -> '8S6090000' -> 'S6090000' -> ... Si está vacío, lo devuelve intacto."""
    return sku[1:] if sku else sku


def _sku_candidates(sku: str, model: str | None) -> list[str]:
    """Identificadores a probar contra la carpeta del manual, en orden y sin duplicados:
    el SKU tal cual, sin su primer elemento (prefijo de mercado: 'A812429000' ->
    '812429000'), el modelo del catálogo tal cual (conserva los '..' comodín interiores,
    p.ej. '212106..1') y el modelo sin puntos finales ('851986...' -> '851986')."""
    sku = (sku or "").strip()
    model = (model or "").strip()
    out: list[str] = []
    for cand in (sku, _strip_leading_char(sku), model, model.rstrip(".")):
        if cand and cand not in out:
            out.append(cand)
    return out


MAX_PAGE_CHARS = 4000   # texto por página en modo documento completo


def search_manual_impl(sku: str, doctype: str, question: str | None = None,
                       model: str | None = None, top: int = MANUAL_TOP) -> dict:
    """Consulta del índice de manuales por sku+doctype. Devuelve un payload citable
    (con pdf_url firmada). Aislada para poder testearla sin el LLM.

    Dos modos: con `question` recupera los fragmentos más relevantes (RAG); sin ella
    recupera el documento COMPLETO (todas sus páginas en orden, agrupadas por PDF).

    Resolución del identificador: se prueban en orden los candidatos de
    `_sku_candidates` (SKU tal cual, sin el primer elemento, modelo, modelo sin puntos
    finales) hasta que uno devuelva resultados. Cubre el prefijo de mercado
    ('A812429000' -> '812429000') y las carpetas nombradas por modelo ('212106..1')."""
    import search_ocr  # búsqueda con filtros de metadatos (índice de manuales)
    dt = DOCTYPE_MAP.get((doctype or "").strip().lower().replace(" ", "_"), (doctype or "").strip())
    q = (question or "").strip()
    cands = _sku_candidates(sku, model)
    if not cands:
        return {"error": "Falta el sku (o el model) del producto."}

    sku_used, hits, full = cands[0], [], {"documents": [], "truncated": False}
    for cand in cands:
        if q:
            hits = search_ocr.search_ocr(q, sku=cand, doctype=dt or None, top=top)
            found = bool(hits)
        else:
            full = search_ocr.fetch_manual(cand, doctype=dt or None)
            found = bool(full["documents"])
        if found:
            if cand != cands[0]:
                print(f"[manual] sku={cands[0]!r} sin resultados -> encontrado como {cand!r}", flush=True)
            sku_used = cand
            break

    if q:
        results = [{"sku": h.get("sku"), "doctype": h.get("doctype"), "pdf_url": h.get("pdf_url"),
                    "text": (h.get("text") or "")[:1200]} for h in hits]
        payload = {"mode": "qa", "sku": sku_used, "doctype": dt,
                   "count": len(results), "results": results}
    else:
        docs = full["documents"]
        for d in docs:
            d["pages"] = [{**p, "text": (p.get("text") or "")[:MAX_PAGE_CHARS]}
                          for p in d["pages"]]
        payload = {"mode": "full_document", "sku": sku_used, "doctype": dt,
                   "count": len(docs), "documents": docs}
        if full.get("truncated"):
            payload["truncated"] = True
    if sku_used != cands[0]:   # informa a Claude de con qué identificador se encontró
        payload["sku_requested"] = cands[0]
    return payload


# --- Tool de guías de estilo: consejos de estilo/decoración/tendencias (RocaLife) ---
# Recupera del índice de artículos (search_style) por búsqueda semántica y devuelve
# fragmentos + título + url PÚBLICA (roca.es) para que Claude cite el artículo como enlace.
# Mismo patrón que search_manual, pero sobre el corpus editorial (no manuales de producto).
STYLE_TOP = 6

_STYLE_TOOL = {
    "name": "search_style_guide",
    "description": ("Consulta las GUÍAS DE ESTILO de Roca: artículos editoriales de RocaLife "
                    "(ideas de decoración, tendencias, cómo elegir, combinar acabados y colores, "
                    "reformas, estilos de baño y cocina). Úsala cuando el usuario pida CONSEJO "
                    "de estilo/decoración/inspiración/tendencias o pregunte por un artículo. NO "
                    "es para datos de producto (usa search_catalog) ni para manuales/instalación "
                    "(usa search_manual). Devuelve fragmentos relevantes y la URL pública del "
                    "artículo para citarla como enlace."),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Consulta en lenguaje natural (español), p.ej. 'ideas para "
                                     "un baño pequeño' o 'cómo combinar acabados en oro'."},
            "keywords": {"type": "array", "items": {"type": "string"},
                         "description": "Opcional: filtra por temas/etiquetas del artículo (en "
                                        "inglés, p.ej. 'small bathrooms', 'colours', 'styles', "
                                        "'renovations'). Normalmente NO hace falta."},
        },
        "required": ["query"],
    },
}


def search_style_guide_impl(query: str, keywords=None, top: int = STYLE_TOP) -> dict:
    """Consulta del índice de guías de estilo. Devuelve un payload citable (título + url
    pública + fragmento). Aislada para poder testearla sin el LLM."""
    import search_style  # búsqueda híbrida sobre el índice de artículos de estilo
    q = (query or "").strip()
    if not q:
        return {"error": "Falta la consulta (query)."}
    hits = search_style.search_style(q, keywords=keywords or None, top=top)
    results = [{"title": h.get("title"), "url": h.get("url"), "slug": h.get("slug"),
                "keywords": h.get("keywords"), "text": (h.get("text") or "")[:1200]}
               for h in hits]
    return {"query": q, "count": len(results), "results": results}


# --- Tools de UI: no calculan nada, abren vistas en el frontend (compra guiada) ---
# stream_turn emite un evento ({type: suppliers|product}) que el cliente traduce en
# openLocalSuppliers / openProduct; el tool_result solo confirma la acción al modelo.
_SUPPLIERS_TOOL = {
    "name": "find_local_suppliers",
    "description": ("Abre para el usuario el buscador de distribuidores Roca cercanos "
                    "(geolocalización + mapa + datos REALES de puntos de venta ordenados por "
                    "distancia). Úsala siempre que pregunte DÓNDE COMPRAR un producto, por "
                    "tiendas, distribuidores o puntos de venta cercanos. Nunca inventes "
                    "tiendas, direcciones ni teléfonos: los muestra esta herramienta."),
    "input_schema": {
        "type": "object",
        "properties": {"product": {
            "type": "string",
            "description": "Nombre o referencia del producto sobre el que pregunta el "
                           "usuario, si se conoce (se mostrará como encabezado del "
                           "buscador). Opcional."}},
        "required": [],
    },
}

_SHOW_PRODUCT_TOOL = {
    "name": "show_product",
    "description": ("Abre para el usuario la ficha de un producto concreto: la vista con sus "
                    "datos, variantes y el BOTÓN DE COMPRA («Comprar online» si es online -> "
                    "añade al carrito; «Dónde comprar» si es solo distribuidor). Úsala cuando "
                    "el usuario quiera comprar, añadir al carrito o ver el detalle de UN "
                    "producto identificado por su SKU (tómalo de los resultados de "
                    "search_catalog o del [contexto])."),
    "input_schema": {
        "type": "object",
        "properties": {"sku": {
            "type": "string",
            "description": "SKU exacto del producto a abrir (de un resultado previo o del "
                           "contexto de SKUs mostrados)."}},
        "required": ["sku"],
    },
}


def configure(search_fn):
    """main.py inyecta su `search`. Construimos aquí (con la firma ya conocida) las
    definiciones de tools para la Messages API."""
    global _search_fn, _TOOLS, SEARCH_PARAMS, LIST_PARAMS
    _search_fn = search_fn
    sig = inspect.signature(search_fn)
    SEARCH_PARAMS = set(sig.parameters)
    LIST_PARAMS = {n for n, p in sig.parameters.items() if _is_list_ann(p.annotation)}

    props = {"query": {"type": "string",
                       "description": "Texto de búsqueda en lenguaje natural (español)."}}
    for name, p in sig.parameters.items():
        if name in INTERNAL_PARAMS:
            continue
        props[name] = {**_json_type(p.annotation), "description": _describe(name)}

    catalog_tool = {
        "name": "search_catalog",
        "description": ("Busca productos en el catálogo Roca por lenguaje natural + filtros "
                        "opcionales. Los resultados se muestran automáticamente en la parrilla "
                        "del usuario."),
        "input_schema": {"type": "object", "properties": props, "required": ["query"]},
    }
    _TOOLS = [catalog_tool, _MANUAL_TOOL, _STYLE_TOOL, _SUPPLIERS_TOOL, _SHOW_PRODUCT_TOOL]


def _search_kwargs(a: dict, limit: int) -> dict:
    """Argumentos de la tool -> kwargs de search(). `query` mapea a `q`; se pasa cualquier
    filtro conocido no vacío (las listas se envuelven en lista)."""
    out = {"q": a.get("query", "") or "", "limit": limit}
    for key, val in a.items():
        if key == "query" or val in (None, "", []):
            continue
        if key not in SEARCH_PARAMS:
            continue
        out[key] = [val] if (key in LIST_PARAMS and not isinstance(val, list)) else val
    return out


def _run_search(**kwargs):
    if _search_fn is None:
        raise RuntimeError("chat.configure(search_fn) no ha sido llamado")
    return _search_fn(**kwargs)


SYSTEM = """Eres el asistente del catálogo Roca para el mercado español: un experto de \
showroom que ayuda a encontrar productos de baño y cocina (lavabos, inodoros, bidés, \
platos de ducha, bañeras, grifería, mobiliario, accesorios).

Herramientas (úsalas, nunca inventes productos, precios, tiendas ni instrucciones):
- search_catalog: buscar productos. Los resultados se enseñan TAMBIÉN al usuario en una \
parrilla automáticamente, así que no listes todos: resume y señala la parrilla ("Aquí \
tienes algunas opciones a la izquierda").
- search_manual: consultar la documentación de UN producto concreto (manual de usuario, \
guía de instalación o ficha técnica): entrega el documento completo o responde preguntas.
- search_style_guide: consultar las GUÍAS DE ESTILO de Roca (artículos de RocaLife): \
consejos de decoración, tendencias, cómo combinar colores y acabados, ideas para reformas \
y estilos de baño/cocina. Úsala para inspiración/consejo de estilo o si preguntan por un \
artículo; NO para datos de producto (search_catalog) ni manuales (search_manual).
- find_local_suppliers: abre el buscador de distribuidores Roca cercanos (mapa + datos \
reales por distancia). Úsala para la compra OFFLINE / física: cuando pregunte por tiendas, \
distribuidores o puntos de venta CERCANOS ("¿dónde compro esto cerca de mí?"), o cuando el \
producto sea solo de distribuidor (price_type "PVPR"). Pasa el nombre del producto en \
`product` si lo conoces. NUNCA inventes direcciones ni teléfonos: para eso está esta tool. \
Tras llamarla, invita a permitir la ubicación en el buscador que se ha abierto.
- show_product: abre la FICHA de un producto por su `sku`, con su botón de compra. Úsala \
para la compra ONLINE: cuando el usuario quiera comprar, añadir al carrito o ver el detalle \
de un producto concreto y sea comprable online (price_type "OnlineFrom"). El botón "Comprar \
online" de esa ficha añade el producto al carrito. Coge el SKU de search_catalog o del \
[contexto]; si no tienes un SKU, busca primero.

REGLA DE COMPRA: usa el price_type del producto (viene en los resultados de search_catalog): \
"OnlineFrom" -> comprable online, usa show_product; "PVPR" -> solo distribuidor, usa \
find_local_suppliers. Si el usuario pide explícitamente tienda física, usa siempre \
find_local_suppliers aunque sea online. NUNCA respondas "no tengo datos de dónde comprar": \
llama a la herramienta que corresponda.

REGLA DE BÚSQUEDA (impórtate mucho):
- Puedes hacer VARIAS llamadas a search_catalog en el mismo turno para refinar: probar \
sinónimos, ajustar filtros, corregir una búsqueda que dio 0 o resultados poco relevantes. \
El usuario NO ve las búsquedas intermedias: su parrilla mostrará SOLO tu ÚLTIMA búsqueda \
con resultados. Por eso, haz que tu ÚLTIMA búsqueda sea la que mejor responde a lo que \
pidió (si refinando empeoras, repite al final la mejor búsqueda que encontraste).
- Empieza por la consulta MÁS SIMPLE y directa posible: normalmente las palabras del \
usuario tal cual, pero CORRIGIENDO erratas evidentes (p. ej. "lababo" -> "lavabo", \
"inodoto" -> "inodoro", "grifo de cosina" -> "grifo cocina"). Si escribe "lavabos", busca \
`query="lavabos"`. Refina solo si hace falta.
- Colores, materiales, formas y descripciones (p. ej. "blanco", "mate", "redondo") van \
en el TEXTO de `query`, NO como filtro (la búsqueda de texto los encuentra de forma \
fiable; los filtros exactos como `finish` distinguen mayúsculas y suelen fallar). \
Ej.: "lavabos blancos" -> `query="lavabos blancos"`.
- Usa filtros estructurados SOLO cuando el usuario los pida y sean numéricos o exactos: \
precio (min_price/max_price en EUR) y dimensiones (min/max_length/width/height en \
MILÍMETROS; convierte cm/m a mm, 100 cm = 1000 mm). Ejemplos: "lavabos blancos por menos \
de 200 €" -> `query="lavabos blancos", max_price=200`; "inodoros de alto máx 100 cm" -> \
`query="inodoros", max_height=1000`.

BÚSQUEDA POR IMAGEN: si el [contexto] trae una "búsqueda por imagen previa", el usuario \
subió foto(s) y esos SKUs son los productos del catálogo visualmente más parecidos (el \
primero es el candidato más probable). PARTE DE ELLOS: cuando diga "esto", "el de la \
foto" o pida recambios/manual/compra sin nombrar producto, asume que habla del primer \
match (o del de la foto que diga, en modo por-foto). Preséntalos con naturalidad ("En tus \
fotos he identificado..."), usa sus SKUs directamente con show_product/search_manual, y \
si quiere variantes o alternativas (otro acabado, otro precio) busca con search_catalog \
partiendo del título del match. No repitas la búsqueda visual: ya está hecha.

DOCUMENTACIÓN DE UN PRODUCTO (search_manual):
- Úsala cuando el usuario pida el manual/guía/ficha de un producto o pregunte cómo usarlo, \
instalarlo, montarlo o mantenerlo.
- Identificar el producto: por SKU explícito, o por el "producto seleccionado" de \
[contexto] cuando diga "este producto", "el que estoy viendo" o pida el manual sin \
especificar cuál: en ese caso usa su SKU directamente SIN pedírselo, y pasa también su \
modelo en `model` (tal cual, con sus puntos si los tiene). Si no hay ni SKU ni producto \
seleccionado, pídeselo.
- `doctype`: UserManual (uso, limpieza, mantenimiento, garantía) o InstallationManual \
(instalación, montaje, medidas, fijación, conexiones); TechnicalFactSheet solo si piden \
la ficha técnica. Si NO tienes claro cuál de los dos quiere, PREGÚNTASELO antes de \
llamar a la tool (no adivines).
- DOS MODOS de uso:
  · Pide el documento entero ("dame el manual", "enséñame la guía de instalación") -> \
llama SIN `question`: recibirás el documento completo por páginas. PRESÉNTALO ENTERO y \
fiel al contenido, estructurado con encabezados y listas (incluye SIEMPRE las advertencias \
de seguridad). Si hay versiones en varios idiomas, presenta la del idioma del usuario y \
menciona las demás. No omitas secciones; condensa solo lo repetido.
  · Pregunta concreta ("¿cómo se limpia?", "¿qué medidas de instalación tiene?") -> llama \
CON `question`: responde SOLO con la información de los fragmentos devueltos.
- En AMBOS modos cita SIEMPRE la fuente como ENLACE MARKDOWN CLICABLE al `pdf_url`, \
p. ej. "[Manual de usuario · SKU 812429000](https://…)" (uno por PDF citado). Si no hay \
resultados o los fragmentos no contienen la respuesta, dilo claramente (no inventes).

GUÍAS DE ESTILO (search_style_guide):
- Úsala cuando el usuario pida CONSEJO de estilo/decoración, inspiración, tendencias, cómo \
combinar colores o acabados, ideas para una reforma o cómo ambientar un espacio (p. ej. \
"ideas para un baño pequeño", "¿qué combina con acabados dorados?", "tendencias en negro"). \
Pasa la consulta del usuario en `query`; normalmente no hace falta `keywords`.
- Responde con los consejos de los fragmentos devueltos (no inventes) y CITA SIEMPRE el \
artículo como ENLACE MARKDOWN CLICABLE a su `url`, p. ej. "[Reflejos dorados en el baño](https://…)". \
Puedes combinarla con search_catalog para sugerir productos concretos acordes al consejo.

Comportamiento:
- Responde en el idioma del usuario (español por defecto). Sé concreto y breve.
- Los precios son PVPR en EUR. Cita atributos reales; si falta un dato, dilo.
- Cuando el usuario diga "estos", "los que se ven", usa el contexto [contexto] de SKUs \
mostrados. Ofrece afinar o comparar."""


def _fmt_num(v) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_cm(mm) -> str:
    try:
        return f"{float(mm) / 10:g} cm"
    except (TypeError, ValueError):
        return f"{mm} mm"


def _search_label(inp: dict) -> str:
    """Etiqueta legible de una búsqueda para el estado del chat:
    'Buscando lavabos negros por menos de 200 € · alto hasta 100 cm'."""
    parts = [f"Buscando {inp.get('query') or 'productos'}"]
    lo, hi = inp.get("min_price"), inp.get("max_price")
    if lo and hi:
        parts.append(f"entre {_fmt_num(lo)} y {_fmt_num(hi)} €")
    elif hi:
        parts.append(f"por menos de {_fmt_num(hi)} €")
    elif lo:
        parts.append(f"por más de {_fmt_num(lo)} €")
    extras = []
    for dim, lab in (("length", "largo"), ("width", "ancho"), ("height", "alto")):
        mn, mx = inp.get(f"min_{dim}"), inp.get(f"max_{dim}")
        if mn and mx:
            extras.append(f"{lab} entre {_fmt_cm(mn)} y {_fmt_cm(mx)}")
        elif mx:
            extras.append(f"{lab} hasta {_fmt_cm(mx)}")
        elif mn:
            extras.append(f"{lab} desde {_fmt_cm(mn)}")
    for k in ("collection", "category", "subcategory"):
        if inp.get(k):
            extras.append(f"en {inp[k]}")
    fin = inp.get("finish")
    if fin:
        extras.append(f"acabado {', '.join(fin) if isinstance(fin, list) else fin}")
    sort_labels = {"price_asc": "por precio ascendente", "price_desc": "por precio descendente",
                   "alpha_asc": "por orden alfabético", "alpha_desc": "por orden alfabético inverso"}
    if inp.get("sort") in sort_labels:
        extras.append(sort_labels[inp["sort"]])
    label = " ".join(parts)
    return f"{label} · {' · '.join(extras)}" if extras else label


_DOCTYPE_LABEL = {"UserManual": "el manual de usuario",
                  "InstallationManual": "la guía de instalación",
                  "TechnicalFactSheet": "la ficha técnica"}
_DOCTYPE_LABEL_FULL = {"UserManual": "el manual de usuario completo",
                       "InstallationManual": "la guía de instalación completa",
                       "TechnicalFactSheet": "la ficha técnica completa"}


def _manual_label(inp: dict) -> str:
    whole = not (inp.get("question") or "").strip()
    labels = _DOCTYPE_LABEL_FULL if whole else _DOCTYPE_LABEL
    doc = labels.get(inp.get("doctype"), "la documentación")
    verb = "Recuperando" if whole else "Consultando"
    sku = inp.get("sku") or inp.get("model")
    return f"{verb} {doc} del producto {sku}" if sku else f"{verb} {doc}"


def _style_label(inp: dict) -> str:
    q = (inp.get("query") or "").strip()
    return f"Buscando guías de estilo sobre {q}" if q else "Buscando guías de estilo"


def _tool_label(name: str, inp: dict) -> str:
    if name == "search_manual":
        return _manual_label(inp)
    if name == "search_style_guide":
        return _style_label(inp)
    if name == "find_local_suppliers":
        prod = inp.get("product")
        return f"Buscando distribuidores cercanos para {prod}" if prod \
            else "Buscando distribuidores cercanos"
    if name == "show_product":
        sku = inp.get("sku")
        return f"Abriendo la ficha del producto {sku}" if sku else "Abriendo la ficha del producto"
    return _search_label(inp)


def _build_prompt(text: str, view: dict | None) -> str:
    if not view:
        return text
    bits = []
    if view.get("query"):
        bits.append(f"búsqueda actual: {view['query']!r}")
    if view.get("visible"):
        bits.append(f"SKUs mostrados: {view['visible'][:12]}")
    sel = view.get("selected") or {}
    if sel.get("sku"):
        s = f"producto seleccionado (último abierto): SKU {sel['sku']}"
        if sel.get("model"):
            s += f", modelo {sel['model']}"
        if sel.get("title"):
            s += f" — «{sel['title']}»"
        bits.append(s)
    img = view.get("image_search") or {}
    groups = [g for g in (img.get("groups") or []) if g.get("matches")]
    if groups:
        # mode 'same' = todas las fotos son el mismo producto (un solo ranking fusionado);
        # 'distinct' = cada foto es un producto distinto (un ranking por foto).
        distinct = img.get("mode") == "distinct"
        parts = []
        for g in groups:
            ms = "; ".join(f"{m['sku']} «{m.get('title') or '?'}»"
                           for m in g["matches"][:6] if m.get("sku"))
            parts.append(f"foto {g.get('photo')}: {ms}" if distinct else ms)
        s = (f"búsqueda por imagen previa ({img.get('photos', 1)} foto(s), "
             f"{'un producto distinto por foto' if distinct else 'mismo producto'})")
        if img.get("refine"):
            s += f", refinada con el texto {img['refine']!r}"
        s += " — productos visualmente más parecidos, mejor match primero: " + " || ".join(parts)
        bits.append(s)
    return f"[contexto] {' | '.join(bits)}\n\n{text}" if bits else text


def _session(session_id: str | None) -> tuple[str, list]:
    """Recupera (o crea) el historial de la sesión, con poda del más antiguo."""
    if session_id and session_id in _SESSIONS:
        return session_id, _SESSIONS[session_id]
    sid = uuid.uuid4().hex
    while len(_SESSIONS) >= MAX_SESSIONS:
        _SESSIONS.pop(next(iter(_SESSIONS)))
    _SESSIONS[sid] = []
    return sid, _SESSIONS[sid]


async def stream_turn(text: str, session_id: str | None = None, view: dict | None = None):
    """Genera eventos de UI: {type: text|tool|grid|done|error, ...}."""
    if _client is None:
        yield {"type": "error",
               "message": "CLAUDE_API_KEY no configurada. Añádela a backend/.env y reinicia "
                          "el servidor. La búsqueda ya funciona."}
        return
    if _TOOLS is None:
        yield {"type": "error", "message": "Chat no inicializado: falta chat.configure(search)."}
        return

    sid, messages = _session(session_id)
    turn_start = len(messages)  # para deshacer el turno completo si la API falla
    messages.append({"role": "user", "content": _build_prompt(text, view)})

    # La parrilla se actualiza UNA sola vez, al final del turno, con la última búsqueda
    # con resultados (el agente puede refinar sin que el usuario vea los intentos).
    best_inp = None   # última search_catalog con total > 0
    last_inp = None   # última search_catalog (fallback si todas dieron 0)
    try:
        for _ in range(MAX_TURNS):
            async with _client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                tools=_TOOLS,
                messages=messages,
            ) as stream:
                async for chunk in stream.text_stream:
                    yield {"type": "text", "text": chunk}
                response = await stream.get_final_message()

            # historial: los bloques (incl. thinking/tool_use) vuelven tal cual a la API
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                inp = block.input if isinstance(block.input, dict) else {}
                yield {"type": "tool", "name": block.name,
                       "label": _tool_label(block.name, inp)}

                if block.name == "search_manual":
                    try:
                        payload = search_manual_impl(inp.get("sku", ""), inp.get("doctype", ""),
                                                     inp.get("question"), inp.get("model"))
                    except Exception as e:  # noqa: BLE001
                        # visible en logs (Railway): sin esto el fallo solo lo ve el LLM
                        print(f"[manual] ERROR {type(e).__name__}: {e}", flush=True)
                        payload = {"error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(payload, ensure_ascii=False)})
                    continue

                if block.name == "search_style_guide":
                    try:
                        payload = search_style_guide_impl(inp.get("query", ""), inp.get("keywords"))
                    except Exception as e:  # noqa: BLE001
                        # visible en logs (Railway): sin esto el fallo solo lo ve el LLM
                        print(f"[style] ERROR {type(e).__name__}: {e}", flush=True)
                        payload = {"error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(payload, ensure_ascii=False)})
                    continue

                if block.name == "find_local_suppliers":
                    # abre en el frontend el buscador de distribuidores (LocalSuppliers):
                    # geolocalización -> /suppliers/nearby -> lista + mapa. La geolocalización
                    # vive en el navegador, así que el trabajo real lo hace el cliente.
                    prod = (inp.get("product") or "").strip()
                    yield {"type": "suppliers", "product": prod or None}
                    note = f" para «{prod}»" if prod else ""
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": f"He abierto el buscador de distribuidores "
                                                    f"cercanos{note}. Muestra puntos de venta Roca "
                                                    "reales ordenados por distancia, con mapa; el "
                                                    "usuario debe permitir la geolocalización (o usar "
                                                    "la ubicación de referencia que ofrece el propio "
                                                    "buscador)."})
                    continue

                if block.name == "show_product":
                    # abre en el frontend la ficha del producto (openProduct): incluye el
                    # botón "Comprar online" -> añade al carrito (compra online).
                    sku = (inp.get("sku") or "").strip()
                    if sku:
                        yield {"type": "product", "sku": sku}
                        content = (f"He abierto la ficha del producto {sku} para el usuario, con "
                                   "su botón de compra (online: «Comprar online» añade al carrito; "
                                   "si es de distribuidor: «Dónde comprar»).")
                    else:
                        content = ("No tengo el SKU. Busca primero con search_catalog y usa el "
                                   "SKU del resultado.")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": content})
                    continue

                # search_catalog
                try:
                    data = _run_search(**_search_kwargs(inp, MODEL_TOP))
                    brief = [_brief_from_card(c) for c in data.get("results", [])]
                    payload = {"total": data.get("total", 0), "results": brief}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(payload, ensure_ascii=False)})
                    last_inp = inp
                    if data.get("total", 0) > 0:
                        best_inp = inp
                except Exception as e:  # noqa: BLE001
                    yield {"type": "tool_error", "name": block.name, "error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": tool_results})

        # parrilla diferida: re-ejecuta la búsqueda ganadora (top 24 para la UI) y adjunta
        # los filtros que usó el agente para reflejarlos en el sidebar.
        winner = best_inp or last_inp
        if winner is not None:
            try:
                grid = _run_search(**_search_kwargs(winner, GRID_TOP))
                filters = {k: v for k, v in winner.items()
                           if k != "query" and v not in (None, "", [])}
                yield {"type": "grid", "query": winner.get("query"),
                       "filters": filters, "data": grid}
            except Exception as e:  # noqa: BLE001
                yield {"type": "tool_error", "name": "search_catalog", "error": str(e)}
    except anthropic.APIStatusError as e:
        del messages[turn_start:]  # no dejar el turno a medias en el historial
        yield {"type": "error", "message": f"Error de la API de Claude ({e.status_code}): {e.message}"}
        return
    except anthropic.APIConnectionError:
        del messages[turn_start:]
        yield {"type": "error", "message": "No se pudo conectar con la API de Claude. Reintenta."}
        return

    yield {"type": "done", "session_id": sid}
