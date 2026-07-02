/**
 * Carrito de compra ONLINE (mock).
 *
 * Seam para el buscador: el botón "Compra online" del colega llama a `addToCart(item)`
 * con un ShopItem. Aquí vive el estado, la persistencia (localStorage) y el cajón (drawer).
 * El "pago" es simulado: no hay pasarela, solo confirmación visual.
 */
import {
  createContext, useContext, useState, useCallback, useEffect, type ReactNode,
} from "react";
import type { ShopItem } from "./api";

export interface CartLine {
  item: ShopItem;
  qty: number;
}

interface CartCtx {
  lines: CartLine[];
  count: number;              // nº total de unidades
  total: number;              // suma PVPR * qty
  open: boolean;
  addToCart: (item: ShopItem, qty?: number, openDrawer?: boolean) => void;
  setQty: (sku: string, qty: number) => void;
  remove: (sku: string) => void;
  clear: () => void;
  setOpen: (v: boolean) => void;
}

const Ctx = createContext<CartCtx | null>(null);
const STORAGE_KEY = "roca_cart_v1";

function load(): CartLine[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as CartLine[]) : [];
  } catch {
    return [];
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>(load);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(lines));
    } catch { /* cuota llena / modo privado: ignorar */ }
  }, [lines]);

  const addToCart = useCallback((item: ShopItem, qty = 1, openDrawer = true) => {
    setLines((prev) => {
      const i = prev.findIndex((l) => l.item.sku === item.sku);
      if (i === -1) return [...prev, { item, qty }];
      const next = prev.slice();
      next[i] = { ...next[i], qty: next[i].qty + qty };
      return next;
    });
    // abre el cajón al añadir para dar feedback, salvo que el llamante lo evite
    // (p. ej. añadir varios productos seguidos desde "Diseña tu baño")
    if (openDrawer) setOpen(true);
  }, []);

  const setQty = useCallback((sku: string, qty: number) => {
    setLines((prev) =>
      qty <= 0
        ? prev.filter((l) => l.item.sku !== sku)
        : prev.map((l) => (l.item.sku === sku ? { ...l, qty } : l))
    );
  }, []);

  const remove = useCallback((sku: string) => {
    setLines((prev) => prev.filter((l) => l.item.sku !== sku));
  }, []);

  const clear = useCallback(() => setLines([]), []);

  const count = lines.reduce((n, l) => n + l.qty, 0);
  const total = lines.reduce((s, l) => s + (l.item.price_rrp ?? 0) * l.qty, 0);

  return (
    <Ctx.Provider
      value={{ lines, count, total, open, addToCart, setQty, remove, clear, setOpen }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useCart() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useCart debe usarse dentro de <CartProvider>");
  return c;
}

function eur(n: number) {
  return n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function CartDrawer() {
  const { lines, count, total, open, setQty, remove, clear, setOpen } = useCart();
  const [done, setDone] = useState(false);

  if (!open) return null;

  function checkout() {
    // pago simulado: en producción, aquí iría la pasarela / creación de pedido.
    setDone(true);
  }

  return (
    <div className="rs-cart-overlay" onClick={() => setOpen(false)}>
      <aside className="rs-cart" onClick={(e) => e.stopPropagation()}>
        <div className="rs-cart-head">
          <span className="rs-cart-title">Cesta {count > 0 && `(${count})`}</span>
          <button className="rs-chat-x" aria-label="Cerrar" onClick={() => setOpen(false)}>
            <svg width="20" height="20" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.6">
              <line x1="5" y1="5" x2="19" y2="19" strokeLinecap="round" />
              <line x1="19" y1="5" x2="5" y2="19" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {done ? (
          <div className="rs-cart-empty">
            <p className="rs-cart-ok">✓ Pedido confirmado</p>
            <p>Gracias por tu compra. Recibirás la confirmación por correo (demo).</p>
            <button
              className="rs-cart-checkout"
              onClick={() => { clear(); setDone(false); setOpen(false); }}
            >
              Seguir comprando
            </button>
          </div>
        ) : lines.length === 0 ? (
          <div className="rs-cart-empty">
            <p>Tu cesta está vacía.</p>
            <p className="rs-cart-hint">Pulsa «Compra online» en un producto para añadirlo.</p>
          </div>
        ) : (
          <>
            <ul className="rs-cart-list">
              {lines.map((l) => (
                <li key={l.item.sku} className="rs-cart-line">
                  <div className="rs-cart-thumb">
                    {l.item.image
                      ? <img src={l.item.image} alt={l.item.title ?? ""} loading="lazy" />
                      : <span className="rs-tile-ph">Roca</span>}
                  </div>
                  <div className="rs-cart-info">
                    <p className="rs-cart-name">{l.item.title ?? l.item.sku}</p>
                    <p className="rs-cart-sub">
                      Ref: {l.item.sku}{l.item.finish ? ` · ${l.item.finish}` : ""}
                    </p>
                    <div className="rs-cart-qty">
                      <button aria-label="Quitar uno" onClick={() => setQty(l.item.sku, l.qty - 1)}>−</button>
                      <span>{l.qty}</span>
                      <button aria-label="Añadir uno" onClick={() => setQty(l.item.sku, l.qty + 1)}>+</button>
                      <button className="rs-cart-remove" onClick={() => remove(l.item.sku)}>Eliminar</button>
                    </div>
                  </div>
                  <div className="rs-cart-price">
                    {l.item.price_rrp != null ? `${eur(l.item.price_rrp * l.qty)} €` : "—"}
                  </div>
                </li>
              ))}
            </ul>

            <div className="rs-cart-foot">
              <div className="rs-cart-total">
                <span>Total</span>
                <b>{eur(total)} €</b>
              </div>
              <button className="rs-cart-checkout" onClick={checkout}>
                Tramitar pedido
              </button>
              <button className="rs-cart-clear" onClick={clear}>Vaciar cesta</button>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
