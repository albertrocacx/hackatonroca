import { useEffect, useState } from "react";
import Tile, { PLACEHOLDER_IMG } from "./Tile";
import type { ModelCard, ShopItem } from "./api";

function price(p: number | null) {
  if (p == null) return null;
  return p.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function ProductCard({
  card, onOpen, onBuyOnline, onFindLocal,
}: {
  card: ModelCard;
  onOpen: (sku: string) => void;
  // Botones de compra (los estiliza el colega; aquí llegan ya cableados a
  // carrito online / buscador de distribuidores). La tarjeta solo construye el ShopItem.
  onBuyOnline?: (item: ShopItem) => void;
  onFindLocal?: (item: ShopItem) => void;
}) {
  const [idx, setIdx] = useState(card.default ?? 0);
  // nueva búsqueda / cambio de datos -> vuelve a la variante por defecto
  useEffect(() => { setIdx(card.default ?? 0); }, [card]);

  const v = card.variants[idx] ?? card.variants[0];
  if (!v) return null;

  // ShopItem de la variante mostrada (el acabado seleccionado con los thumbnails).
  // `online`: el buscador lo expondrá (flag ecommerce); si falta, se asume disponible.
  const shopItem: ShopItem = {
    sku: v.sku, title: card.title, image: v.image, price_rrp: v.price_rrp,
    finish: v.finish, collection: card.collection, online: (v as any).online,
  };
  const canBuyOnline = shopItem.online !== false;

  return (
    <article className="rs-card">
      <div className="rs-card-main">
        <div className="rs-card-tilewrap" onClick={() => onOpen(v.sku)}>
          <Tile image={v.image} title={card.title} />
          {(onBuyOnline || onFindLocal) && (
            <div className="rs-buy" onClick={(e) => e.stopPropagation()}>
              {onBuyOnline && (
                <button
                  type="button"
                  className="rs-buy-online"
                  disabled={!canBuyOnline}
                  title={canBuyOnline ? "" : "No disponible para compra online"}
                  onClick={() => onBuyOnline(shopItem)}
                >
                  {canBuyOnline ? "Compra online" : "Solo en tienda"}
                </button>
              )}
              {onFindLocal && (
                <button
                  type="button"
                  className="rs-buy-local"
                  onClick={() => onFindLocal(shopItem)}
                >
                  Encuentra proveedor local
                </button>
              )}
            </div>
          )}
        </div>
        <div onClick={() => onOpen(v.sku)}>
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
