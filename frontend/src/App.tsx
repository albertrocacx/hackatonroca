import { useState, type FormEvent } from "react";
import { search, getProduct, type ProductSummary, type ProductDetail } from "./api";

const REL_LABELS: Record<string, string> = {
  compatible: "Compatible con",
  optional: "Opcionales",
  included: "Incluye",
  sparepart: "Se compone de",
};

function price(p: number | null) {
  return p == null ? "—" : `${p.toLocaleString("es-ES")} € (sin IVA)`;
}

export default function App() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ProductSummary[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doSearch(e: FormEvent) {
    e.preventDefault();
    setLoading(true); setError(null); setDetail(null);
    try {
      const r = await search(q);
      setResults(r.results); setTotal(r.total);
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
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div style={{ maxWidth: 760, margin: "40px auto", fontFamily: "system-ui, sans-serif", padding: "0 16px" }}>
      <h1>Buscador Roca <small style={{ color: "#888" }}>(PoC)</small></h1>

      <form onSubmit={doSearch} style={{ display: "flex", gap: 8, margin: "20px 0" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ej: lavabo Aloa, grifo negro, plato de ducha..."
          style={{ flex: 1, padding: "10px 12px", fontSize: 16 }}
        />
        <button type="submit" style={{ padding: "10px 20px", fontSize: 16 }}>Buscar</button>
      </form>

      {loading && <p>Buscando...</p>}
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {total !== null && !loading && <p style={{ color: "#666" }}>{total} resultados</p>}

      <ul style={{ listStyle: "none", padding: 0 }}>
        {results.map((r) => (
          <li key={r.sku} onClick={() => openProduct(r.sku)}
              style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12, marginBottom: 8, cursor: "pointer" }}>
            <strong>{r.title}</strong>
            <div style={{ color: "#666", fontSize: 14 }}>
              {r.sku} · {r.category}{r.collection ? ` · ${r.collection}` : ""}{r.finish ? ` · ${r.finish}` : ""}
            </div>
            <div style={{ fontSize: 14 }}>{price(r.price_rrp)}</div>
          </li>
        ))}
      </ul>

      {detail && (
        <div style={{ border: "2px solid #333", borderRadius: 8, padding: 16, marginTop: 20 }}>
          <button onClick={() => setDetail(null)} style={{ float: "right" }}>cerrar</button>
          <h2 style={{ marginTop: 0 }}>{detail.title}</h2>
          <p style={{ color: "#666" }}>{detail.sku} · {detail.category} · {price(detail.price_rrp)}</p>
          {detail.desc.marketing && <p>{detail.desc.marketing}</p>}
          {Object.entries(REL_LABELS).map(([key, label]) => {
            const items = (detail.relations as any)[key] as ProductSummary[];
            if (!items || items.length === 0) return null;
            return (
              <div key={key} style={{ marginTop: 12 }}>
                <h4 style={{ marginBottom: 4 }}>{label} ({items.length})</h4>
                <ul style={{ margin: 0 }}>
                  {items.slice(0, 10).map((it) => (
                    <li key={it.sku}>
                      <a href="#" onClick={(e) => { e.preventDefault(); openProduct(it.sku); }}>
                        {it.title}
                      </a> <span style={{ color: "#888" }}>· {price(it.price_rrp)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
