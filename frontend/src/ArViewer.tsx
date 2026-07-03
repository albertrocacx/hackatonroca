import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { TDSLoader } from "three/examples/jsm/loaders/TDSLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { XREstimatedLight } from "three/examples/jsm/webxr/XREstimatedLight.js";
import type { Model3dInfo } from "./api";

// Visor de Realidad Aumentada ("Visualizar en tu habitación") con tres modos,
// del más al menos inmersivo según lo que soporte el dispositivo:
//
//  1. AR real (WebXR, Android/Chrome): la cámara detecta el suelo (hit-test);
//     una retícula sigue el plano detectado y al tocar, el producto queda
//     FIJADO en ese punto del espacio físico (como el AR de roca.es).
//  2. AR Quick Look (iPhone/iPad): el modelo cargado se exporta a USDZ al
//     vuelo y se abre el visor AR nativo de iOS, que ancla al suelo real.
//  3. Previsualización (escritorio / sin soporte): cámara como fondo (si hay)
//     y el producto sobre un suelo virtual con controles manuales:
//     arrastrar mueve, pellizco/rueda escala, dos dedos o Shift+arrastrar
//     rota, ▲/▼ altura, y "Anclar" usa el giroscopio si existe.
//
// Los CAD del blob vienen en unidades/ejes arbitrarios (pulgadas Z-up los
// .3ds): orientación y escala se deducen comparando el bounding box con las
// medidas reales del catálogo (dims_mm). Formato principal FBX si el
// navegador puede leerlo; si no, 3DS (?ar3d=3ds fuerza el fallback).

type CamState = "starting" | "on" | "off";
type ModelState = "loading" | "ready" | "error";
type XrState = "none" | "available" | "active";

const MODEL_DISTANCE = 2.4; // m delante de la cámara (modo previsualización)
const FLOOR_Y = -1.35;      // cota del suelo virtual (el móvil se sostiene ~1,35 m)
const CAM_PITCH = -0.22;    // rad: mirada ligeramente hacia abajo al encuadrar

const mm = (v: number | null | undefined) => (v ? v / 1000 : null);

const IS_IOS =
  typeof navigator !== "undefined" &&
  (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1));

// Altura de montaje típica por categoría: los productos murales (lavabos,
// grifería, espejos) no van en el suelo; se elevan hasta su cota habitual
// (si el CAD ya trae pedestal/columna, no se eleva).
function mountLift(category: string | null, modelHeight: number): number {
  const c = category ?? "";
  let top = 0;
  if (/lavabo/i.test(c)) top = 0.85;
  else if (/grifer/i.test(c)) top = 0.95;
  else if (/espejo/i.test(c)) top = 1.55;
  else if (/accesorio/i.test(c)) top = 1.0;
  return Math.max(0, top - modelHeight);
}

// Deduce orientación (Y-up vs Z-up) y escala a metros comparando el bbox del
// CAD con las medidas reales. Decide por el encaje de los DOS ejes
// horizontales (largo/ancho); el alto solo desempata: el del catálogo suele
// ser la pieza suelta (lavabo sin pedestal) y el del CAD el conjunto.
function fitToCatalog(
  size: THREE.Vector3, dims: Model3dInfo["dims_mm"], loadedAs: "GLB" | "FBX" | "3DS"
): { rotX: number; scale: number } {
  const L = mm(dims?.length), W = mm(dims?.width), H = mm(dims?.height);
  let rotZup = loadedAs === "3DS"; // convención CAD: los .3ds suelen ser Z-up
  let scale = 0;
  if (L && W && size.x > 0 && size.y > 0 && size.z > 0) {
    const [r1, r2] = L >= W ? [L, W] : [W, L];
    const hyp = (h1: number, h2: number, vert: number) => {
      const [a, b] = h1 >= h2 ? [h1, h2] : [h2, h1];
      const s = r1 / a;
      return { s, errH: Math.abs(b * s - r2) / r2, vert: vert * s };
    };
    const yUp = hyp(size.x, size.z, size.y);
    const zUp = hyp(size.x, size.y, size.z);
    if (Math.abs(zUp.errH - yUp.errH) > 0.1) {
      rotZup = zUp.errH < yUp.errH;
    } else if (H) {
      rotZup = Math.abs(Math.log(zUp.vert / H)) < Math.abs(Math.log(yUp.vert / H));
    }
    scale = (rotZup ? zUp : yUp).s;
  }
  // sin medidas o escala disparatada: normaliza a un alto razonable
  const maxDim = Math.max(size.x, size.y, size.z) * (scale || 1);
  if (!scale || maxDim > 5 || maxDim < 0.05) {
    const v = Math.max(size.x, size.y, size.z);
    scale = v > 0 ? 0.9 / v : 1;
  }
  return { rotX: rotZup ? -Math.PI / 2 : 0, scale };
}

export default function ArViewer({
  info, title, category, onClose,
}: {
  info: Model3dInfo;
  title: string | null;
  category: string | null;
  onClose: () => void;
}) {
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [cam, setCam] = useState<CamState>("starting");
  const [modelState, setModelState] = useState<ModelState>("loading");
  const [fmt, setFmt] = useState<"GLB" | "FBX" | "3DS" | null>(null);
  const [gyro, setGyro] = useState(false);
  const gyroRef = useRef(false);
  const [xr, setXr] = useState<XrState>("none");
  const [xrPlaced, setXrPlaced] = useState(false);
  const [xrTracking, setXrTracking] = useState(false); // hay superficie bajo la retícula
  const [usdzUrl, setUsdzUrl] = useState<string | null>(null);
  // móvil Android sin AR real: motivo para mostrárselo al usuario
  const [xrWhy, setXrWhy] = useState<"sin-webxr" | "sin-arcore" | null>(null);

  // API imperativa que el efecto de three.js expone a los botones de la UI
  const actions = useRef({
    scale: (_f: number) => {},
    rotate: (_rad: number) => {},
    lift: (_dy: number) => {},
    reset: () => {},
    enterXr: () => {},
    endXr: () => {},
  });

  const gyroAvailable =
    typeof window !== "undefined" && "DeviceOrientationEvent" in window &&
    (navigator.maxTouchPoints ?? 0) > 0;

  async function toggleGyro() {
    if (gyroRef.current) { gyroRef.current = false; setGyro(false); return; }
    const DOE = DeviceOrientationEvent as unknown as { requestPermission?: () => Promise<string> };
    if (typeof DOE.requestPermission === "function") {
      try {
        if ((await DOE.requestPermission()) !== "granted") return;
      } catch { return; }
    }
    gyroRef.current = true;
    setGyro(true);
  }

  // ------------------------------------------------- escena three.js + AR --
  useEffect(() => {
    if (!hostRef.current) return;
    const host: HTMLDivElement = hostRef.current;
    const overlayEl = overlayRef.current;

    // preserveDrawingBuffer: el canvas se puede leer tras el render (capturas)
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(host.clientWidth, host.clientHeight);
    renderer.domElement.className = "ar-canvas";
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      60, host.clientWidth / host.clientHeight, 0.01, 50
    );
    camera.position.set(0, 0, 0);
    camera.rotation.x = CAM_PITCH;

    // luces por defecto agrupadas: en AR real, si el dispositivo da
    // estimación de luz (light-estimation), se sustituyen por la de la sala
    const baseLights = new THREE.Group();
    baseLights.add(new THREE.HemisphereLight(0xffffff, 0x8d8478, 1.35));
    const sun = new THREE.DirectionalLight(0xffffff, 1.8);
    sun.position.set(2, 4, 2.5);
    baseLights.add(sun);
    scene.add(baseLights);

    const xrLight = new XREstimatedLight(renderer);
    xrLight.addEventListener("estimationstart", () => {
      scene.add(xrLight);
      scene.remove(baseLights);
      if (xrLight.environment) scene.environment = xrLight.environment;
    });
    xrLight.addEventListener("estimationend", () => {
      scene.remove(xrLight);
      if (!baseLights.parent) scene.add(baseLights);
      scene.environment = null;
    });

    // ancla del producto: en previsualización se mueve por el suelo virtual;
    // en AR real se fija donde el usuario toca sobre el plano detectado
    const anchor = new THREE.Group();
    anchor.position.set(0, FLOOR_Y, -MODEL_DISTANCE);
    scene.add(anchor);

    // sombra de contacto falsa; vive FUERA del ancla para quedarse en el
    // suelo cuando el producto se eleva (murales)
    const cnv = document.createElement("canvas");
    cnv.width = cnv.height = 256;
    const ctx = cnv.getContext("2d")!;
    const grad = ctx.createRadialGradient(128, 128, 8, 128, 128, 128);
    grad.addColorStop(0, "rgba(0,0,0,0.42)");
    grad.addColorStop(0.7, "rgba(0,0,0,0.16)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 256, 256);
    const shadowMat = new THREE.MeshBasicMaterial({
      map: new THREE.CanvasTexture(cnv), transparent: true, depthWrite: false,
    });
    const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.5, 40), shadowMat);
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.set(0, FLOOR_Y + 0.004, -MODEL_DISTANCE);
    scene.add(shadow);
    let floorY = FLOOR_Y; // cota del suelo (virtual, o del plano detectado en XR)

    // retícula del hit-test (AR real): doble anillo (oscuro + claro) para que
    // se vea tanto en suelos claros como oscuros, y punto central
    const reticle = new THREE.Group();
    const ringOut = new THREE.Mesh(
      new THREE.RingGeometry(0.10, 0.15, 40).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0x1a1a1a, opacity: 0.55, transparent: true })
    );
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.11, 0.14, 40).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xffffff, opacity: 0.95, transparent: true })
    );
    ring.position.y = 0.001;
    const dot = new THREE.Mesh(
      new THREE.CircleGeometry(0.02, 20).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    reticle.add(ringOut, ring, dot);
    reticle.matrixAutoUpdate = false;
    reticle.visible = false;
    scene.add(reticle);

    // --------------------------------------------------- cámara de fondo --
    // Solo para la previsualización: en AR real la cámara la gestiona el
    // propio WebXR (hay que soltarla antes de pedir la sesión).
    let stream: MediaStream | null = null;
    let camCancel = false;
    let camGen = 0; // generación: invalida los getUserMedia en vuelo al parar/entrar en XR
    async function startCam(retryOnce = false) {
      const gen = ++camGen;
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
          audio: false,
        });
        // llegó tarde (se pidió XR / se cerró / hubo otro startCam): suéltala
        if (gen !== camGen || camCancel || xrActive) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream?.getTracks().forEach((t) => t.stop());
        stream = s;
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          await videoRef.current.play().catch(() => {});
        }
        setCam("on");
      } catch {
        // al salir de XR, ARCore puede tardar en soltar la cámara: un reintento
        if (retryOnce && !camCancel && !xrActive) {
          setTimeout(() => { if (!camCancel && !xrActive) void startCam(false); }, 1000);
          return;
        }
        setCam("off"); // sin cámara/permiso: previsualización con fondo neutro
      }
    }
    function stopCam() {
      camGen++; // descarta cualquier getUserMedia pendiente
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    startCam();

    // ------------------------------------------------------ carga del modelo
    let disposed = false;
    const furniture = new THREE.Group();
    anchor.add(furniture);

    const isTap = /grifer/i.test(category ?? "");

    function placeLoaded(obj: THREE.Object3D, kind: "GLB" | "FBX" | "3DS") {
      if (disposed) return;
      // materiales CAD (sin nombre/blanco puro): acabado cerámico o cromado
      obj.traverse((c) => {
        const mesh = c as THREE.Mesh;
        if (!mesh.isMesh) return;
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const m of mats) {
          const ph = m as THREE.MeshPhongMaterial;
          if (!ph || !(ph as any).isMeshPhongMaterial || ph.map) continue;
          const generic = !ph.name || ph.color.getHex() === 0xffffff;
          if (!generic) continue;
          if (isTap) {
            ph.color.set("#c9ced3"); ph.specular.set("#9aa0a6"); ph.shininess = 85;
          } else {
            ph.color.set("#f4f4f2"); ph.specular.set("#666666"); ph.shininess = 55;
          }
        }
      });
      const box = new THREE.Box3().setFromObject(obj);
      const size = new THREE.Vector3(); box.getSize(size);
      const { rotX, scale } = fitToCatalog(size, info.dims_mm, kind);
      obj.rotation.x = rotX;
      obj.scale.setScalar(scale);
      // base en y=0 del ancla y centrado en x/z
      const box2 = new THREE.Box3().setFromObject(obj);
      const ctr = new THREE.Vector3(); box2.getCenter(ctr);
      obj.position.set(-ctr.x, -box2.min.y, -ctr.z);
      furniture.add(obj);
      // huella de la sombra acorde al producto
      const fx = Math.max(box2.max.x - box2.min.x, 0.25);
      const fz = Math.max(box2.max.z - box2.min.z, 0.25);
      shadow.scale.set(fx * 1.25, fz * 1.25, 1);
      // murales: a su altura de montaje típica
      baseLift = mountLift(category, box2.max.y - box2.min.y);
      lift = baseLift;
      applyLift();
      setFmt(kind);
      setModelState("ready");
      if (IS_IOS) prepareUsdz(); // Quick Look necesita el USDZ listo en el href
    }

    // cadena de carga: GLB (glTF: PBR, metros, Y-up — el mejor para web/AR)
    // → FBX → 3DS. ?ar3d=3ds fuerza el último eslabón para depurar.
    const force3ds = new URLSearchParams(window.location.search).get("ar3d") === "3ds";
    const fail = () => { if (!disposed) setModelState("error"); };
    const loadTds = () => {
      if (!info.tds) { fail(); return; }
      new TDSLoader().load(info.tds, (o) => placeLoaded(o, "3DS"), undefined, fail);
    };
    const loadFbx = () => {
      if (!info.fbx) { loadTds(); return; }
      new FBXLoader().load(
        info.fbx,
        (o) => placeLoaded(o, "FBX"),
        undefined,
        () => { console.warn("FBX no cargó, probando 3DS"); loadTds(); }
      );
    };
    if (info.glb && !force3ds) {
      // los GLB del blob vienen comprimidos con Draco (KHR_draco_mesh_compression);
      // el decoder WASM se sirve desde public/draco/ (copiado de three)
      const draco = new DRACOLoader();
      draco.setDecoderPath("/draco/");
      const gltfLoader = new GLTFLoader();
      gltfLoader.setDRACOLoader(draco);
      gltfLoader.load(
        info.glb,
        (g) => { placeLoaded(g.scene, "GLB"); draco.dispose(); },
        undefined,
        (e) => { console.warn("GLB no cargó, probando FBX/3DS", e); draco.dispose(); loadFbx(); }
      );
    } else if (!force3ds) {
      loadFbx();
    } else {
      loadTds();
    }

    // ------------------------------------ iOS: exportar a USDZ (Quick Look)
    // Safari no soporta WebXR: se exporta el modelo YA normalizado (escala
    // real en metros) a USDZ y un <a rel="ar"> abre el visor AR nativo, que
    // detecta el suelo y ancla el objeto él solo.
    let usdzObjectUrl: string | null = null;
    async function prepareUsdz() {
      try {
        const { USDZExporter } = await import("three/examples/jsm/exporters/USDZExporter.js");
        // clon a escala real (sin el pellizco del usuario) con materiales
        // Standard: el exportador no entiende MeshPhongMaterial
        const clone = furniture.clone(true);
        clone.scale.setScalar(1);
        // el exportador lee object.matrix (local): sin render de por medio hay
        // que recalcularla tras tocar la escala, o exportaría el pellizco del usuario
        clone.updateMatrix();
        clone.traverse((c) => {
          const mesh = c as THREE.Mesh;
          if (!mesh.isMesh) return;
          const conv = (m: THREE.Material) => {
            // los GLB ya traen PBR (MeshStandardMaterial): se exportan tal cual
            if ((m as THREE.MeshStandardMaterial).isMeshStandardMaterial) return m;
            const ph = m as THREE.MeshPhongMaterial;
            return new THREE.MeshStandardMaterial({
              color: ph.color ? ph.color.clone() : new THREE.Color(0xf0f0f0),
              map: ph.map ?? null, // conserva la textura si el CAD la trae
              roughness: ph.shininess != null ? Math.min(0.9, Math.max(0.1, 1 - ph.shininess / 100)) : 0.5,
              metalness: isTap ? 0.8 : 0.05,
            });
          };
          mesh.material = Array.isArray(mesh.material) ? mesh.material.map(conv) : conv(mesh.material);
        });
        const data = await new USDZExporter().parseAsync(clone);
        if (disposed) return;
        const blob = new Blob([data], { type: "model/vnd.usdz+zip" });
        // Quick Look es poco fiable con blob: URLs (modo AR deshabilitado en
        // varias versiones de iOS): se sube al backend y se enlaza una URL
        // real con extensión .usdz. El blob queda de último recurso.
        const src = info.fbx ?? info.tds;
        const m = src?.match(/^(.*)\/models3d\/([^/]+)\/file\//);
        if (m) {
          try {
            const r = await fetch(`${m[1]}/models3d/${m[2]}/usdz`, { method: "POST", body: blob });
            if (r.ok) {
              const j = await r.json();
              // allowsContentScaling=0: Quick Look bloquea el pellizco para
              // que el producto se vea SIEMPRE a escala real (práctica retail)
              if (!disposed) setUsdzUrl(`${m[1]}${j.url}#allowsContentScaling=0`);
              return;
            }
          } catch { /* backend sin el endpoint: cae al blob */ }
        }
        usdzObjectUrl = URL.createObjectURL(blob);
        if (!disposed) setUsdzUrl(usdzObjectUrl);
      } catch (e) {
        console.warn("No se pudo preparar el USDZ para Quick Look", e);
      }
    }

    // --------------------------------------------------------------- gestos
    const pointers = new Map<number, { x: number; y: number }>();
    let pinchDist = 0, pinchAngle = 0;
    let userScale = 1;
    let lift = 0;     // elevación sobre el suelo
    let baseLift = 0; // altura de montaje inicial (murales); la fija placeLoaded
    const clampScale = (v: number) => Math.min(3.5, Math.max(0.3, v));
    const applyScale = () => furniture.scale.setScalar(userScale);
    const applyLift = () => { anchor.position.y = floorY + lift; };

    function onPointerDown(e: PointerEvent) {
      (e.target as Element).setPointerCapture?.(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (xrActive) {
        dragAccum = 0;
        // cada dedo emite su propio 'select' al soltarse: un gesto de dos
        // dedos no debe recolocar el producto
        if (pointers.size >= 2) multiTouch = true;
        return; // en AR: tocar coloca, arrastrar gira
      }
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
        pinchAngle = Math.atan2(b.y - a.y, b.x - a.x);
      }
    }
    function onPointerMove(e: PointerEvent) {
      if (xrActive) {
        // giro del producto colocado arrastrando en horizontal; dragAccum
        // evita que el 'select' del final del gesto lo recoloque
        const prev = pointers.get(e.pointerId);
        if (prev && xrPlacedFlag && pointers.size === 1) {
          const dx = e.clientX - prev.x;
          dragAccum += Math.abs(dx) + Math.abs(e.clientY - prev.y);
          anchor.rotation.y += dx * 0.008;
        }
        if (prev) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        return;
      }
      const prev = pointers.get(e.pointerId);
      if (!prev) return;
      const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) {
        if (e.shiftKey || (e.buttons & 2)) {
          anchor.rotation.y += dx * 0.008;
        } else {
          // arrastre = desplazar por el suelo (x lateral, y de pantalla = profundidad)
          const k = MODEL_DISTANCE / host.clientHeight * 1.4;
          anchor.position.x += dx * k;
          anchor.position.z += dy * k;
          anchor.position.z = Math.min(-0.8, Math.max(-8, anchor.position.z));
        }
      } else if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const ang = Math.atan2(b.y - a.y, b.x - a.x);
        if (pinchDist > 0) {
          userScale = clampScale(userScale * (d / pinchDist));
          applyScale();
          anchor.rotation.y += ang - pinchAngle;
        }
        pinchDist = d; pinchAngle = ang;
      }
    }
    function onPointerUp(e: PointerEvent) {
      pointers.delete(e.pointerId);
      pinchDist = 0;
    }
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      if (xrActive) return;
      userScale = clampScale(userScale * (1 - e.deltaY * 0.001));
      applyScale();
    }
    const el = renderer.domElement;
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", onPointerUp);
    el.addEventListener("pointercancel", onPointerUp);
    el.addEventListener("wheel", onWheel, { passive: false });
    const noCtx = (e: Event) => e.preventDefault();
    el.addEventListener("contextmenu", noCtx);

    // -------------------------------------------- AR real (WebXR hit-test)
    let xrActive = false;
    let xrSession: XRSession | null = null;
    // dos fuentes de hit-test: los planos son precisos pero tardan en
    // consolidarse; los feature points responden al instante. Se prefiere
    // plano cuando lo hay.
    let hitSourcePlane: XRHitTestSource | null = null;
    let hitSourcePoint: XRHitTestSource | null = null;
    let lastHit: XRHitTestResult | null = null; // último hit bajo la retícula
    let xrAnchor: XRAnchor | null = null;       // ancla ARCore del producto colocado
    let xrAnchorWall = false;                   // el ancla vive en una pared
    let xrPlacedFlag = false;
    let placeGen = 0;                           // generación: invalida anclas de colocaciones superadas
    let dragAccum = 0;                          // px: distingue giro de toque
    let multiTouch = false;                     // gesto de 2 dedos: sus 'select' no colocan
    const camWorldPos = new THREE.Vector3();
    const tmpS = new THREE.Vector3();
    const smoothPos = new THREE.Vector3();
    const smoothQ = new THREE.Quaternion();
    let smoothInit = false;
    // pose CRUDA del último hit (la del ancla); la suavizada es solo visual
    const rawHitPos = new THREE.Vector3();
    const rawHitQ = new THREE.Quaternion();
    // plan B de anclaje: si createAnchor() del hit falla (UAs estrictos con
    // frames pasados), se crea un ancla libre en el siguiente XRFrame
    let frameAnchorGen = 0;
    const frameAnchorPos = new THREE.Vector3();
    const frameAnchorQ = new THREE.Quaternion();

    // Android de verdad (los portátiles táctiles Windows no deben ver avisos de ARCore)
    const isAndroidTouch = /android/i.test(navigator.userAgent);
    const xrSystem: XRSystem | undefined = (navigator as any).xr;
    if (xrSystem?.isSessionSupported) {
      xrSystem.isSessionSupported("immersive-ar")
        .then((ok) => {
          if (disposed) return;
          if (ok) setXr("available");
          else if (isAndroidTouch) setXrWhy("sin-arcore");
        })
        .catch(() => { if (!disposed && isAndroidTouch) setXrWhy("sin-arcore"); });
    } else if (isAndroidTouch) {
      setXrWhy("sin-webxr"); // navegador embebido o sin WebXR: usar Chrome
    }

    // los toques sobre los botones del overlay no deben colocar el producto
    function onBeforeXrSelect(e: Event) {
      const t = e.target as Element | null;
      if (t?.closest(".ar-topbar, .ar-toolbar, .ar-hint, .ar-status")) e.preventDefault();
    }

    // coloca el grupo `anchor` en un punto/orientación dados (suelo o pared)
    const hitNormal = new THREE.Vector3();
    function placeAt(pos: THREE.Vector3, q: THREE.Quaternion) {
      hitNormal.set(0, 1, 0).applyQuaternion(q);
      const esPared = Math.abs(hitNormal.y) < 0.4;
      xrAnchorWall = esPared;
      if (esPared) {
        // colgado en la pared a la altura del toque, de espaldas a ella
        anchor.position.copy(pos);
        anchor.rotation.set(0, Math.atan2(hitNormal.x, hitNormal.z), 0);
        floorY = pos.y;         // la sombra no aplica en pared
        shadow.visible = false;
        lift = 0;
      } else {
        // en suelo, los murales recuperan su altura de montaje (por si la
        // colocación anterior fue en pared, que fuerza lift = 0)
        lift = baseLift;
        floorY = pos.y;
        anchor.position.set(pos.x, pos.y + lift, pos.z);
        // de frente hacia el usuario (luego se puede girar con ⟲/⟳ o arrastrando)
        camera.getWorldPosition(camWorldPos);
        anchor.rotation.set(0, Math.atan2(camWorldPos.x - pos.x, camWorldPos.z - pos.z), 0);
        shadow.visible = true;
      }
      anchor.visible = true;
      xrPlacedFlag = true;
      setXrPlaced(true);
    }

    function onXrSelect() {
      // un arrastre (girar) o un gesto de 2 dedos también disparan 'select'
      // al soltar: no deben recolocar
      if (dragAccum > 12) { dragAccum = 0; return; }
      dragAccum = 0;
      if (multiTouch) {
        if (pointers.size === 0) multiTouch = false; // último dedo del gesto
        return;
      }
      if (!reticle.visible) return;
      // se coloca en la pose CRUDA del último hit (la misma a la que quedará
      // atada el ancla); la retícula suavizada es solo presentación
      placeAt(rawHitPos, rawHitQ);
      // ancla ARCore: el sistema refina la pose según mejora su mapa del
      // mundo (menos deriva al moverse); si no hay soporte, colocación fija
      xrAnchor?.delete();
      xrAnchor = null;
      const gen = ++placeGen;
      const adopt = (a: XRAnchor | undefined) => {
        // solo si esta colocación sigue siendo la vigente
        if (a && gen === placeGen && xrActive && xrPlacedFlag) {
          xrAnchor?.delete();
          xrAnchor = a;
        } else {
          a?.delete();
        }
      };
      const anchorFromFrame = () => {
        // plan B: ancla libre en la pose de colocación, creada en el
        // siguiente XRFrame (el bucle la recoge con frameAnchorGen)
        frameAnchorPos.copy(rawHitPos);
        frameAnchorQ.copy(rawHitQ);
        frameAnchorGen = gen;
      };
      const p = lastHit?.createAnchor?.();
      if (p) p.then(adopt).catch(anchorFromFrame);
      else anchorFromFrame();
    }

    let xrStarting = false;
    async function enterXr() {
      if (!xrSystem || xrActive || xrStarting) return;
      xrStarting = true;
      stopCam(); // ARCore necesita la cámara en exclusiva
      let session: XRSession | null = null;
      try {
        session = await xrSystem.requestSession("immersive-ar", {
          requiredFeatures: ["hit-test", "local-floor"],
          // anchors: pose refinada por ARCore; light-estimation: luz de la sala
          optionalFeatures: ["dom-overlay", "anchors", "light-estimation"],
          ...(overlayEl ? { domOverlay: { root: overlayEl } } : {}),
        });
        xrSession = session;
        // el listener 'end' se registra ANTES de los awaits restantes: si el
        // usuario aborta durante el arranque de ARCore, onXrEnd restaura todo
        session.addEventListener("end", onXrEnd);
        session.addEventListener("select", onXrSelect);
        overlayEl?.addEventListener("beforexrselect", onBeforeXrSelect);
        xrActive = true;
        renderer.xr.enabled = true;
        renderer.xr.setReferenceSpaceType("local-floor");
        await renderer.xr.setSession(session);
        const viewerSpace = await session.requestReferenceSpace("viewer");
        // planos (precisos, tardan) + feature points (inmediatos): la retícula
        // arranca con puntos y se afina sola cuando ARCore consolida el plano
        try {
          hitSourcePlane = (await session.requestHitTestSource?.({
            space: viewerSpace, entityTypes: ["plane"],
          })) ?? null;
        } catch { hitSourcePlane = null; }
        try {
          hitSourcePoint = (await session.requestHitTestSource?.({
            space: viewerSpace, entityTypes: ["point"],
          })) ?? null;
        } catch { hitSourcePoint = null; }
        if (!hitSourcePlane && !hitSourcePoint) {
          // sin entityTypes específicos (implementaciones antiguas): genérico
          hitSourcePlane = (await session.requestHitTestSource?.({ space: viewerSpace })) ?? null;
        }
        smoothInit = false;
        xrPlacedFlag = false;
        xrAnchorWall = false;
        multiTouch = false;
        setXr("active");
        setXrPlaced(false);
        // hasta que el usuario coloque, solo se ve la retícula
        anchor.visible = false;
        shadow.visible = false;
      } catch (e) {
        console.warn("No se pudo iniciar la sesión AR", e);
        if (session) {
          // sesión creada pero arranque fallido: ciérrala; si ya estaba
          // terminada, 'end' no volverá a disparar y limpiamos a mano
          session.end().catch(() => onXrEnd());
        } else {
          onXrEnd();
        }
      } finally {
        xrStarting = false;
      }
    }

    function onXrEnd() {
      overlayEl?.removeEventListener("beforexrselect", onBeforeXrSelect);
      hitSourcePlane = null;
      hitSourcePoint = null;
      lastHit = null;
      placeGen++; // invalida cualquier createAnchor() aún en vuelo
      xrAnchor?.delete();
      xrAnchor = null;
      xrAnchorWall = false;
      xrPlacedFlag = false;
      xrSession = null;
      xrActive = false;
      if (disposed) return; // el 'end' puede llegar después del cleanup del efecto
      renderer.xr.enabled = false;
      reticle.visible = false;
      anchor.visible = true;
      shadow.visible = true;
      // restaura la iluminación por defecto si la sesión usaba la estimada
      scene.remove(xrLight);
      if (!baseLights.parent) scene.add(baseLights);
      scene.environment = null;
      setXr(xrSystem ? "available" : "none");
      setXrPlaced(false);
      setXrTracking(false);
      // de vuelta a la previsualización: recoloca en el suelo virtual
      floorY = FLOOR_Y;
      resetPlacement();
      onResize();
      if (!disposed) startCam(true); // ARCore puede tardar en soltar la cámara
    }

    function resetPlacement() {
      userScale = 1; applyScale();
      lift = baseLift; applyLift();
      anchor.rotation.set(0, 0, 0);
      anchor.position.set(0, floorY + lift, -MODEL_DISTANCE);
      camera.position.set(0, 0, 0);
      camera.quaternion.identity();
      camera.rotation.x = CAM_PITCH;
      // WebXR sobrescribe fov/aspect con los de la cámara del dispositivo
      camera.fov = 60;
      camera.aspect = host.clientWidth / host.clientHeight;
      camera.updateProjectionMatrix();
    }

    actions.current = {
      scale: (f) => { userScale = clampScale(userScale * f); applyScale(); },
      rotate: (rad) => { anchor.rotation.y += rad; },
      lift: (dy) => { lift = Math.min(1.4, Math.max(0, lift + dy)); applyLift(); },
      reset: () => {
        if (xrActive) {
          // en AR real, "centrar" solo restaura escala/altura;
          // la posición la decide el toque sobre la superficie detectada
          userScale = 1; applyScale();
          lift = xrAnchorWall ? 0 : baseLift;
          applyLift();
        } else {
          resetPlacement();
        }
      },
      enterXr: () => { void enterXr(); },
      endXr: () => { xrSession?.end().catch(() => {}); },
    };

    // ------------------------------------- giroscopio: ancla a la habitación
    // (solo previsualización; en WebXR la pose la da el propio dispositivo)
    const zee = new THREE.Vector3(0, 0, 1);
    const euler = new THREE.Euler();
    const q0 = new THREE.Quaternion();
    const q1 = new THREE.Quaternion(-Math.sqrt(0.5), 0, 0, Math.sqrt(0.5));
    let alpha0: number | null = null;
    let hasOrientation = false;
    const devQ = new THREE.Quaternion();
    function onOrientation(e: DeviceOrientationEvent) {
      if (e.alpha == null && e.beta == null && e.gamma == null) return;
      if (alpha0 == null) alpha0 = e.alpha ?? 0;
      const alpha = THREE.MathUtils.degToRad((e.alpha ?? 0) - alpha0);
      const beta = THREE.MathUtils.degToRad(e.beta ?? 90);
      const gamma = THREE.MathUtils.degToRad(e.gamma ?? 0);
      const orient = THREE.MathUtils.degToRad(
        (screen.orientation?.angle ?? (window as any).orientation ?? 0) as number
      );
      euler.set(beta, alpha, -gamma, "YXZ");
      devQ.setFromEuler(euler);
      devQ.multiply(q1);
      devQ.multiply(q0.setFromAxisAngle(zee, -orient));
      hasOrientation = true;
    }
    window.addEventListener("deviceorientation", onOrientation);

    // ------------------------------------------------------- bucle y resize
    let lastTracking = false;
    const targetPos = new THREE.Vector3();
    const targetQ = new THREE.Quaternion();
    const reticleMatrix = new THREE.Matrix4();
    const anchorPose = new THREE.Vector3();
    renderer.setAnimationLoop((_t: number, frame?: XRFrame) => {
      if (xrActive && frame) {
        const refSpace = renderer.xr.getReferenceSpace();
        // retícula: plano si lo hay (preciso); si no, feature point (rápido)
        let pose: XRPose | undefined;
        if (refSpace) {
          const planeHits = hitSourcePlane ? frame.getHitTestResults(hitSourcePlane) : [];
          const hits = planeHits.length
            ? planeHits
            : (hitSourcePoint ? frame.getHitTestResults(hitSourcePoint) : []);
          if (hits.length > 0) {
            pose = hits[0].getPose(refSpace) ?? undefined;
            lastHit = hits[0];
          }
        }
        if (pose) {
          reticle.visible = true;
          reticleMatrix.fromArray(pose.transform.matrix);
          reticleMatrix.decompose(targetPos, targetQ, tmpS);
          rawHitPos.copy(targetPos);
          rawHitQ.copy(targetQ);
          // suavizado: elimina el temblor del hit-test (salta si va muy lejos)
          if (!smoothInit || smoothPos.distanceTo(targetPos) > 0.5) {
            smoothPos.copy(targetPos);
            smoothQ.copy(targetQ);
            smoothInit = true;
          } else {
            smoothPos.lerp(targetPos, 0.35);
            smoothQ.slerp(targetQ, 0.35);
          }
          reticle.matrix.compose(smoothPos, smoothQ, tmpS.set(1, 1, 1));
        } else {
          reticle.visible = false;
        }
        if (reticle.visible !== lastTracking) {
          lastTracking = reticle.visible; // setState solo en transiciones
          setXrTracking(reticle.visible);
        }
        // plan B de anclaje pendiente: ancla libre en la pose de colocación
        if (frameAnchorGen === placeGen && frameAnchorGen > 0 && refSpace && frame.createAnchor) {
          const gen = frameAnchorGen;
          frameAnchorGen = 0; // un solo intento
          try {
            frame.createAnchor(
              new XRRigidTransform(
                { x: frameAnchorPos.x, y: frameAnchorPos.y, z: frameAnchorPos.z },
                { x: frameAnchorQ.x, y: frameAnchorQ.y, z: frameAnchorQ.z, w: frameAnchorQ.w }
              ),
              refSpace
            )?.then((a) => {
              if (a && gen === placeGen && xrActive && xrPlacedFlag) {
                xrAnchor?.delete();
                xrAnchor = a;
              } else {
                a?.delete();
              }
            }).catch(() => { /* sin anchors: colocación fija */ });
          } catch { /* sin anchors: colocación fija */ }
        }
        // producto anclado: ARCore refina la pose del XRAnchor cada frame
        // (lift se respeta también en pared, o ▲/▼ quedarían sin efecto)
        if (xrAnchor && refSpace && frame.trackedAnchors?.has(xrAnchor)) {
          const ap = frame.getPose(xrAnchor.anchorSpace, refSpace);
          if (ap) {
            anchorPose.set(ap.transform.position.x, ap.transform.position.y, ap.transform.position.z);
            if (!xrAnchorWall) floorY = anchorPose.y;
            anchor.position.set(anchorPose.x, anchorPose.y + lift, anchorPose.z);
          }
        }
      } else if (gyroRef.current && hasOrientation && !xrActive) {
        camera.quaternion.copy(devQ);
      }
      // la sombra sigue al producto en el plano del suelo
      shadow.position.x = anchor.position.x;
      shadow.position.z = anchor.position.z;
      shadow.position.y = floorY + 0.004;
      const liftNow = anchor.position.y - floorY;
      shadowMat.opacity = Math.max(0.25, 1 - liftNow * 0.6);
      renderer.render(scene, camera);
    });

    function onResize() {
      camera.aspect = host.clientWidth / host.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(host.clientWidth, host.clientHeight);
    }
    window.addEventListener("resize", onResize);

    if (import.meta.env.DEV) {
      // hook de depuración para tests de integración (solo build de desarrollo)
      (window as any).__arDebug = { renderer, scene, camera, anchor, furniture, reticle, prepareUsdz };
    }

    return () => {
      disposed = true;
      camCancel = true;
      xrSession?.end().catch(() => {});
      stopCam();
      if (usdzObjectUrl) URL.revokeObjectURL(usdzObjectUrl);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("deviceorientation", onOrientation);
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", onPointerUp);
      el.removeEventListener("pointercancel", onPointerUp);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("contextmenu", noCtx);
      renderer.setAnimationLoop(null);
      renderer.dispose();
      host.removeChild(renderer.domElement);
      scene.traverse((c) => {
        const mesh = c as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry?.dispose();
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          mats.forEach((m) => m?.dispose());
        }
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  // cerrar con Escape (y terminar la sesión AR si está activa)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { actions.current.endXr(); onClose(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const d = info.dims_mm;
  const dimsLabel = d?.length && d?.width && d?.height
    ? `${Math.round(d.length / 10)} × ${Math.round(d.width / 10)} × ${Math.round(d.height / 10)} cm`
    : null;

  const xrActive = xr === "active";

  return (
    <div
      ref={overlayRef}
      className={`ar-overlay${cam === "off" && !xrActive ? " ar-overlay--fallback" : ""}${xrActive ? " ar-overlay--xr" : ""}`}
    >
      <video ref={videoRef} className="ar-video" playsInline muted autoPlay />
      <div ref={hostRef} className="ar-stage" />

      <div className="ar-topbar">
        <div>
          <div className="ar-heading">Visualiza en tu habitación</div>
          {title && <div className="ar-product">{title}{dimsLabel ? ` · ${dimsLabel}` : ""}</div>}
        </div>
        <button
          type="button"
          className="ar-close"
          onClick={() => { actions.current.endXr(); onClose(); }}
          aria-label="Cerrar"
        >×</button>
      </div>

      <div className="ar-status">
        {modelState === "loading" && <span className="ar-chip">Cargando modelo 3D…</span>}
        {modelState === "ready" && fmt && !xrActive && (
          <span className="ar-chip ar-chip--ok">
            Modelo 3D {fmt}{fmt === "3DS" && (info.glb || info.fbx) ? " (fallback)" : ""} · escala real
          </span>
        )}
        {modelState === "error" && (
          <span className="ar-chip ar-chip--err">No se pudo cargar el modelo 3D</span>
        )}
        {cam === "off" && !xrActive && (
          <span className="ar-chip ar-chip--warn">
            Cámara no disponible · modo previsualización
          </span>
        )}
        {xrWhy && !xrActive && (
          <span className="ar-chip ar-chip--warn">
            {xrWhy === "sin-webxr"
              ? "AR real no disponible: abre esta página en Chrome (no en un navegador integrado)"
              : "AR real no disponible: instala/actualiza “Servicios de Google Play para RA” (ARCore)"}
          </span>
        )}
        {xrActive && (
          <span className={`ar-chip ${xrPlaced || xrTracking ? "ar-chip--ok" : "ar-chip--warn"}`}>
            {xrPlaced
              ? "Fijado en tu espacio · arrastra para girarlo · toca para moverlo"
              : xrTracking
                ? "Superficie detectada · toca para colocar el producto"
                : "Buscando superficie… mueve el móvil despacio apuntando al suelo o la pared"}
          </span>
        )}
      </div>

      {/* AR real: WebXR en Android/Chrome, Quick Look (USDZ) en iOS */}
      {modelState === "ready" && !xrActive && (xr === "available" || (IS_IOS && usdzUrl)) && (
        <div className="ar-xr-row">
          {xr === "available" && (
            <button type="button" className="ar-place" onClick={() => actions.current.enterXr()}>
              ◉ Colocar en tu espacio
            </button>
          )}
          {IS_IOS && usdzUrl && (
            <a className="ar-place" rel="ar" href={usdzUrl}>
              <img
                alt=""
                width="1"
                height="1"
                src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
              />
              ◉ Ver en tu espacio (AR)
            </a>
          )}
        </div>
      )}

      <div className="ar-toolbar">
        <button type="button" onClick={() => actions.current.rotate(-Math.PI / 12)} title="Rotar a la izquierda">⟲</button>
        <button type="button" onClick={() => actions.current.rotate(Math.PI / 12)} title="Rotar a la derecha">⟳</button>
        <button type="button" onClick={() => actions.current.scale(1 / 1.15)} title="Reducir">−</button>
        <button type="button" onClick={() => actions.current.scale(1.15)} title="Ampliar">+</button>
        <button type="button" onClick={() => actions.current.lift(0.08)} title="Subir (suspendidos)">▲</button>
        <button type="button" onClick={() => actions.current.lift(-0.08)} title="Bajar">▼</button>
        <button type="button" className="ar-txt" onClick={() => actions.current.reset()}>Centrar</button>
        {xrActive ? (
          <button type="button" className="ar-txt" onClick={() => actions.current.endXr()}>Salir AR</button>
        ) : (
          // con WebXR el giroscopio sobra, pero si quedó activo se mantiene el
          // botón para poder apagarlo
          gyroAvailable && (xr !== "available" || gyro) && (
            <button type="button" className={`ar-txt${gyro ? " is-on" : ""}`} onClick={toggleGyro}>
              {gyro ? "Anclado ✓" : "Anclar"}
            </button>
          )
        )}
      </div>

      <div className="ar-hint">
        {xrActive
          ? "Mueve el móvil despacio para detectar el suelo · toca donde quieras colocar el producto"
          : "Arrastra para mover · pellizca o usa la rueda para escalar · dos dedos o Shift+arrastrar para rotar"}
      </div>
    </div>
  );
}
