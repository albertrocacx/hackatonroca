"""
Interpretacion de consultas en lenguaje natural para el buscador.

Una sola llamada al LLM de Azure AI Foundry (cliente OpenAI v1) que a la vez:
  - corrige erratas (fuzzy) SIN cambiar el significado, y
  - extrae atributos (categoria, color, precio, tamano, coleccion) -> filtros.

Si no hay credenciales FOUNDRY (o el SDK no esta), `available()` devuelve False y
main.py usa el heuristico `parse_query` como fallback. Nunca rompe la busqueda.
"""
import json
import os
import re

# Carga backend/.env si existe (no pisa variables ya definidas: en Railway mandan las
# del panel). Igual que azure_search.py, para funcionar tambien en standalone.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001
    pass

try:
    from openai import OpenAI
except Exception:  # noqa: BLE001
    OpenAI = None


def _default_foundry_endpoint() -> str:
    """FOUNDRY_ENDPOINT si esta; si no, deriva del recurso Azure OpenAI (mismo recurso
    aihackathonfoundry) anadiendo /openai/v1; si tampoco, el valor por defecto del hackathon."""
    ep = os.getenv("FOUNDRY_ENDPOINT")
    if ep:
        return ep
    aoai = os.getenv("AZURE_OPENAI_ENDPOINT")
    if aoai:
        return aoai.rstrip("/") + "/openai/v1"
    return "https://aihackathonfoundry.services.ai.azure.com/openai/v1"


FOUNDRY_ENDPOINT = _default_foundry_endpoint()
# La API key del recurso Foundry. En este hackathon el recurso de Azure OpenAI y el de
# Foundry son el mismo (aihackathonfoundry), asi que AZURE_OPENAI_KEY sirve como fallback.
FOUNDRY_API_KEY = (os.getenv("FOUNDRY_API_KEY")
                   or os.getenv("OPENAI_API_KEY")
                   or os.getenv("AZURE_OPENAI_KEY", ""))
DEPLOYMENT = os.getenv("FOUNDRY_DEPLOYMENT", "gpt-5.4-nano")

_llm = None


def _client():
    global _llm
    if _llm is None:
        if OpenAI is None:
            raise RuntimeError("openai no instalado")
        if not FOUNDRY_API_KEY:
            raise RuntimeError("Falta FOUNDRY_API_KEY (o AZURE_OPENAI_KEY) en el entorno o backend/.env")
        _llm = OpenAI(base_url=FOUNDRY_ENDPOINT, api_key=FOUNDRY_API_KEY)
    return _llm


def available() -> bool:
    """True si podemos llamar al LLM (SDK presente + API key configurada)."""
    return OpenAI is not None and bool(FOUNDRY_API_KEY)


SYSTEM = (
    "Eres un asistente que interpreta busquedas de una tienda de banos (Roca). "
    "Recibes una frase libre en espanol y devuelves SOLO un objeto JSON valido, "
    "sin texto adicional ni bloques de codigo. Tu trabajo:\n"
    "1) Corregir erratas tipograficas SIN cambiar el significado ni traducir. "
    "Si no hay erratas, devuelve la frase igual.\n"
    "2) Extraer los atributos de producto que aparezcan en la frase.\n\n"
    "Esquema exacto del JSON de salida:\n"
    "{\n"
    '  "corrected_query": string,        // frase corregida (o igual si no hay erratas)\n'
    '  "corrected": boolean,             // true solo si has cambiado algo\n'
    '  "category": string|null,          // EXACTAMENTE una de las categorias dadas, o null\n'
    '  "color": string|null,             // la palabra de color/acabado que uso el usuario (p.ej. "rojo"), para la etiqueta; o null\n'
    '  "finishes": string[],             // valores EXACTOS de la lista de acabados que correspondan a ese color; [] si ninguno\n'
    '  "collection": string|null,        // nombre de coleccion/linea de diseno si se menciona, o null\n'
    '  "price": { "min": number|null, "max": number|null },  // en euros\n'
    '  "size": { "band": "small"|"medium"|"large"|null, "dimension": "length"|"width"|"height"|null },\n'
    '  "search_text": string             // solo el/los sustantivos de producto (p.ej. "ducha"); "" si no hay\n'
    "}\n\n"
    "Reglas:\n"
    "- 'menos de 300', 'hasta 300', 'por debajo de 300' => price.max=300. "
    "'mas de 500', 'desde 500' => price.min=500. 'entre 200 y 400' => min=200,max=400.\n"
    "- Tamanos relativos: 'pequena/mini/compacta'=>small, 'mediana'=>medium, 'grande/xl/amplia'=>large. "
    "Elige la dimension mas relevante para esa categoria (length=largo, width=ancho, height=alto). "
    "Si no hay mencion de tamano, band=null y dimension=null.\n"
    "- category DEBE salir de la lista de categorias dada (elige el valor mas parecido) o null.\n"
    "- Para el color/acabado: rellena 'finishes' con los valores EXACTOS de la lista de acabados dada "
    "que correspondan al color pedido (p.ej. 'rojo' -> ['Passion Red']; 'verde' -> todos los verdes de la lista; "
    "'negro' -> todos los negros). Si el color no tiene acabados en la lista, finishes=[]. "
    "'color' es solo la palabra que uso el usuario (para la etiqueta).\n"
    "- search_text NO debe incluir el color, el precio ni el tamano; solo el producto."
)


def _extract_json(text: str):
    """Parsea el JSON de la respuesta del LLM, tolerando fences ```json o texto alrededor."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def interpret(q: str, categories: tuple, colors: tuple, finishes: tuple = ()):
    """Llama al LLM. Devuelve (parsed|None, debug), donde `debug` incluye los textos
    EXACTOS enviados a la LLM (system/user), la respuesta cruda y metadatos del modelo.
    `categories`, `colors` y `finishes` acotan los valores validos."""
    user = (
        f"Categorias disponibles: {', '.join(categories)}\n"
        f"Colores frecuentes: {', '.join(colors)}\n"
        f"Acabados disponibles (usa estos valores EXACTOS en 'finishes'): {', '.join(finishes)}\n\n"
        f"Frase del usuario: {q}"
    )
    debug = {"engine": "llm", "model": DEPLOYMENT, "endpoint": FOUNDRY_ENDPOINT,
             "system": SYSTEM, "user": user, "raw": None}
    resp = _client().responses.create(
        model=DEPLOYMENT,
        max_output_tokens=500,
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.output_text or ""
    debug["raw"] = raw
    return _extract_json(raw), debug
