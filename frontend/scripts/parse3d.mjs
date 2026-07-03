// Valida CAD 3D (.fbx / .3ds) con los loaders reales de three.js y extrae
// metadatos (bbox, mallas, materiales). Lo invoca backend/build_models3d.py
// con cwd=frontend para que resuelva el paquete `three`.
//
//   node scripts/parse3d.mjs <dir>
//
// Recorre <dir>/<carpeta>/<fichero> y escribe en stdout un JSON:
//   { "<carpeta>/<fichero>": {ok, bbox?, meshes?, materials?, error?} }
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import * as THREE from "three";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { TDSLoader } from "three/examples/jsm/loaders/TDSLoader.js";

const root = process.argv[2];
if (!root) {
  console.error("uso: node scripts/parse3d.mjs <dir>");
  process.exit(2);
}

// Los CAD no llevan texturas empaquetadas, pero por si acaso: en Node no hay
// DOM, asi que evitamos que un intento de cargar texturas tire el parseo.
const silentManager = new THREE.LoadingManager();
silentManager.onError = () => {};

function parseOne(path) {
  const buf = readFileSync(path);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  let obj;
  if (path.toLowerCase().endsWith(".fbx")) {
    obj = new FBXLoader(silentManager).parse(ab, "");
  } else {
    obj = new TDSLoader(silentManager).parse(ab, "");
  }
  let meshes = 0;
  let tris = 0;
  const materials = new Set();
  obj.traverse((c) => {
    if (!c.isMesh) return;
    meshes++;
    const g = c.geometry;
    tris += Math.floor((g.index ? g.index.count : g.attributes.position?.count ?? 0) / 3);
    for (const m of Array.isArray(c.material) ? c.material : [c.material]) {
      if (m?.name) materials.add(m.name);
    }
  });
  const box = new THREE.Box3().setFromObject(obj);
  const size = new THREE.Vector3();
  if (!box.isEmpty()) box.getSize(size);
  return {
    ok: meshes > 0 && size.length() > 0,
    meshes,
    tris,
    materials: [...materials].slice(0, 12),
    bbox: [size.x, size.y, size.z].map((v) => +v.toFixed(4)),
    ...(meshes === 0 ? { error: "sin mallas" } : {}),
  };
}

const out = {};
for (const folder of readdirSync(root)) {
  const fdir = join(root, folder);
  if (!statSync(fdir).isDirectory()) continue;
  for (const file of readdirSync(fdir)) {
    const ext = file.toLowerCase();
    if (!ext.endsWith(".fbx") && !ext.endsWith(".3ds")) continue;
    const key = `${folder}/${file}`;
    try {
      out[key] = parseOne(join(fdir, file));
    } catch (e) {
      out[key] = { ok: false, error: String(e?.message ?? e).slice(0, 300) };
    }
  }
}
process.stdout.write(JSON.stringify(out));
