"""
Extraccion para busqueda semantica: procesa los JSON de metadatos de producto
(formato web Roca, p.ej. 5A154BC00.json) y construye, por producto, el TEXTO
natural que se enviara al modelo de embeddings.

Que hace:
  - Selecciona solo los campos con carga semantica (titulo, descripciones,
    jerarquia de categorias, coleccion, acabados, atributos descriptivos, precio).
  - Descarta ruido para embeddings (imagenes, planos, manuales, BIM, IDs,
    versiones, estados, urls...).
  - Deduplica frases repetidas manteniendo el orden.

Salida -> backend/data/search_text.md   (solo el texto; varios productos se
          separan con una linea '---').

Uso:
  python extract_search_text.py <archivo.json | carpeta_con_jsons> [...]
Si no se pasa ruta, procesa backend/data/*.json.
"""
import json
import os
import sys
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "backend", "data")
OUT_FILE = os.path.join(OUT_DIR, "search_text.md")

# Atributos (por 'code') que NO aportan a la busqueda semantica.
ATTR_BLOCKLIST = {
    "BIMUrl", "Status", "MDMModel", "ModelNumber", "PublicationStatus",
    "Version", "BoxGrossWeight_kg", "BoxHeight_mm", "BoxLength_mm",
    "BoxNumberOfPieces", "BoxWidth_mm", "GrossWeight_kg",
}

# Simbolo de moneda por mercado (por defecto euro).
CURRENCY = {"ES": "€", "PT": "€", "FR": "€", "GB": "£"}


def clean(v):
    """Normaliza a string no vacio o None."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def add(parts, seen, value, prefix=""):
    """Anade 'value' (con 'prefix' opcional) a 'parts' evitando duplicados."""
    v = clean(value)
    if not v:
        return
    line = f"{prefix}{v}" if prefix else v
    key = line.lower()
    if key not in seen:
        seen.add(key)
        parts.append(line)


def walk_categories(nodes, parts, seen):
    """Recorre searchCategories en profundidad: nombres, leads y titulos SEO."""
    for node in nodes or []:
        add(parts, seen, node.get("name"), "Categoria: ")
        add(parts, seen, node.get("seoTitle"))
        add(parts, seen, node.get("lead"))
        walk_categories(node.get("children"), parts, seen)


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

    # 1) Titulo y descripciones principales.
    add(parts, seen, product.get("description"))
    add(parts, seen, product.get("seoDescription"))

    # 2) Coleccion / design line (p.ej. "Carmen").
    for dl in product.get("designlines", []):
        add(parts, seen, dl.get("name"), "Coleccion: ")

    # 3) Jerarquia de categorias (nombres + textos de marketing).
    walk_categories(product.get("searchCategories"), parts, seen)
    for cat in product.get("sellings", []):
        add(parts, seen, cat.get("name"), "Categoria: ")
    for cat in product.get("neutralCategories", []):
        add(parts, seen, cat.get("name"), "Categoria: ")

    # 4) Atributos descriptivos (STRING) que no esten en la blocklist.
    for attr in product.get("attributes", []):
        if attr.get("code") in ATTR_BLOCKLIST:
            continue
        if attr.get("type") != "STRING":
            continue
        value = clean(attr.get("value"))
        if not value or value.startswith("http"):
            continue
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
