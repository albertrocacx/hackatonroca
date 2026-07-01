/**
 * Compra OFFLINE: encuentra distribuidores Roca cercanos.
 *
 * Seam para el buscador: el botón "Encuentra proveedor local" del colega llama a
 * `openLocalSuppliers(item)` en App, que monta este modal.
 *
 * Flujo: pide permiso de geolocalización → /suppliers/nearby (orden por distancia)
 * → lista + mapa. Si el usuario deniega el permiso, se ofrece una ubicación de
 * referencia (Barcelona) para poder demostrar el resultado igualmente.
 *
 * El mapa usa Leaflet + OpenStreetMap cargado desde CDN bajo demanda (sin npm install
 * ni API key). Si no carga (sin red), degrada a lista sola sin romper nada.
 */
import { useEffect, useRef, useState } from "react";
import { nearbySuppliers, type Supplier, type ShopItem } from "./api";

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const DEMO_ORIGIN = { lat: 41.3874, lon: 2.1686, label: "Barcelona (referencia)" };

type Phase = "asking" | "loading" | "ready" | "error";

// Carga Leaflet una sola vez. Resuelve con window.L o rechaza si no hay red.
let leafletPromise: Promise<any> | null = null;
function loadLeaflet(): Promise<any> {
  if ((window as any).L) return Promise.resolve((window as any).L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = LEAFLET_CSS;
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = LEAFLET_JS;
    js.async = true;
    js.onload = () => resolve((window as any).L);
    js.onerror = () => reject(new Error("Leaflet no disponible"));
    document.head.appendChild(js);
  });
  return leafletPromise;
}

function mapsLink(s: Supplier) {
  return `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`;
}

export default function LocalSuppliers({
  item, onClose,
}: {
  item: ShopItem | null;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("asking");
  const [msg, setMsg] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [origin, setOrigin] = useState<{ lat: number; lon: number } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markers = useRef<Record<string, any>>({});

  async function fetchFor(lat: number, lon: number) {
    setPhase("loading");
    try {
      const r = await nearbySuppliers(lat, lon, 8);
      setSuppliers(r.suppliers);
      setOrigin(r.origin);
      setSelected(r.suppliers[0]?.id ?? null);
      setPhase("ready");
    } catch (e) {
      setMsg(String(e));
      setPhase("error");
    }
  }

  function requestLocation() {
    setPhase("asking");
    setMsg(null);
    if (!("geolocation" in navigator)) {
      setMsg("Tu navegador no permite geolocalización.");
      setPhase("error");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => fetchFor(pos.coords.latitude, pos.coords.longitude),
      (err) => {
        setMsg(
          err.code === err.PERMISSION_DENIED
            ? "Has denegado el acceso a tu ubicación."
            : "No hemos podido obtener tu ubicación."
        );
        setPhase("error");
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
  }

  // pide permiso al abrir
  useEffect(() => { requestLocation(); }, []);

  // dibuja / actualiza el mapa cuando hay resultados
  useEffect(() => {
    if (phase !== "ready" || !origin || !mapEl.current) return;
    let cancelled = false;

    loadLeaflet()
      .then((L) => {
        if (cancelled || !mapEl.current) return;
        if (!mapRef.current) {
          mapRef.current = L.map(mapEl.current, { scrollWheelZoom: false })
            .setView([origin.lat, origin.lon], 11);
          L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "© OpenStreetMap",
            maxZoom: 19,
          }).addTo(mapRef.current);
        }
        const map = mapRef.current;
        Object.values(markers.current).forEach((m: any) => map.removeLayer(m));
        markers.current = {};

        // marcador de la ubicación del usuario
        L.circleMarker([origin.lat, origin.lon], {
          radius: 8, color: "#2a7a9c", fillColor: "#2a7a9c", fillOpacity: 1, weight: 2,
        }).addTo(map).bindPopup("Tu ubicación");

        // marcadores de distribuidores
        const pts: [number, number][] = [[origin.lat, origin.lon]];
        for (const s of suppliers) {
          const mk = L.circleMarker([s.lat, s.lon], {
            radius: 7, color: "#1a1a1a",
            fillColor: s.official ? "#9b8b72" : "#fff", fillOpacity: 1, weight: 2,
          })
            .addTo(map)
            .bindPopup(`<b>${s.name}</b><br>${s.address}, ${s.city}<br>${s.distance_km} km`);
          mk.on("click", () => setSelected(s.id));
          markers.current[s.id] = mk;
          pts.push([s.lat, s.lon]);
        }
        map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
      })
      .catch(() => { /* sin mapa: la lista sigue funcionando */ });

    return () => { cancelled = true; };
  }, [phase, origin, suppliers]);

  // limpia el mapa al desmontar
  useEffect(() => () => {
    if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; }
  }, []);

  // centra el mapa y abre el popup al seleccionar en la lista
  function pick(s: Supplier) {
    setSelected(s.id);
    const map = mapRef.current;
    const mk = markers.current[s.id];
    if (map && mk) { map.setView([s.lat, s.lon], 13); mk.openPopup(); }
  }

  return (
    <div className="rs-overlay" onClick={onClose}>
      <div className="rs-panel rs-sup-panel" onClick={(e) => e.stopPropagation()}>
        <button className="rs-close" onClick={onClose} aria-label="Cerrar">×</button>

        <h2 className="rs-detail-title">Encuentra tu distribuidor</h2>
        {item && (
          <p className="rs-sup-lead">
            Dónde comprar <b>{item.title ?? item.sku}</b> cerca de ti.
          </p>
        )}

        {phase === "asking" && (
          <p className="rs-state">Solicitando tu ubicación…</p>
        )}

        {phase === "loading" && (
          <p className="rs-state">Buscando distribuidores cercanos…</p>
        )}

        {phase === "error" && (
          <div className="rs-sup-error">
            <p>{msg}</p>
            <div className="rs-sup-error-actions">
              <button className="rs-search-btn" onClick={requestLocation}>
                Reintentar
              </button>
              <button
                className="rs-cart-clear"
                onClick={() => fetchFor(DEMO_ORIGIN.lat, DEMO_ORIGIN.lon)}
              >
                Usar {DEMO_ORIGIN.label}
              </button>
            </div>
          </div>
        )}

        {phase === "ready" && (
          <div className="rs-sup-body">
            <ul className="rs-sup-list">
              {suppliers.map((s) => (
                <li
                  key={s.id}
                  className={`rs-sup-item${s.id === selected ? " is-active" : ""}`}
                  onClick={() => pick(s)}
                >
                  <div className="rs-sup-item-top">
                    <span className="rs-sup-name">{s.name}</span>
                    {s.distance_km != null && (
                      <span className="rs-sup-dist">{s.distance_km} km</span>
                    )}
                  </div>
                  {s.official && <span className="rs-sup-badge">Showroom oficial</span>}
                  <p className="rs-sup-addr">{s.address}, {s.postal_code} {s.city}</p>
                  <div className="rs-sup-actions">
                    <a href={`tel:${s.phone.replace(/\s/g, "")}`} onClick={(e) => e.stopPropagation()}>
                      {s.phone}
                    </a>
                    <a href={mapsLink(s)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                      Cómo llegar →
                    </a>
                  </div>
                </li>
              ))}
            </ul>
            <div className="rs-sup-map" ref={mapEl} />
          </div>
        )}
      </div>
    </div>
  );
}
