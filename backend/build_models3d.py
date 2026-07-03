# -*- coding: utf-8 -*-
"""
Manifest de modelos 3D (Realidad Aumentada).

Los CAD 3D de producto viven en Blob Storage:
    data/products/products_3d/<model>/<model>__<codigo>__<coleccion>__..__CAD3D.(fbx|3ds)

Este script construye backend/data/models3d_manifest.json: por carpeta (= campo
`model` del catalogo), que ficheros hay, cuales parsean con three.js, su bbox y
el fichero elegido (fbx principal + 3ds fallback). El backend (models3d.py) lo
usa para decidir si un producto tiene AR y que blob servir.

Uso (pipeline en 3 pasos, paralelizable por slices):
    python build_models3d.py --list-folders            # imprime las carpetas
    python build_models3d.py --slice A,B,C --out p.json  # descarga+parsea un slice
    python build_models3d.py --merge p1.json p2.json ... # fusiona -> manifest

El paso --slice descarga los blobs del slice a un dir temporal y los parsea con
frontend/scripts/parse3d.mjs (three.js FBXLoader/TDSLoader via Node).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
load_dotenv(os.path.join(HERE, ".env"))

CONTAINER = "data"
PREFIX = "products/products_3d/"
MANIFEST = os.path.join(HERE, "data", "models3d_manifest.json")
PARSER = os.path.join(ROOT, "frontend", "scripts", "parse3d.mjs")
FRONTEND = os.path.join(ROOT, "frontend")


def get_container():
    from azure.storage.blob import BlobServiceClient
    cs = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not cs:
        sys.exit("Falta AZURE_STORAGE_CONNECTION_STRING en backend/.env")
    return BlobServiceClient.from_connection_string(cs).get_container_client(CONTAINER)


def list_folders(cont):
    folders = {}
    for b in cont.list_blobs(name_starts_with=PREFIX):
        rest = b.name[len(PREFIX):]
        if "/" not in rest:
            continue
        folder, name = rest.split("/", 1)
        folders.setdefault(folder, []).append({"name": name, "size": b.size})
    return folders


def cmd_list(cont):
    for f in sorted(list_folders(cont)):
        print(f)


def cmd_slice(cont, wanted, out_path):
    folders = list_folders(cont)
    missing = [w for w in wanted if w not in folders]
    if missing:
        print(f"AVISO: carpetas inexistentes: {missing}", file=sys.stderr)
    tmp = tempfile.mkdtemp(prefix="models3d_")
    try:
        for f in wanted:
            if f not in folders:
                continue
            os.makedirs(os.path.join(tmp, f), exist_ok=True)
            for item in folders[f]:
                dest = os.path.join(tmp, f, item["name"])
                with open(dest, "wb") as fh:
                    cont.download_blob(f"{PREFIX}{f}/{item['name']}").readinto(fh)
        # parsea todo el slice con three.js (cwd=frontend para resolver el paquete)
        r = subprocess.run(
            ["node", PARSER, tmp],
            cwd=FRONTEND, capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            sys.exit(f"parse3d.mjs fallo:\n{r.stderr[-2000:]}")
        parsed = json.loads(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    result = {}
    for f in wanted:
        if f not in folders:
            continue
        sizes = {i["name"]: i["size"] for i in folders[f]}
        files = []
        for name, size in sorted(sizes.items()):
            info = parsed.get(f"{f}/{name}", {"ok": False, "error": "no parseado"})
            files.append({"name": name, "size": size, **info})
        result[f] = {"files": files}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False)
    ok = sum(1 for v in result.values() for x in v["files"] if x.get("ok"))
    bad = sum(1 for v in result.values() for x in v["files"] if not x.get("ok"))
    print(f"slice: {len(result)} carpetas, {ok} ficheros OK, {bad} con error -> {out_path}")


# Empareja fbx/3ds por "clave de variante": el nombre sin la marca de formato.
def _variant_key(name):
    stem = os.path.splitext(name)[0]
    return re.sub(r"(__|_)(fbx|threeds)(_|__)?", "__", stem, flags=re.I)


def choose(files):
    """Elige fbx principal y 3ds fallback (preferentemente la misma variante)."""
    fbx_ok = [f for f in files if f["name"].lower().endswith(".fbx") and f.get("ok")]
    tds_ok = [f for f in files if f["name"].lower().endswith(".3ds") and f.get("ok")]
    fbx = fbx_ok[0] if fbx_ok else None
    tds = None
    if fbx:
        want = _variant_key(fbx["name"])
        tds = next((t for t in tds_ok if _variant_key(t["name"]) == want), None)
    if tds is None and tds_ok:
        tds = tds_ok[0]
    return (fbx["name"] if fbx else None), (tds["name"] if tds else None)


def cmd_merge(parts, out_path):
    merged = {}
    for p in parts:
        with open(p, encoding="utf-8") as fh:
            merged.update(json.load(fh))
    manifest = {}
    for folder, data in sorted(merged.items()):
        fbx, tds = choose(data["files"])
        if not fbx and not tds:
            continue  # carpeta sin ningun fichero utilizable
        manifest[folder] = {"fbx": fbx, "tds": tds, "files": data["files"]}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    total = len(merged)
    print(f"manifest: {len(manifest)}/{total} carpetas utilizables -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-folders", action="store_true")
    ap.add_argument("--slice", help="carpetas separadas por coma")
    ap.add_argument("--out", default=None)
    ap.add_argument("--merge", nargs="+", help="parts JSON a fusionar")
    args = ap.parse_args()
    if args.list_folders:
        cmd_list(get_container())
    elif args.slice:
        if not args.out:
            sys.exit("--slice requiere --out")
        cmd_slice(get_container(), [s for s in args.slice.split(",") if s], args.out)
    elif args.merge:
        cmd_merge(args.merge, args.out or MANIFEST)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
