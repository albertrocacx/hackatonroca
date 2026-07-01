import { useState, useEffect, useRef, type FormEvent } from "react";
import "./styles.css";
import {
  search, suggest, getProduct,
  type ProductSummary, type ProductDetail, type Suggestion, type Filter,
} from "./api";

const TYPE_LABEL: Record<string, string> = {
  category: "Categoría",
  subcategory: "Subcategoría",
  collection: "Colección",
};

const REL_LABELS: Record<string, string> = {
  compatible: "Compatible con",
  optional: "Productos opcionales",
  included: "Incluye",
  sparepart: "Se compone de",
};

function price(p: number | null) {
  if (p == null) return null;
  return p.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const PLACEHOLDER_IMG = "https://www.roca.es/o/roca-restyle-theme/images/product-thumbnail.jpg";

function Tile({ image, title }: { image: string | null; title: string | null }) {
  const [broken, setBroken] = useState(false);
  const src = image && !broken ? image : PLACEHOLDER_IMG;
  return (
    <div className="rs-tile">
      <img src={src} alt={title ?? ""} loading="lazy" onError={() => setBroken(true)} />
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="1.6">
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" strokeLinecap="round" />
    </svg>
  );
}

export default function App() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<ProductSummary[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- autocompletado ---
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [autoFilters, setAutoFilters] = useState<Filter[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);   // fila resaltada por teclado
  const boxRef = useRef<HTMLDivElement>(null);
  const skipSuggest = useRef(false);          // evita reabrir el desplegable al fijar el texto elegido

  useEffect(() => {
    const term = q.trim();
    if (!term) { setSuggestions([]); setAutoFilters([]); setActive(-1); return; }
    if (skipSuggest.current) { skipSuggest.current = false; return; }
    const t = setTimeout(async () => {
      try {
        const r = await suggest(q);
        setSuggestions(r.suggestions);
        setAutoFilters(r.filters);
        setActive(-1);
        setOpen(true);
      } catch { /* silencioso: el autocompletado es best-effort */ }
    }, 180);
    return () => clearTimeout(t);
  }, [q]);

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a <= 0 ? suggestions.length - 1 : a - 1));
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();          // evita el submit del formulario: usamos la fila resaltada
      pickSuggestion(suggestions[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  async function runSearch(text: string, concept: Suggestion | undefined, filters: Filter[]) {
    setLoading(true); setError(null); setDetail(null); setOpen(false);
    const label = [concept?.term ?? text, ...filters.map((f) => f.label)].filter(Boolean).join(" · ");
    try {
      const r = await search(concept ? "" : text, { concept, filters });
      setResults(r.results); setTotal(r.total); setSubmitted(label);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  function doSearch(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    // Enter sin elegir: busca la frase de intención como texto libre + filtros
    runSearch(q, undefined, autoFilters);
  }

  function pickSuggestion(s: Suggestion) {
    skipSuggest.current = true;
    setQ(s.term);                 // refleja en el campo la opción elegida
    runSearch(s.term, s, autoFilters);
  }

  async function openProduct(sku: string) {
    setError(null);
    try {
      setDetail(await getProduct(sku));
      window.scrollTo({ top: 0 });
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <>
      <header className="rs-header">
        <a className="rs-logo" href="#" onClick={(e) => e.preventDefault()} aria-label="Roca">
          <img src="https://www.roca.es/documents/20126/346080475/roca-logo.svg/4dc29d13-1df3-b628-786b-7c63db57cdcd?t=1753429104544" alt="Roca" />
        </a>
        <form className="rs-searchform" onSubmit={doSearch}>
          <div className="rs-searchbox" ref={boxRef}>
            <SearchIcon />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              onFocus={() => (suggestions.length || autoFilters.length) && setOpen(true)}
              placeholder="Introduce tu búsqueda"
              aria-label="Buscar"
              autoFocus
            />
            {q && (
              <button type="button" className="rs-clear" aria-label="Borrar" onClick={() => { setQ(""); setOpen(false); }}>
                <svg width="18" height="18" viewBox="0 0 24 24" stroke="#1a1a1a" strokeWidth="1.6">
                  <line x1="5" y1="5" x2="19" y2="19" strokeLinecap="round" />
                  <line x1="19" y1="5" x2="5" y2="19" strokeLinecap="round" />
                </svg>
              </button>
            )}

            {open && (suggestions.length > 0 || autoFilters.length > 0) && (
              <div className="rs-suggest">
                {autoFilters.length > 0 && (
                  <div className="rs-suggest-filters">
                    {autoFilters.map((f, i) => (
                      <span key={i} className={`rs-chip rs-chip-${f.type}`}>
                        {f.type === "finish" ? "Acabado" : "Precio"}: {f.label}
                      </span>
                    ))}
                  </div>
                )}
                {suggestions.map((s, i) => (
                  <button
                    type="button"
                    key={`${s.term}-${i}`}
                    className={`rs-suggest-item${i === active ? " is-active" : ""}`}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => pickSuggestion(s)}
                  >
                    <span className="rs-suggest-term">{s.term}</span>
                    <span className="rs-suggest-tags">
                      <span className="rs-suggest-type">{TYPE_LABEL[s.type]}</span>
                      {s.source === "semantic" && <span className="rs-suggest-sem">similar</span>}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </form>
      </header>

      <main className="rs-main">
        {loading && <p className="rs-state">Buscando…</p>}
        {error && <p className="rs-state rs-error">{error}</p>}

        {!loading && total !== null && (
          <h1 className="rs-count">{submitted} <span>({total})</span></h1>
        )}

        {!loading && total === 0 && (
          <p className="rs-state">No se han encontrado productos para «{submitted}».</p>
        )}

        <div className="rs-grid">
          {results.map((r) => (
            <article key={r.sku} className="rs-card" onClick={() => openProduct(r.sku)}>
              <Tile image={r.image} title={r.title} />
              {r.collection && <p className="rs-coll">{r.collection}</p>}
              <h3 className="rs-title">{r.title}</h3>
              <div className="rs-meta">
                <div>Ref: {r.sku}</div>
                {r.dims && <div>{r.dims}</div>}
                {r.finish && <div>{r.finish}</div>}
              </div>
              {r.price_rrp != null && (
                <div className="rs-price">PVPR: <b>{price(r.price_rrp)} €</b></div>
              )}
            </article>
          ))}
        </div>
      </main>

      {detail && (
        <div className="rs-overlay" onClick={() => setDetail(null)}>
          <div className="rs-panel" onClick={(e) => e.stopPropagation()}>
            <button className="rs-close" onClick={() => setDetail(null)} aria-label="Cerrar">×</button>
            <div className="rs-detail-top">
              <Tile image={detail.image} title={detail.title} />
              <div>
                {detail.collection && <p className="rs-coll">{detail.collection}</p>}
                <h2 className="rs-detail-title">{detail.title}</h2>
                <div className="rs-meta">
                  <div>Ref: {detail.sku}</div>
                  {detail.category && <div>{detail.category}{detail.subcategory ? ` · ${detail.subcategory}` : ""}</div>}
                  {detail.dims && <div>{detail.dims}</div>}
                  {detail.finish && <div>{detail.finish}</div>}
                </div>
                {detail.price_rrp != null && (
                  <div className="rs-price">PVPR: <b>{price(detail.price_rrp)} €</b></div>
                )}
                {detail.desc.marketing && <p className="rs-detail-desc">{detail.desc.marketing}</p>}
              </div>
            </div>

            {Object.entries(REL_LABELS).map(([key, label]) => {
              const items = (detail.relations as any)[key] as ProductSummary[];
              if (!items || items.length === 0) return null;
              return (
                <div key={key} className="rs-rel">
                  <h4>{label} ({items.length})</h4>
                  <ul className="rs-rel-list">
                    {items.slice(0, 12).map((it) => (
                      <li key={it.sku} className="rs-rel-item" onClick={() => openProduct(it.sku)}>
                        <span style={{ color: "var(--ink)", whiteSpace: "normal" }}>{it.title}</span>
                        <span>{it.price_rrp != null ? `${price(it.price_rrp)} €` : "—"}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
