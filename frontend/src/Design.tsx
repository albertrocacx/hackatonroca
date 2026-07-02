/**
 * "Diseña tu baño": overlay a pantalla completa (mismo patrón que CartDrawer).
 *
 * Tres caminos sobre el mismo panel:
 *  - sin foto del espacio: la IA compone el baño desde cero con los productos elegidos;
 *  - con foto: integra los productos en el espacio real del usuario;
 *  - con foto y (típicamente) cesta vacía: "renueva tu baño" — Claude detecta los
 *    elementos existentes en la foto y propone sustitutos Roca por cada uno, que el
 *    usuario añade a la cesta con un clic.
 *
 * Los productos salen de la cesta (cero estado nuevo) y el resultado permite iterar
 * ("pon el grifo en negro") y comprar lo que se ve.
 */
import { useEffect, useRef, useState } from "react";
import { useCart } from "./cart";
import {
  designBathroom, analyzeBathroom,
  type DesignResponse, type RenewalItem, type RenewalProduct,
} from "./api";
import { PLACEHOLDER_IMG } from "./Tile";

const STYLES = ["Moderno", "Nórdico", "Clásico", "Industrial", "Mediterráneo", "Spa"];
const MAX_UPLOAD_PX = 1600;   // la foto del espacio se reduce en cliente antes de subirla

interface Props {
  ready: boolean;                          // health.design_ready
  onClose: () => void;
}

function eur(n: number | null) {
  if (n == null) return "—";
  return `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

// Lee y reduce la foto a JPEG (dataURL) para no mandar 10 MB al backend.
function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, MAX_UPLOAD_PX / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Imagen no válida")); };
    img.src = url;
  });
}

export default function Design({ ready, onClose }: Props) {
  const { lines, addToCart } = useCart();
  const usable = lines.filter((l) => l.item.image);
  const [sel, setSel] = useState<Set<string>>(
    () => new Set(usable.slice(0, 7).map((l) => l.item.sku))
  );
  const [room, setRoom] = useState<string | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [styleText, setStyleText] = useState("");
  const [busy, setBusy] = useState(false);
  const [secs, setSecs] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DesignResponse | null>(null);
  const [followup, setFollowup] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // --- renueva tu baño (análisis de la foto) ---
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<RenewalItem[] | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"render" | "analysis">("render");

  useEffect(() => {
    if (!busy) return;
    setSecs(0);
    const t = window.setInterval(() => setSecs((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function toggle(sku: string) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  }

  async function onFile(f: File | undefined) {
    if (!f) return;
    try {
      setRoom(await fileToDataUrl(f));
      setError(null);
    } catch {
      setError("No se pudo leer la imagen.");
    }
  }

  async function run(req: Parameters<typeof designBathroom>[0]) {
    setBusy(true);
    setError(null);
    try {
      setResult(await designBathroom(req));
      setFollowup("");
      setView("render");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function generate() {
    const skus = [...sel];
    if (skus.length === 0) return;
    run({
      skus,
      room_image: room,
      style: styleText.trim() || style,
      session_id: result?.session_id ?? null,
    });
  }

  function refine() {
    const t = followup.trim();
    if (!t || !result) return;
    run({ skus: [], instruction: t, session_id: result.session_id });
  }

  async function analyze() {
    if (!room) return;
    setAnalyzing(true);
    setError(null);
    try {
      const r = await analyzeBathroom(room);
      setAnalysis(r.items);
      setView("analysis");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzing(false);
    }
  }

  // Añade a la cesta SIN abrir el cajón (se sigue trabajando en el panel) y deja el
  // producto marcado para el próximo render.
  function addPick(p: RenewalProduct) {
    addToCart({
      sku: p.sku, title: p.title, image: p.image,
      price_rrp: p.price_rrp, finish: p.finish, collection: p.collection,
    }, 1, false);
    setSel((prev) => new Set(prev).add(p.sku));
    setAdded((prev) => new Set(prev).add(p.sku));
  }

  const working = busy || analyzing;
  const total = result?.products.reduce((s, p) => s + (p.price_rrp ?? 0), 0) ?? 0;
  const showAnalysis = view === "analysis" && analysis !== null;

  return (
    <div className="rs-design-overlay" onClick={onClose}>
      <section className="rs-design" onClick={(e) => e.stopPropagation()}>
        <header className="rs-design-head">
          <span className="rs-cart-title">Diseña tu baño</span>
          <button className="rs-chat-x" aria-label="Cerrar" onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.6">
              <line x1="5" y1="5" x2="19" y2="19" strokeLinecap="round" />
              <line x1="19" y1="5" x2="5" y2="19" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="rs-design-body">
          <div className="rs-design-controls">
            {!ready && (
              <p className="rs-design-warn">
                El motor de imagen no está configurado en el backend (falta el deployment
                gpt-image en Foundry). El resto de la app funciona con normalidad.
              </p>
            )}

            <h3>1 · Productos de tu cesta</h3>
            {lines.length === 0 ? (
              <p className="rs-design-hint">
                Tu cesta está vacía. Añade productos desde el buscador, o sube una foto de
                tu baño actual (paso 2) y te proponemos productos Roca para renovarlo.
              </p>
            ) : (
              <ul className="rs-design-products">
                {lines.map((l) => {
                  const noImg = !l.item.image;
                  return (
                    <li key={l.item.sku} className={noImg ? "is-disabled" : ""}>
                      <label>
                        <input
                          type="checkbox"
                          disabled={noImg || working}
                          checked={sel.has(l.item.sku)}
                          onChange={() => toggle(l.item.sku)}
                        />
                        <img src={l.item.image ?? PLACEHOLDER_IMG} alt="" loading="lazy" />
                        <span className="rs-design-pname">
                          {l.item.title ?? l.item.sku}
                          {l.item.finish && <em> · {l.item.finish}</em>}
                          {noImg && <em> · sin foto de catálogo</em>}
                        </span>
                        <span className="rs-design-pprice">{eur(l.item.price_rrp)}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}

            <h3>2 · Tu espacio (opcional)</h3>
            <p className="rs-design-hint">
              Sube una foto de tu baño o de un espacio vacío y colocaremos los productos ahí.
              Sin foto, la IA crea el baño desde cero.
            </p>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => onFile(e.target.files?.[0])}
            />
            {room ? (
              <>
                <div className="rs-design-room">
                  <img src={room} alt="Tu espacio" />
                  <button type="button" disabled={working} onClick={() => { setRoom(null); if (fileRef.current) fileRef.current.value = ""; }}>
                    Quitar foto
                  </button>
                </div>
                <button
                  type="button"
                  className="rs-design-analyze"
                  disabled={working || !ready}
                  onClick={analyze}
                >
                  {analyzing ? "Analizando tu baño…" : "Proponme productos para renovarlo"}
                </button>
                <p className="rs-design-hint">
                  Detectamos lo que hay en tu baño y te sugerimos sustitutos Roca para
                  añadir a la cesta.
                </p>
              </>
            ) : (
              <button type="button" className="rs-design-upload" disabled={working}
                      onClick={() => fileRef.current?.click()}>
                Subir foto de tu espacio
              </button>
            )}

            <h3>3 · Estilo</h3>
            <div className="rs-design-styles">
              {STYLES.map((s) => (
                <button
                  type="button"
                  key={s}
                  className={`rs-pop-cat${style === s && !styleText.trim() ? " is-active" : ""}`}
                  disabled={working}
                  onClick={() => { setStyle(style === s ? null : s); setStyleText(""); }}
                >
                  {s}
                </button>
              ))}
            </div>
            <input
              className="rs-design-styletext"
              value={styleText}
              disabled={working}
              placeholder="…o descríbelo: «minimalista en tonos arena»"
              onChange={(e) => setStyleText(e.target.value)}
            />

            <button
              type="button"
              className="rs-cart-checkout rs-design-go"
              disabled={working || !ready || sel.size === 0}
              onClick={generate}
            >
              {busy ? "Generando…" : result ? "Volver a generar" : "Generar mi baño"}
            </button>
            {!working && ready && sel.size === 0 && (
              <p className="rs-design-hint">
                Marca al menos un producto de la cesta para poder generar.
              </p>
            )}
            {error && <p className="rs-design-error">{error}</p>}
          </div>

          <div className="rs-design-result">
            {result && analysis && !working && (
              <div className="rs-design-tabs">
                <button type="button" className={view === "render" ? "is-active" : ""}
                        onClick={() => setView("render")}>Tu baño</button>
                <button type="button" className={view === "analysis" ? "is-active" : ""}
                        onClick={() => setView("analysis")}>Propuestas de renovación</button>
              </div>
            )}

            {busy ? (
              <div className="rs-design-progress">
                <i className="rs-chat-pulse" />
                <p>Diseñando tu baño… {secs}s</p>
                <p className="rs-design-hint">Suele tardar entre 30 y 90 segundos.</p>
              </div>
            ) : analyzing ? (
              <div className="rs-design-progress">
                <i className="rs-chat-pulse" />
                <p>Analizando tu baño y buscando productos Roca…</p>
                <p className="rs-design-hint">Unos 20 segundos.</p>
              </div>
            ) : showAnalysis ? (
              analysis.length === 0 ? (
                <div className="rs-design-empty">
                  <p>No hemos reconocido elementos sustituibles en la foto. Prueba con
                  una imagen más general del baño.</p>
                </div>
              ) : (
                <>
                  <p className="rs-design-hint">
                    Esto es lo que hemos visto en tu baño. Añade a la cesta los sustitutos
                    que te gusten y pulsa «Generar mi baño» para verlos en tu espacio.
                  </p>
                  {analysis.map((it) => (
                    <div key={it.label} className="rs-design-reno">
                      <h4>{it.label}</h4>
                      {it.products.length === 0 ? (
                        <p className="rs-design-hint">Sin resultados en el catálogo.</p>
                      ) : (
                        <ul className="rs-design-strip">
                          {it.products.map((p) => (
                            <li key={p.sku}>
                              <img src={p.image ?? PLACEHOLDER_IMG} alt="" loading="lazy" />
                              <div>
                                <p className="rs-design-pname" title={p.title ?? p.sku}>
                                  {p.title ?? p.sku}
                                </p>
                                <p className="rs-design-pprice">{eur(p.price_rrp)}</p>
                              </div>
                              <button
                                type="button"
                                className={added.has(p.sku) ? "is-added" : ""}
                                onClick={() => addPick(p)}
                              >
                                {added.has(p.sku) ? "✓ En la cesta" : "Añadir"}
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </>
              )
            ) : result ? (
              <>
                <img
                  className="rs-design-img"
                  src={`data:image/png;base64,${result.image_b64}`}
                  alt="Diseño de baño generado"
                />
                <div className="rs-design-refine">
                  <input
                    value={followup}
                    placeholder="Pide un cambio: «pon el grifo en negro», «suelo de madera»…"
                    onChange={(e) => setFollowup(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") refine(); }}
                  />
                  <button type="button" disabled={!followup.trim()} onClick={refine}>
                    Aplicar
                  </button>
                </div>
                <ul className="rs-design-strip">
                  {result.products.map((p) => (
                    <li key={p.sku}>
                      <img src={p.image ?? PLACEHOLDER_IMG} alt="" loading="lazy" />
                      <div>
                        <p className="rs-design-pname">{p.title ?? p.sku}</p>
                        <p className="rs-design-pprice">{eur(p.price_rrp)}</p>
                      </div>
                      <button
                        type="button"
                        className={added.has(p.sku) ? "is-added" : ""}
                        onClick={() => addPick({
                          sku: p.sku, title: p.title, image: p.image,
                          price_rrp: p.price_rrp, finish: p.finish,
                          collection: p.collection,
                        })}
                      >
                        {added.has(p.sku) ? "✓ En la cesta" : "Añadir"}
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="rs-design-total">
                  Total productos del diseño: <b>{eur(total)}</b>
                </p>
                {result.skipped.length > 0 && (
                  <p className="rs-design-hint">
                    Sin foto de catálogo (no incluidos): {result.skipped.join(", ")}
                  </p>
                )}
                <p className="rs-design-disclaimer">
                  Imagen generada por IA a partir de las fotos reales de producto:
                  representación orientativa, no un render exacto.
                </p>
              </>
            ) : (
              <div className="rs-design-empty">
                <p>Elige productos, sube tu espacio si quieres, y pulsa «Generar mi baño».</p>
                <p className="rs-design-hint">
                  ¿Empiezas de cero? Sube una foto de tu baño actual y pide propuestas
                  de renovación.
                </p>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
