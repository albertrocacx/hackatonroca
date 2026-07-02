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
            "finish": v.get("finish"), "price_rrp": v.get("price_rrp")}


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
    "description": ("Responde preguntas sobre la documentación de UN producto concreto (por SKU): "
                    "manual de usuario, guía de instalación o ficha técnica. Devuelve fragmentos y "
                    "la URL del PDF para citarla. Requiere sku + doctype + question."),
    "input_schema": {
        "type": "object",
        "properties": {
            "sku": {"type": "string",
                    "description": "SKU/código del producto (carpeta del manual), p.ej. '8S6090000'."},
            "doctype": {"type": "string",
                        "enum": ["UserManual", "InstallationManual", "TechnicalFactSheet"],
                        "description": "Documento a consultar: UserManual (uso/mantenimiento), "
                                       "InstallationManual (instalación/montaje) o TechnicalFactSheet (ficha técnica)."},
            "question": {"type": "string", "description": "La pregunta del usuario en lenguaje natural."},
        },
        "required": ["sku", "doctype", "question"],
    },
}


def _strip_leading_char(sku: str) -> str:
    """Quita el primer elemento (letra o dígito) empezando por la izquierda.
    '18S6090000' -> '8S6090000' -> 'S6090000' -> ... Si está vacío, lo devuelve intacto."""
    return sku[1:] if sku else sku


def search_manual_impl(sku: str, doctype: str, question: str, top: int = MANUAL_TOP) -> dict:
    """Búsqueda en el índice de manuales por sku+doctype. Devuelve un payload citable
    (con pdf_url firmada). Aislada para poder testearla sin el LLM.

    Reintento por SKU (una sola vez): se busca con el SKU introducido; si no devuelve nada,
    se quita el primer elemento (letra o dígito) empezando por la izquierda y se vuelve a
    buscar. Si tampoco aparece, se termina. Cubre SKUs con un prefijo/variante que no casa
    con la carpeta del manual (p.ej. '18S6090000' -> '8S6090000')."""
    import search_ocr  # búsqueda vectorial con filtros de metadatos (índice de manuales)
    dt = DOCTYPE_MAP.get((doctype or "").strip().lower().replace(" ", "_"), (doctype or "").strip())
    q = (question or "").strip()
    sku0 = (sku or "").strip()

    sku_used = sku0
    hits = search_ocr.search_ocr(q, sku=sku0 or None, doctype=dt or None, top=top)
    retried = False
    if not hits and sku0:
        sku1 = _strip_leading_char(sku0)
        if sku1 and sku1 != sku0:   # solo reintentamos si el recorte deja un SKU no vacío
            retried = True
            print(f"[manual] sku={sku0!r} sin resultados -> reintento con {sku1!r}", flush=True)
            hits = search_ocr.search_ocr(q, sku=sku1, doctype=dt or None, top=top)
            sku_used = sku1

    results = [{"sku": h.get("sku"), "doctype": h.get("doctype"), "pdf_url": h.get("pdf_url"),
                "text": (h.get("text") or "")[:1200]} for h in hits]
    payload = {"sku": sku_used, "doctype": dt, "count": len(results), "results": results}
    if retried:   # informa a Claude de que el SKU fue recortado para citar bien
        payload["sku_requested"] = sku0
    return payload


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
    _TOOLS = [catalog_tool, _MANUAL_TOOL]


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

Herramientas (úsalas, nunca inventes productos, precios ni instrucciones):
- search_catalog: buscar productos. Los resultados se enseñan TAMBIÉN al usuario en una \
parrilla automáticamente, así que no listes todos: resume y señala la parrilla ("Aquí \
tienes algunas opciones a la izquierda").
- search_manual: responder preguntas sobre la documentación de UN producto concreto (por \
SKU): manual de usuario, guía de instalación o ficha técnica.

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

DOCUMENTACIÓN DE UN PRODUCTO (search_manual):
- Úsala cuando el usuario pregunte cómo usar, instalar, montar o mantener un producto \
identificado por su SKU (o uno visible en [contexto]).
- Necesitas DOS datos para llamarla: `sku` y `doctype`. `doctype` es UserManual (uso, \
limpieza, mantenimiento, garantía) o InstallationManual (instalación, montaje, medidas, \
fijación, conexiones); usa TechnicalFactSheet solo si piden la ficha técnica.
- Si NO tienes claro si la pregunta es del manual de USUARIO o de la guía de INSTALACIÓN, \
PREGÚNTASELO al usuario antes de llamar a la tool (no adivines). Si falta el SKU, pídelo \
(o confírmalo con el de [contexto] si es evidente).
- Con los fragmentos devueltos: responde SOLO con esa información y CITA la fuente como \
ENLACE MARKDOWN CLICABLE al `pdf_url`, p. ej. "[Guía de instalación · SKU 8S6090000](https://…)". \
Si los fragmentos no contienen la respuesta, dilo claramente (no inventes).

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


def _manual_label(inp: dict) -> str:
    doc = _DOCTYPE_LABEL.get(inp.get("doctype"), "la documentación")
    sku = inp.get("sku")
    return f"Consultando {doc} del producto {sku}" if sku else f"Consultando {doc}"


def _build_prompt(text: str, view: dict | None) -> str:
    if not view:
        return text
    bits = []
    if view.get("query"):
        bits.append(f"búsqueda actual: {view['query']!r}")
    if view.get("visible"):
        bits.append(f"SKUs mostrados: {view['visible'][:12]}")
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
                label = _manual_label(inp) if block.name == "search_manual" else _search_label(inp)
                yield {"type": "tool", "name": block.name, "label": label}

                if block.name == "search_manual":
                    try:
                        payload = search_manual_impl(inp.get("sku", ""), inp.get("doctype", ""),
                                                     inp.get("question", ""))
                    except Exception as e:  # noqa: BLE001
                        payload = {"error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(payload, ensure_ascii=False)})
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
