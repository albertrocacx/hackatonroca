"""Chat IA opcional sobre los resultados del catálogo.

Transporte-agnóstico: `stream_turn` es un generador asíncrono que produce eventos
{type: text|tool|grid|done|error, ...}; la ruta /api/chat de main.py los serializa
como NDJSON (una línea JSON por evento) sobre un StreamingResponse.

La recuperación va SIEMPRE a través de la búsqueda propia de la app (inyectada con
`configure`), de modo que el motor de búsqueda sigue siendo la única fuente de verdad:
este módulo no reimplementa nada de búsqueda.

Auth: necesita CLAUDE_CODE_OAUTH_TOKEN (genéralo con `claude setup-token`). Ponlo en
backend/.env. Si falta, stream_turn emite un evento de error en lugar de llamar a Claude.
"""
import inspect
import json
import os
import types as _types
from typing import Union, get_args, get_origin

from claude_agent_sdk import (
    query, tool, create_sdk_mcp_server, ClaudeAgentOptions,
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
)


# --- token: env o backend/.env (mini-loader para no depender de python-dotenv) ---
def _load_env():
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
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
OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")

GRID_TOP = 24     # productos que se vuelcan a la parrilla
MODEL_TOP = 10    # productos que se pasan al modelo (resumir, no listar todo)


# --- inyección de la búsqueda (el handler /search de main.py) ---
# El schema de la tool se DERIVA de la firma de search(): cualquier filtro que acepte
# (hoy o en el futuro) queda disponible para el agente sin tocar este archivo.
_search_fn = None
_MCP = None
TOOLS = ["mcp__roca__search_catalog"]
SEARCH_PARAMS: set[str] = set()   # todos los params de search()
LIST_PARAMS: set[str] = set()     # los que son listas (deben enviarse como lista)
INTERNAL_PARAMS = {"q", "limit", "include_spare"}  # no son filtros de usuario


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


def configure(search_fn):
    """main.py inyecta su `search`. Construimos aquí (con la firma ya conocida) el schema
    dinámico de la tool, la tool y el servidor MCP en proceso."""
    global _search_fn, _MCP, SEARCH_PARAMS, LIST_PARAMS
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
    schema = {"type": "object", "properties": props, "required": ["query"]}

    @tool("search_catalog",
          "Busca productos en el catálogo Roca por lenguaje natural + filtros opcionales. "
          "Los resultados se muestran automáticamente en la parrilla del usuario.",
          schema)
    async def _search_catalog_tool(args):
        data = _run_search(**_search_kwargs(args, MODEL_TOP))
        brief = [_brief_from_card(c) for c in data.get("results", [])]
        payload = {"total": data.get("total", 0), "results": brief}
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    _MCP = create_sdk_mcp_server("roca", tools=[_search_catalog_tool])


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

Herramienta (úsala, nunca inventes productos ni precios):
- search_catalog: buscar productos. Los resultados se enseñan TAMBIÉN al usuario en una \
parrilla automáticamente, así que no listes todos: resume y señala la parrilla ("Aquí \
tienes algunas opciones a la izquierda").

REGLA DE BÚSQUEDA (impórtate mucho):
- Haz UNA sola llamada a search_catalog por turno, con la consulta MÁS SIMPLE y directa \
posible: normalmente las palabras del usuario tal cual. Si escribe "lavabos", busca \
`query="lavabos"` y nada más. NO encadenes varias búsquedas ni hagas intentos \
exploratorios (evita dejar la parrilla vacía).
- Colores, materiales, formas y descripciones (p. ej. "blanco", "mate", "redondo") van \
en el TEXTO de `query`, NO como filtro (la búsqueda de texto los encuentra de forma \
fiable; los filtros exactos como `finish` distinguen mayúsculas y suelen fallar). \
Ej.: "lavabos blancos" -> `query="lavabos blancos"`.
- Usa filtros estructurados SOLO cuando el usuario los pida y sean numéricos o exactos: \
precio (min_price/max_price en EUR) y dimensiones (min/max_length/width/height en \
MILÍMETROS; convierte cm/m a mm, 100 cm = 1000 mm). Ejemplos: "lavabos blancos por menos \
de 200 €" -> `query="lavabos blancos", max_price=200`; "inodoros de alto máx 100 cm" -> \
`query="inodoros", max_height=1000`.
- Solo haz más de una búsqueda si el usuario pide algo que de verdad lo exige (p. ej. \
comparar dos categorías distintas), y dilo.

Comportamiento:
- Responde en el idioma del usuario (español por defecto). Sé concreto y breve.
- Los precios son PVPR en EUR. Cita atributos reales; si falta un dato, dilo.
- Cuando el usuario diga "estos", "los que se ven", usa el contexto [contexto] de SKUs \
mostrados. Ofrece afinar o comparar."""


def _build_prompt(text: str, view: dict | None) -> str:
    if not view:
        return text
    bits = []
    if view.get("query"):
        bits.append(f"búsqueda actual: {view['query']!r}")
    if view.get("visible"):
        bits.append(f"SKUs mostrados: {view['visible'][:12]}")
    return f"[contexto] {' | '.join(bits)}\n\n{text}" if bits else text


async def stream_turn(text: str, session_id: str | None = None, view: dict | None = None):
    """Genera eventos de UI: {type: text|tool|grid|done|error, ...}."""
    if not OAUTH_TOKEN:
        yield {"type": "error",
               "message": "CLAUDE_CODE_OAUTH_TOKEN no configurado. Ejecuta `claude setup-token`, "
                          "añádelo a backend/.env y reinicia el servidor. La búsqueda ya funciona."}
        return
    if _MCP is None:
        yield {"type": "error", "message": "Chat no inicializado: falta chat.configure(search)."}
        return

    opts = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        mcp_servers={"roca": _MCP},
        allowed_tools=TOOLS,
        permission_mode="bypassPermissions",
        env={"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN},
        resume=session_id,
        max_turns=12,
    )

    grid_locked = False   # se bloquea tras la 1ª búsqueda CON resultados (un intento vacío no la fija)
    async for msg in query(prompt=_build_prompt(text, view), options=opts):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    yield {"type": "text", "text": block.text}
                elif isinstance(block, ToolUseBlock):
                    short = block.name.split("__")[-1]
                    yield {"type": "tool", "name": short}
                    if short == "search_catalog" and not grid_locked:
                        # mueve la parrilla: re-ejecuta la MISMA búsqueda (top 24 para la UI)
                        # y adjunta los filtros que usó el agente para reflejarlos en el sidebar.
                        try:
                            inp = block.input
                            data = _run_search(**_search_kwargs(inp, GRID_TOP))
                            # filtros = todos los args que usó el agente (menos la query), dinámico
                            filters = {k: v for k, v in inp.items()
                                       if k != "query" and v not in (None, "", [])}
                            yield {"type": "grid", "query": inp.get("query"),
                                   "filters": filters, "data": data}
                            # solo fijamos la parrilla cuando hay resultados; si dio 0, un 2º
                            # intento (p. ej. mejor casing) aún puede sustituirla.
                            if data.get("total", 0) > 0:
                                grid_locked = True
                        except Exception as e:  # noqa: BLE001
                            yield {"type": "tool_error", "name": short, "error": str(e)}
        elif isinstance(msg, ResultMessage):
            yield {"type": "done", "session_id": msg.session_id}
