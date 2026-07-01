import { useEffect, useState } from "react";
import Tile, { PLACEHOLDER_IMG } from "./Tile";
import type { ModelCard } from "./api";

function price(p: number | null) {
  if (p == null) return null;
  return p.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function ProductCard({
  card, onOpen,
}: {
  card: ModelCard;
  onOpen: (sku: string) => void;
}) {
  const [idx, setIdx] = useState(card.default ?? 0);
  // nueva búsqueda / cambio de datos -> vuelve a la variante por defecto
  useEffect(() => { setIdx(card.default ?? 0); }, [card]);

  const v = card.variants[idx] ?? card.variants[0];
  if (!v) return null;

  return (
    <article className="rs-card">
      <div className="rs-card-main" onClick={() => onOpen(v.sku)}>
        <Tile image={v.image} title={card.title} />
        {card.collection && <p className="rs-coll">{card.collection}</p>}
        <h3 className="rs-title">{card.title}</h3>
        <div className="rs-meta">
          <div>Ref: {v.sku}</div>
          {v.dims && <div>{v.dims}</div>}
          {v.finish && <div>{v.finish}</div>}
        </div>
        {v.price_rrp != null && (
          <div className="rs-price">PVPR: <b>{price(v.price_rrp)} €</b></div>
        )}
      </div>

      {card.variants.length > 1 && (
        <div className="rs-swatches">
          {card.variants.map((vr, i) => (
            <button
              type="button"
              key={vr.sku}
              className={`rs-swatch${i === idx ? " is-active" : ""}`}
              title={vr.finish ?? ""}
              aria-label={vr.finish ?? ""}
              onClick={() => setIdx(i)}
            >
              <img src={vr.image ?? PLACEHOLDER_IMG} alt={vr.finish ?? ""} loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </article>
  );
}
