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
import json
import os

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
_search_fn = None


def configure(search_fn):
    """main.py inyecta aquí su función `search` para que el agente busque en proceso."""
    global _search_fn
    _search_fn = search_fn


def _search_kwargs(a: dict, limit: int) -> dict:
    """Mapea los argumentos de la tool a los kwargs de search() (params lista van como lista)."""
    out = {"q": a.get("query", "") or "", "limit": limit}
    if a.get("category"):
        out["category"] = [a["category"]]
    if a.get("collection"):
        out["collection"] = [a["collection"]]
    if a.get("subcategory"):
        out["subcategory"] = a["subcategory"]
    if a.get("finish"):
        out["finish"] = a["finish"] if isinstance(a["finish"], list) else [a["finish"]]
    if a.get("min_price") is not None:
        out["min_price"] = a["min_price"]
    if a.get("max_price") is not None:
        out["max_price"] = a["max_price"]
    return out


def _run_search(**kwargs):
    if _search_fn is None:
        raise RuntimeError("chat.configure(search_fn) no ha sido llamado")
    return _search_fn(**kwargs)


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Texto de búsqueda en lenguaje natural (español)."},
        "category": {"type": "string", "description": "Categoría exacta para filtrar (opcional)."},
        "collection": {"type": "string", "description": "Colección exacta para filtrar (opcional)."},
        "subcategory": {"type": "string", "description": "Subcategoría exacta (opcional)."},
        "finish": {"type": "array", "items": {"type": "string"},
                   "description": "Acabados/colores a filtrar (opcional)."},
        "min_price": {"type": "number", "description": "Precio mínimo en EUR (opcional)."},
        "max_price": {"type": "number", "description": "Precio máximo en EUR (opcional)."},
    },
    "required": ["query"],
}


@tool("search_catalog",
      "Busca productos en el catálogo Roca por lenguaje natural + filtros opcionales. "
      "Los resultados se muestran automáticamente en la parrilla del usuario.",
      SEARCH_SCHEMA)
async def _search_catalog_tool(args):
    data = _run_search(**_search_kwargs(args, MODEL_TOP))
    brief = [{"sku": r["sku"], "title": r["title"], "collection": r["collection"],
              "finish": r["finish"], "price_rrp": r["price_rrp"]}
             for r in data.get("results", [])]
    payload = {"total": data.get("total", 0), "results": brief}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


_MCP = create_sdk_mcp_server("roca", tools=[_search_catalog_tool])
TOOLS = ["mcp__roca__search_catalog"]

SYSTEM = """Eres el asistente del catálogo Roca para el mercado español: un experto de \
showroom que ayuda a encontrar productos de baño y cocina (lavabos, inodoros, bidés, \
platos de ducha, bañeras, grifería, mobiliario, accesorios).

Herramienta (úsala, nunca inventes productos ni precios):
- search_catalog: buscar/filtrar productos. Llámala siempre que el usuario quiera ver o \
afinar lo que se muestra. Los resultados que obtienes se enseñan TAMBIÉN al usuario en \
una parrilla automáticamente, así que no listes todos: resume y señala la parrilla \
("Aquí tienes algunas opciones a la izquierda").

Comportamiento:
- Responde en el idioma del usuario (español por defecto). Sé concreto y breve.
- Los precios son PVPR en EUR. Cita atributos reales; si falta un dato, dilo.
- Cuando el usuario diga "estos", "los que se ven", usa el contexto [contexto] de SKUs \
mostrados. No hagas listados largos: la parrilla ya los muestra. Ofrece afinar o comparar."""


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

    opts = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        mcp_servers={"roca": _MCP},
        allowed_tools=TOOLS,
        permission_mode="bypassPermissions",
        env={"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN},
        resume=session_id,
        max_turns=12,
    )

    async for msg in query(prompt=_build_prompt(text, view), options=opts):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    yield {"type": "text", "text": block.text}
                elif isinstance(block, ToolUseBlock):
                    short = block.name.split("__")[-1]
                    yield {"type": "tool", "name": short}
                    if short == "search_catalog":
                        # mueve la parrilla: re-ejecuta la MISMA búsqueda (top 24 para la UI).
                        try:
                            data = _run_search(**_search_kwargs(block.input, GRID_TOP))
                            yield {"type": "grid",
                                   "query": block.input.get("query"), "data": data}
                        except Exception as e:  # noqa: BLE001
                            yield {"type": "tool_error", "name": short, "error": str(e)}
        elif isinstance(msg, ResultMessage):
            yield {"type": "done", "session_id": msg.session_id}
