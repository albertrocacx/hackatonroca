import { useEffect, useState } from "react";
import { getProduct, type ProductDetail } from "./api";
import { PLACEHOLDER_IMG } from "./Tile";

function price(p: number | null) {
  if (p == null) return "—";
  return p.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
}

// Filas de la tabla: etiqueta + cómo extraer el valor de la ficha
const ROWS: [string, (p: ProductDetail) => string | number | null][] = [
  ["Colección", (p) => p.collection],
  ["Categoría", (p) => p.category],
  ["Subcategoría", (p) => p.subcategory],
  ["Acabado", (p) => p.finish],
  ["Medidas (mm)", (p) => p.dims],
  ["Acabados disponibles", (p) => p.variants.length],
];

export default function Compare({ skus, onClose, onOpen }: {
  skus: string[];
  onClose: () => void;
  onOpen: (sku: string) => void;
}) {
  const [items, setItems] = useState<ProductDetail[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all(skus.map(getProduct))
      .then((r) => { if (alive) setItems(r); })
      .catch((e) => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [skus]);

  return (
    <div className="rs-overlay" onClick={onClose}>
      <div className="rs-panel rs-panel--cmp" onClick={(e) => e.stopPropagation()}>
        <button className="rs-close" onClick={onClose} aria-label="Cerrar">×</button>
        <p className="rs-coll">Comparativa</p>
        <h2 className="rs-detail-title">{skus.length} productos</h2>

        {error && <p className="rs-state rs-error">{error}</p>}
        {!items && !error && <p className="rs-state">Cargando…</p>}

        {items && (
          <div className="rs-cmp-scroll">
            <table className="rs-cmp-table">
              <thead>
                <tr>
                  <th />
                  {items.map((p) => (
                    <th key={p.sku}>
                      <button type="button" className="rs-cmp-prod" onClick={() => onOpen(p.sku)}>
                        <span className="rs-cmp-img">
                          <img src={p.image ?? PLACEHOLDER_IMG} alt={p.title ?? ""} />
                        </span>
                        <span className="rs-cmp-title">{p.title}</span>
                        <span className="rs-cmp-ref">Ref. {p.sku}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>PVPR</td>
                  {items.map((p) => <td key={p.sku}><b>{price(p.price_rrp)}</b></td>)}
                </tr>
                {ROWS.map(([label, get]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    {items.map((p) => <td key={p.sku}>{get(p) ?? "—"}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
