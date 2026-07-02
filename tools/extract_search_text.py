"""
Extraccion para busqueda semantica: procesa los JSON de metadatos de producto
(formato web Roca, p.ej. 5A154BC00.json) y construye, por producto, el TEXTO
natural que se enviara al modelo de embeddings.

Que hace:
  - Selecciona solo los campos con carga semantica (titulo, coleccion,
    categoria mas especifica, acabados, atributos descriptivos, precio).
  - Descarta ruido para embeddings (imagenes, planos, manuales, BIM, IDs,
    versiones, estados, urls, textos de marketing genericos de categoria...).
  - Deduplica: por linea exacta y, para las descripciones de atributo, por
    contencion de tokens (si no aportan ninguna palabra nueva, se omiten).

Salida -> backend/data/search_text.md   (solo el texto; varios productos se
          separan con una linea '---').

Uso:
  python extract_search_text.py <archivo.json | carpeta_con_jsons> [...]
Si no se pasa ruta, procesa backend/data/*.json.
"""
import json
import os
import re
import sys
import glob
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "backend", "data")
OUT_FILE = os.path.join(OUT_DIR, "search_text.md")

# Atributos (por 'code') que NO aportan a la busqueda semantica.
#   - ShortDescription: abreviada y con mojibake -> ruido.
#   - DisplayModelNumber: identificador y ademas llega como lista sin formatear.
#   - MDMModel / ModelNumber: duplican la 'Referencia' (code) que ya se emite aparte.
ATTR_BLOCKLIST = {
    "BIMUrl", "Status", "MDMModel", "ModelNumber", "PublicationStatus",
    "Version", "BoxGrossWeight_kg", "BoxHeight_mm", "BoxLength_mm",
    "BoxNumberOfPieces", "BoxWidth_mm", "GrossWeight_kg",
    "ShortDescription", "DisplayModelNumber",
}

# Descripciones de atributo: se conservan solo si aportan tokens nuevos
# respecto al titulo + coleccion (evita repetir la descripcion en mayusculas,
# abreviada o con la coletilla de marca/coleccion).
DESC_ATTRS = {"MarketingDescription", "LongDescription"}

# Atributos NUMBER que si aportan semantica (por defecto los numeros se ignoran).
NUMBER_ATTR_ALLOW = {"NumberOfWaterOutletsFaucet"}

# Simbolo de moneda por mercado (por defecto euro).
CURRENCY = {"ES": "€", "PT": "€", "FR": "€", "GB": "£"}

# Palabras vacias (es) que no cuentan para la contencion de tokens.
STOPWORDS = {
    "de", "la", "el", "los", "las", "con", "para", "y", "en", "un", "una",
    "del", "al", "por", "o", "a", "su", "que", "no",
}


def clean(v):
    """Normaliza a string no vacio o None."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def norm_tokens(text):
    """Conjunto de tokens significativos: minusculas, sin acentos, sin stopwords."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    toks = re.split(r"[^a-z0-9]+", s)
    return {t for t in toks if len(t) > 2 and t not in STOPWORDS}


def fmt_number(v):
    """'2.0000' -> '2', '2.17' -> '2.17'. Si no es numero, devuelve el texto limpio."""
    s = clean(v)
    if s is None:
        return None
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else f"{f:g}"


def add(parts, seen, value, prefix=""):
    """Anade 'value' (con 'prefix' opcional) a 'parts' evitando duplicados exactos."""
    v = clean(value)
    if not v:
        return
    line = f"{prefix}{v}" if prefix else v
    key = line.lower()
    if key not in seen:
        seen.add(key)
        parts.append(line)


def deepest_category(nodes):
    """Devuelve el nodo hoja mas profundo de searchCategories (la categoria mas especifica)."""
    best = {"node": None, "depth": -1}

    def rec(node, depth):
        children = node.get("children")
        if children:
            for ch in children:
                rec(ch, depth + 1)
        elif depth > best["depth"]:
            best["node"], best["depth"] = node, depth

    for n in nodes or []:
        rec(n, 0)
    return best["node"]


def price_line(product):
    """Frase de precio a partir de los acabados; 'Precio desde' si hay varios."""
    prices = [f["price"]["value"] for f in product.get("finisheds") or []
              if isinstance(f.get("price"), dict) and f["price"].get("value") is not None]
    if not prices:
        return None
    lo = min(prices)
    symbol = CURRENCY.get(clean(product.get("market")), "€")
    amount = f"{lo:.2f}".replace(".", ",")  # formato es-ES: 331,00
    label = "Precio: " if len(set(prices)) == 1 else "Precio desde: "
    return f"{label}{amount} {symbol}"


def extract_search_text(product):
    """Construye el texto para embeddings a partir de un producto."""
    parts, seen = [], set()
    covered = set()  # tokens ya presentes en titulo + coleccion (para DESC_ATTRS)

    # 1) Titulo (unica descripcion; seoDescription se descarta: su coletilla
    #    -coleccion, categoria, marca, sku- ya se captura o se omite a proposito).
    desc = clean(product.get("description"))
    add(parts, seen, desc)
    if desc:
        covered |= norm_tokens(desc)

    # 1b) Identificadores: referencia de modelo y SKUs (busqueda por codigo).
    add(parts, seen, product.get("displayCode") or product.get("code"), "Referencia: ")
    skus = []
    for fin in product.get("finisheds", []):
        sku = clean(fin.get("sku"))
        if sku and sku not in skus:
            skus.append(sku)
    if skus:
        add(parts, seen, ", ".join(skus), "SKU: ")

    # 2) Coleccion / design line (p.ej. "Brava").
    for dl in product.get("designlines", []):
        name = clean(dl.get("name"))
        add(parts, seen, name, "Colección: ")
        if name:
            covered |= norm_tokens(name)

    # 3) Categoria: solo el nodo hoja de searchCategories (nombre + seoTitle).
    leaf = deepest_category(product.get("searchCategories"))
    if leaf:
        add(parts, seen, leaf.get("name"), "Categoría: ")
        add(parts, seen, leaf.get("seoTitle"))

    # 4) Atributos descriptivos.
    for attr in product.get("attributes", []):
        code = attr.get("code")
        if code in ATTR_BLOCKLIST:
            continue

        atype = attr.get("type")
        if atype == "NUMBER":
            if code not in NUMBER_ATTR_ALLOW:
                continue
            value = fmt_number(attr.get("value"))
        elif atype == "STRING":
            value = clean(attr.get("value"))
        else:
            continue

        if not value or value.startswith("http"):
            continue

        # Descripciones de atributo: solo si aportan info nueva real.
        # (>=2 tokens nuevos; un unico token nuevo suele ser ruido o mojibake).
        if code in DESC_ATTRS:
            tokens = norm_tokens(value)
            if len(tokens - covered) < 2:
                continue
            covered |= tokens

        label = clean(attr.get("label")) or clean(attr.get("code"))
        add(parts, seen, value, f"{label}: " if label else "")

    # 5) Acabados disponibles (p.ej. "Cromado").
    for fin in product.get("finisheds", []):
        add(parts, seen, fin.get("name"), "Acabado: ")

    # 6) Precio.
    add(parts, seen, price_line(product))

    return "\n".join(parts)


def iter_json_files(paths):
    """Expande archivos y carpetas en una lista de rutas .json."""
    for p in paths:
        if os.path.isdir(p):
            yield from sorted(glob.glob(os.path.join(p, "*.json")))
        else:
            yield p


def main(argv):
    if argv:
        files = list(iter_json_files(argv))
    else:
        files = sorted(glob.glob(os.path.join(OUT_DIR, "*.json")))

    if not files:
        print("No hay JSON que procesar. Pasa un archivo o carpeta como argumento.")
        return 1

    blocks, n = [], 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                product = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"AVISO: no puedo leer {path}: {e}")
            continue
        text = extract_search_text(product)
        if not text:
            print(f"AVISO: sin texto, omito {path}")
            continue
        blocks.append(text)
        n += 1

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as out:
        out.write("\n\n---\n\n".join(blocks) + "\n")

    print(f"search_text.md: {n} productos")
    print(f"Salida -> {os.path.abspath(OUT_FILE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
