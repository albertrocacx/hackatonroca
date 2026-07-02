"""
Smoke test manual de /search/image contra el backend local y el endpoint DINO real.

Requiere: backend corriendo en :8000 con IMAGE_SEARCH_API_KEY en backend/.env.
Uso:  python tools/smoke_search_image.py [carpeta_con_fotos] [q opcional]
      (por defecto: C:/Users/parand01/Desktop/IA/hackaton_dino/imgtest, sin texto)
"""
import glob
import os
import sys

import requests

API = os.getenv("API_URL", "http://localhost:8000")
folder = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\parand01\Desktop\IA\hackaton_dino\imgtest"
q = sys.argv[2] if len(sys.argv) > 2 else ""

paths = sorted(glob.glob(os.path.join(folder, "*.jpg")))[:3]
if not paths:
    sys.exit(f"No hay .jpg en {folder}")
print(f"Fotos: {[os.path.basename(p) for p in paths]}  q={q!r}")

files = [("images", (os.path.basename(p), open(p, "rb"), "image/jpeg")) for p in paths]
r = requests.post(f"{API}/search/image", files=files, data={"q": q, "mode": "same"}, timeout=120)
print(f"HTTP {r.status_code}")
r.raise_for_status()
body = r.json()
print(f"total modelos: {body['total']}")
for c in body["results"][:9]:
    v = c["variants"][c["default"]]
    print(f"  {c['model']:<14} {v['sku']:<14} {c['title']}")
