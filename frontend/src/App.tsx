import { useState, type FormEvent } from "react";
import "./styles.css";
import { search, getProduct, type ProductSummary, type ProductDetail } from "./api";

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

function Tile({ image, title, label }: { image: string | null; title: string | null; label: string | null }) {
  const [broken, setBroken] = useState(false);
  if (image && !broken) {
    return (
      <div className="rs-tile">
        <img src={image} alt={title ?? ""} loading="lazy" onError={() => setBroken(true)} />
      </div>
    );
  }
  return <div className="rs-tile"><span className="rs-tile-ph">{label ?? "Roca"}</span></div>;
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

  async function doSearch(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setLoading(true); setError(null); setDetail(null);
    try {
      const r = await search(q);
      setResults(r.results); setTotal(r.total); setSubmitted(q);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
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
        <a className="rs-logo" href="#" onClick={(e) => e.preventDefault()}>Roca</a>
        <form className="rs-searchform" onSubmit={doSearch}>
          <div className="rs-searchbox">
            <SearchIcon />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Introduce tu búsqueda"
              aria-label="Buscar"
              autoFocus
            />
            {q && (
              <button type="button" className="rs-clear" aria-label="Borrar" onClick={() => setQ("")}>
                <svg width="18" height="18" viewBox="0 0 24 24" stroke="#1a1a1a" strokeWidth="1.6">
                  <line x1="5" y1="5" x2="19" y2="19" strokeLinecap="round" />
                  <line x1="19" y1="5" x2="5" y2="19" strokeLinecap="round" />
                </svg>
              </button>
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
              <Tile image={r.image} title={r.title} label={r.category} />
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
              <Tile image={detail.image} title={detail.title} label={detail.category} />
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
