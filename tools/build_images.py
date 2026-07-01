"""
Genera backend/data/images.json a partir del export del DAM (Cloudinary.xlsx).

Solo usamos fotos de producto (content_type == 'product_pictures'), nunca planos
tecnicos. El export relaciona cada asset con sus 'skus' y 'models'; resolvemos por
producto priorizando la coincidencia por SKU (foto del acabado exacto) y cayendo a
la coincidencia por modelo. images.json queda indexado por SKU.

Uso:  ./backend/.venv/bin/python tools/build_images.py
"""
import openpyxl, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, "..", "Cloudinary.xlsx")
OUT = os.path.join(HERE, "..", "backend", "data", "images.json")
PRODUCTS = os.path.join(HERE, "..", "backend", "data", "products.json")

# t_Download_72_dpi: version optimizada para web (misma que ya usaba el proyecto)
TRANSFORM = "/image/upload/"
TRANSFORM_72 = "/image/upload/t_Download_72_dpi/"


def split_ids(v):
    return [x.strip() for x in re.split(r"[;|]", str(v)) if x and x.strip()] if v else []


def to_72dpi(secure_url):
    return secure_url.replace(TRANSFORM, TRANSFORM_72, 1)


# ---------- indexa product_pictures por sku y por modelo ----------
wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
ws = wb.active
it = ws.iter_rows(values_only=True)
hdr = list(next(it))
idx = {h: i for i, h in enumerate(hdr)}
col = lambda row, name: row[idx[name]] if idx.get(name) is not None else None

by_sku, by_model = {}, {}
for row in it:
    if col(row, "content_type") != "product_pictures":
        continue
    if col(row, "resource_type") != "image":
        continue
    url = col(row, "secure_url")
    if not url:
        continue
    url = to_72dpi(url)
    for s in split_ids(col(row, "skus")):
        by_sku.setdefault(s, url)
    for m in split_ids(col(row, "models")):
        by_model.setdefault(m, url)
wb.close()

# ---------- resuelve una imagen por producto (sku > modelo) ----------
products = json.load(open(PRODUCTS, encoding="utf-8"))
images = {}
hit_sku = hit_model = 0
for p in products:
    sku, model = p.get("sku"), p.get("model")
    if sku in by_sku:
        images[sku] = by_sku[sku]; hit_sku += 1
    elif model in by_model:
        images[sku] = by_model[model]; hit_model += 1

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(images, f, ensure_ascii=False)

total = len(products)
print(f"assets product_pictures: sku={len(by_sku)} model={len(by_model)}")
print(f"images.json: {len(images)} productos con foto "
      f"({100 * len(images) // total}%) — por sku {hit_sku}, por modelo {hit_model}")
print(f"salida -> {os.path.abspath(OUT)}")
