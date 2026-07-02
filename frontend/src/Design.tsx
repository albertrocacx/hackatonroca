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
import { SHOWCASE_PRODUCTS } from "./showcase";

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

// Botón añadir/quitar de la cesta en las tiras de productos. Cuando el producto ya
// está en la cesta, el hover avisa de que el clic lo quita.
function CartToggle({ inCart, onAdd, onRemove }: {
  inCart: boolean; onAdd: () => void; onRemove: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      className={inCart ? "is-added" : ""}
      title={inCart ? "Quitar de la cesta" : undefined}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={inCart ? onRemove : onAdd}
    >
      {inCart ? (hover ? "Quitar" : "✓ En la cesta") : "Añadir"}
    </button>
  );
}

// Mensajes de fase durante el render (30-90 s): cambian según el tiempo transcurrido
// para transmitir avance real aunque el backend no emita progreso.
const WAIT_PHASES: [number, string][] = [
  [0, "Estudiando las fotos reales de tus productos…"],
  [8, "Componiendo la distribución del espacio…"],
  [20, "Aplicando materiales, acabados e iluminación…"],
  [38, "Renderizando texturas y detalles…"],
  [60, "Últimos retoques, ya casi está…"],
];

// Producto mínimo que sabe pintar el escaparate de espera.
type ShowItem = {
  sku: string; title: string | null; image: string | null;
  price_rrp: number | null; finish?: string | null;
  collection?: string | null; category?: string | null;
};

// Escaparate de espera: primero desfilan los productos que están entrando en el
// diseño (los de la cesta) y después la selección fija de catálogo (showcase.ts),
// 5 s cada uno, con fase y barra de progreso asintótica. Convierte los 30-90 s
// del render en anticipación. Sin la selección fija, con 1-2 productos en la
// cesta el carrusel se repetiría sin parar.
function DesignShowcase({ items, secs }: { items: ShowItem[]; secs: number }) {
  const [idx, setIdx] = useState(0);
  // Barajado una vez por render (el componente se monta al empezar cada espera),
  // sin lo que ya desfila desde la cesta.
  const [extras] = useState<ShowItem[]>(() => {
    const inCart = new Set(items.map((it) => it.sku));
    return SHOWCASE_PRODUCTS
      .filter((p) => !inCart.has(p.sku))
      .sort(() => Math.random() - 0.5);
  });
  const seq = [...items, ...extras];
  useEffect(() => {
    if (seq.length < 2) return;
    const t = window.setInterval(() => setIdx((i) => i + 1), 5000);
    return () => window.clearInterval(t);
  }, [seq.length]);

  const pos = idx % seq.length;
  const inCartPhase = pos < items.length;
  const p = seq[pos];
  const phase = WAIT_PHASES.reduce((msg, [t, s]) => (secs >= t ? s : msg), WAIT_PHASES[0][1]);
  // Se acerca a 96% sin llegar nunca: no miente si el render tarda más de lo típico.
  const pct = Math.min(96, Math.round(100 * (1 - Math.exp(-secs / 30))));
  // Los de catálogo van con su área delante ("Espejos e iluminación · Luna · 302 €")
  const meta = [!inCartPhase ? p.category : null, p.collection, p.finish].filter(Boolean);
  if (p.price_rrp != null) meta.push(eur(p.price_rrp));

  return (
    <div className="rs-design-wait">
      <span className="rs-design-wait-tag">
        {inCartPhase
          ? `Colocando en tu baño${items.length > 1 ? ` · ${pos + 1} de ${items.length}` : ""}`
          : "Mientras tanto, del catálogo Roca"}
      </span>
      <div className="rs-design-wait-img">
        <img key={`${p.sku}-${idx}`} src={p.image ?? PLACEHOLDER_IMG} alt="" />
      </div>
      <div>
        <p className="rs-design-wait-name">{p.title ?? p.sku}</p>
        <p className="rs-design-wait-meta">{meta.join(" · ")}</p>
      </div>
      {inCartPhase && items.length > 1 && items.length <= 10 && (
        <div className="rs-design-wait-dots">
          {items.map((it, i) => (
            <i key={it.sku} className={i === pos ? "is-on" : ""} />
          ))}
        </div>
      )}
      <div className="rs-design-wait-bar"><i style={{ width: `${pct}%` }} /></div>
      <p className="rs-design-wait-phase">{phase}</p>
      <p className="rs-design-hint">Suele tardar entre 30 y 90 segundos · {secs}s</p>
    </div>
  );
}

// Comparador antes/después: las dos imágenes superpuestas y un divisor arrastrable.
// La foto original ocupa el lado izquierdo del divisor y el render el derecho.
function CompareSlider({ before, after }: { before: string; after: string }) {
  const [pos, setPos] = useState(50);   // % del ancho donde está el divisor
  const boxRef = useRef<HTMLDivElement>(null);

  function moveTo(clientX: number) {
    const r = boxRef.current?.getBoundingClientRect();
    if (!r || r.width === 0) return;
    setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  }

  return (
    <div
      ref={boxRef}
      className="rs-design-compare"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        moveTo(e.clientX);
      }}
      onPointerMove={(e) => { if (e.buttons & 1) moveTo(e.clientX); }}
    >
      <img src={after} alt="Diseño de baño generado" draggable={false} />
      <img
        className="rs-compare-before"
        src={before}
        alt="Tu baño actual"
        draggable={false}
        style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}
      />
      <span className="rs-compare-tag" style={{ left: 8, opacity: pos < 14 ? 0 : 1 }}>
        Antes
      </span>
      <span className="rs-compare-tag" style={{ right: 8, opacity: pos > 86 ? 0 : 1 }}>
        Después
      </span>
      <div
        className="rs-compare-handle"
        style={{ left: `${pos}%` }}
        role="slider"
        aria-label="Comparar antes y después"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pos)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") { e.preventDefault(); setPos((p) => Math.max(0, p - 5)); }
          if (e.key === "ArrowRight") { e.preventDefault(); setPos((p) => Math.min(100, p + 5)); }
        }}
      >
        <i>
          <svg width="16" height="12" viewBox="0 0 16 12" fill="none"
               stroke="currentColor" strokeWidth="1.6"
               strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 1 1 6l4 5" />
            <path d="M11 1l4 5-4 5" />
          </svg>
        </i>
      </div>
    </div>
  );
}

export default function Design({ ready, onClose }: Props) {
  const { lines, addToCart, remove } = useCart();
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
  const [before, setBefore] = useState<string | null>(null);  // foto usada en el render actual
  const [followup, setFollowup] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // --- renueva tu baño (análisis de la foto) ---
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<RenewalItem[] | null>(null);
  const [view, setView] = useState<"render" | "analysis">("render");

  // "en la cesta" se deriva del carrito real: refleja también quitados desde el cajón
  const inCart = new Set(lines.map((l) => l.item.sku));

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
      // El "antes" del comparador es la foto enviada en este render. Una iteración
      // (instruction sin skus) edita el render previo, así que conserva su "antes";
      // un render nuevo sin foto lo limpia (no hay nada que comparar).
      if (!(req.instruction && req.skus.length === 0)) setBefore(req.room_image ?? null);
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
  }

  // Quita de la cesta y desmárcalo para el próximo render.
  function removePick(sku: string) {
    remove(sku);
    setSel((prev) => {
      const next = new Set(prev);
      next.delete(sku);
      return next;
    });
  }

  const working = busy || analyzing;
  // Productos que enseña el escaparate de espera: los marcados de la cesta;
  // en una iteración sin skus (refine), los del render anterior.
  const selectedItems = usable.filter((l) => sel.has(l.item.sku)).map((l) => l.item);
  const waitItems = selectedItems.length > 0 ? selectedItems : (result?.products ?? []);
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
              waitItems.length > 0 ? (
                <DesignShowcase items={waitItems} secs={secs} />
              ) : (
                <div className="rs-design-progress">
                  <i className="rs-chat-pulse" />
                  <p>Diseñando tu baño… {secs}s</p>
                  <p className="rs-design-hint">Suele tardar entre 30 y 90 segundos.</p>
                </div>
              )
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
                              <CartToggle
                                inCart={inCart.has(p.sku)}
                                onAdd={() => addPick(p)}
                                onRemove={() => removePick(p.sku)}
                              />
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
                {before ? (
                  <>
                    <CompareSlider
                      before={before}
                      after={`data:image/png;base64,${result.image_b64}`}
                    />
                    <p className="rs-design-hint rs-compare-hint">
                      Arrastra el divisor para comparar tu baño con el nuevo diseño.
                    </p>
                  </>
                ) : (
                  <img
                    className="rs-design-img"
                    src={`data:image/png;base64,${result.image_b64}`}
                    alt="Diseño de baño generado"
                  />
                )}
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
                      <CartToggle
                        inCart={inCart.has(p.sku)}
                        onAdd={() => addPick({
                          sku: p.sku, title: p.title, image: p.image,
                          price_rrp: p.price_rrp, finish: p.finish,
                          collection: p.collection,
                        })}
                        onRemove={() => removePick(p.sku)}
                      />
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
