import { useState, useEffect, useRef, type FormEvent } from "react";
import "./styles.css";
import Facets from "./Facets";
import Chat, { type ChatMsg } from "./Chat";
import Tile, { PLACEHOLDER_IMG } from "./Tile";
import ProductCard from "./ProductCard";
import { useCart, CartDrawer } from "./cart";
import LocalSuppliers from "./LocalSuppliers";
import ProductCta from "./ProductCta";
import {
  search, suggest, getProduct, getHealth, streamChat, EMPTY_SELECTED,
  type ProductSummary, type ProductDetail, type Suggestion, type Filter,
  type Selected, type Facets as FacetsData, type ModelCard, type ShopItem,
} from "./api";

const CHAT_INTRO =
  "Hola. Dime qué buscas —un lavabo, un plato de ducha, una grifería— y te muestro opciones en la parrilla. Puedo filtrar por precio o acabado y afinar la búsqueda.";
const TOOL_LABEL: Record<string, string> = { search_catalog: "Buscando en el catálogo" };

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

// Panel al enfocar el buscador vacío (datos fake por ahora; luego se pueden cablear
// a un endpoint de "más buscadas").
const POPULAR_SEARCHES = [
  "Lavabos murales",
  "Inodoros suspendidos Rimless",
  "Grifería termostática de ducha",
  "Platos de ducha extraplanos",
  "Muebles de baño",
  "Mamparas de ducha",
];

const POPULAR_CATEGORIES = [
  "Lavabos",
  "Inodoros",
  "Grifería",
  "Platos de ducha",
  "Bañeras",
  "Muebles de baño",
  "Mamparas",
  "Accesorios",
];

function price(p: number | null) {
  if (p == null) return null;
  return p.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Combina la selección base (concepto) con los filtros auto-detectados por el autocompletado
function withAutoFilters(base: Selected, filters: Filter[]): Selected {
  const s: Selected = {
    ...base,
    finishes: [...base.finishes],
    price: { ...base.price },
  };
  for (const f of filters) {
    if (f.type === "finish") s.finishes = [...s.finishes, ...(f.values ?? [])];
    if (f.type === "price") s.price = { min: f.min_price ?? null, max: f.max_price ?? null };
  }
  return s;
}

function SearchIcon({ small = false }: { small?: boolean }) {
  const s = small ? 16 : 20;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="1.6">
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" strokeLinecap="round" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2l1.9 5.1L19 9l-5.1 1.9L12 16l-1.9-5.1L5 9l5.1-1.9L12 2z" />
      <path d="M19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9L19 14z" opacity=".7" />
    </svg>
  );
}

function CartIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="9" cy="20" r="1.4" />
      <circle cx="18" cy="20" r="1.4" />
      <path d="M2 3h3l2.2 12.3a1.5 1.5 0 0 0 1.5 1.2h8.4a1.5 1.5 0 0 0 1.5-1.2L21.5 7H6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function App() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<ModelCard[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [facets, setFacets] = useState<FacetsData | null>(null);
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- compra: carrito online + buscador de distribuidores offline ---
  const { addToCart, count: cartCount, setOpen: setCartOpen } = useCart();
  const [localItem, setLocalItem] = useState<ShopItem | null>(null);

  // Seam para los botones del buscador (los añade el colega):
  //   "Compra online"            -> addToCart(item)
  //   "Encuentra proveedor local"-> openLocalSuppliers(item)
  function openLocalSuppliers(item: ShopItem) { setLocalItem(item); }

  // --- chat IA (opcional) ---
  const [aiOpen, setAiOpen] = useState(false);
  const [chatReady, setChatReady] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [chatStatus, setChatStatus] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const sessionId = useRef<string | null>(null);

  // --- estado de la búsqueda actual (define el SCOPE) + selección de facetas ---
  const [baseText, setBaseText] = useState("");
  const [subcat, setSubcat] = useState<string | null>(null);
  const [sel, setSel] = useState<Selected>(EMPTY_SELECTED);
  const debounce = useRef<number | undefined>(undefined);

  // --- autocompletado ---
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [autoFilters, setAutoFilters] = useState<Filter[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef<HTMLDivElement>(null);
  const skipSuggest = useRef(false);

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
      } catch { /* silencioso */ }
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
      e.preventDefault();
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

  // Única función que llama al backend. No toca la selección de facetas (sólo la usa).
  async function runSearch(text: string, sc: string | null, s: Selected) {
    setLoading(true); setError(null); setDetail(null); setOpen(false);
    try {
      const r = await search(text, s, sc);
      setResults(r.results); setTotal(r.total); setFacets(r.facets);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  // Nueva búsqueda desde el buscador: fija SCOPE y resetea facetas (semilla = filtros auto)
  function doSearch(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    const s = withAutoFilters(EMPTY_SELECTED, autoFilters);
    setBaseText(q); setSubcat(null); setSel(s); setSubmitted(q);
    clearTimeout(debounce.current);
    runSearch(q, null, s);
  }

  // Búsqueda de texto directa desde el panel al enfocar (sugerencias más buscadas y
  // categorías populares). Robusta: usa el motor de /search (Azure/substring).
  function launchSearch(term: string) {
    skipSuggest.current = true;
    setQ(term);
    setBaseText(term); setSubcat(null); setSel(EMPTY_SELECTED); setSubmitted(term);
    setOpen(false);
    clearTimeout(debounce.current);
    runSearch(term, null, EMPTY_SELECTED);
  }

  function pickSuggestion(sug: Suggestion) {
    skipSuggest.current = true;
    setQ(sug.term);
    let base = EMPTY_SELECTED;
    let sc: string | null = null;
    if (sug.type === "category") base = { ...EMPTY_SELECTED, categories: [sug.term] };
    else if (sug.type === "collection") base = { ...EMPTY_SELECTED, collections: [sug.term] };
    else if (sug.type === "subcategory") sc = sug.term;
    const s = withAutoFilters(base, autoFilters);
    const label = [sug.term, ...autoFilters.map((f) => f.label)].filter(Boolean).join(" · ");
    setBaseText(""); setSubcat(sc); setSel(s); setSubmitted(label);
    clearTimeout(debounce.current);
    runSearch("", sc, s);
  }

  // Cambio en el sidebar: actualiza selección, mantiene SCOPE, re-busca con debounce
  function onFacetsChange(next: Selected) {
    setSel(next);
    clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => runSearch(baseText, subcat, next), 220);
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

  useEffect(() => {
    getHealth().then((h) => setChatReady(!!h.chat_ready)).catch(() => {});
  }, []);

  // Envía un turno al agente y consume el stream NDJSON, actualizando chat + parrilla.
  async function sendChat(text: string) {
    if (!text.trim() || chatBusy) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setChatBusy(true);
    setChatStatus("");
    let asstOpen = false;   // ¿hay una burbuja del asistente abierta a la que ir añadiendo texto?
    try {
      const view = {
        query: submitted,
        visible: results.slice(0, 12)
          .map((r) => r.variants[r.default]?.sku ?? r.variants[0]?.sku)
          .filter(Boolean),
      };
      for await (const ev of streamChat({ text, session_id: sessionId.current, view })) {
        if (ev.type === "text") {
          setChatStatus(null);
          if (!asstOpen) {
            asstOpen = true;
            setMessages((m) => [...m, { role: "assistant", text: ev.text }]);
          } else {
            setMessages((m) => {
              const c = m.slice();
              c[c.length - 1] = { ...c[c.length - 1], text: c[c.length - 1].text + ev.text };
              return c;
            });
          }
        } else if (ev.type === "tool") {
          setChatStatus(TOOL_LABEL[ev.name] ?? "");
        } else if (ev.type === "grid") {
          asstOpen = false;                       // tras la parrilla, el próximo texto abre burbuja nueva
          const q2 = ev.query ?? submitted;
          setResults(ev.data.results);
          setTotal(ev.data.total);
          setFacets(ev.data.facets);
          setSubmitted(q2);
          // el agente fija el SCOPE (texto + subcategoría) y refleja SUS filtros en el sidebar,
          // para que checkboxes y sliders muestren lo que Claude buscó (p. ej. precio máx 150).
          const f = ev.filters ?? {};
          setBaseText(q2);
          setSubcat(f.subcategory ?? null);
          setSel({
            ...EMPTY_SELECTED,
            categories: f.category ? [f.category] : [],
            collections: f.collection ? [f.collection] : [],
            finishes: f.finish ?? [],
            price: { min: f.min_price ?? null, max: f.max_price ?? null },
            length: { min: f.min_length ?? null, max: f.max_length ?? null },
            width: { min: f.min_width ?? null, max: f.max_width ?? null },
            height: { min: f.min_height ?? null, max: f.max_height ?? null },
          });
          setMessages((m) => [...m, { role: "note", text: "Resultados actualizados ←" }]);
        } else if (ev.type === "done") {
          if (ev.session_id) sessionId.current = ev.session_id;
        } else if (ev.type === "error") {
          setMessages((m) => [...m, { role: "error", text: ev.message }]);
        }
      }
    } catch (err) {
      setMessages((m) => [...m, { role: "error", text: `Error de conexión con el chat: ${err}` }]);
    } finally {
      setChatBusy(false);
      setChatStatus(null);
    }
  }

  // Botón "Búsqueda IA": abre el panel y siembra la conversación con la consulta actual.
  function openAI() {
    setAiOpen(true);
    const seed = q.trim();
    if (!chatReady) {
      if (messages.length === 0) {
        setMessages([{ role: "error",
          text: "Chat en modo demo. Para activarlo: ejecuta `claude setup-token`, añade CLAUDE_CODE_OAUTH_TOKEN a backend/.env y reinicia el servidor. La búsqueda ya funciona." }]);
      }
      return;
    }
    if (seed) sendChat(seed);
    else if (messages.length === 0) setMessages([{ role: "assistant", text: CHAT_INTRO }]);
  }

  function newChat() {
    sessionId.current = null;
    setMessages(chatReady ? [{ role: "assistant", text: CHAT_INTRO }] : []);
    setChatStatus(null);
  }

  return (
    <>
      <div className={`rs-app${aiOpen ? " rs-app--chat" : ""}`}>
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
              onFocus={() => setOpen(true)}
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

            {open && q.trim() === "" && (
              <div className="rs-suggest">
                <div className="rs-suggest-head">Sugerencias</div>
                {POPULAR_SEARCHES.map((term) => (
                  <button
                    type="button"
                    key={term}
                    className="rs-suggest-item rs-suggest-item--pop"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => launchSearch(term)}
                  >
                    <SearchIcon small />
                    <span className="rs-suggest-term">{term}</span>
                  </button>
                ))}
                <div className="rs-suggest-head">Categorías populares</div>
                <div className="rs-pop-cats">
                  {POPULAR_CATEGORIES.map((cat) => (
                    <button
                      type="button"
                      key={cat}
                      className="rs-pop-cat"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => launchSearch(cat)}
                    >
                      {cat}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {open && q.trim() !== "" && (suggestions.length > 0 || autoFilters.length > 0) && (
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
          <button type="submit" className="rs-search-btn">Buscar</button>
          <button type="button" className="rs-ai-btn" onClick={openAI} title="Buscar y conversar con IA">
            <SparkIcon />
            <span>Búsqueda IA</span>
          </button>
        </form>
        <button
          type="button"
          className="rs-cart-btn"
          onClick={() => setCartOpen(true)}
          aria-label={`Cesta (${cartCount})`}
          title="Cesta"
        >
          <CartIcon />
          {cartCount > 0 && <span className="rs-cart-badge">{cartCount}</span>}
        </button>
      </header>

      <div className="rs-layout">
        {facets && total !== null && total > 0 && (
          <aside className="rs-sidebar">
            <Facets facets={facets} selected={sel} onChange={onFacetsChange} />
          </aside>
        )}

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
            {results.map((c) => (
              <ProductCard
                key={c.model}
                card={c}
                onOpen={openProduct}
                onBuyOnline={addToCart}
                onFindLocal={openLocalSuppliers}
              />
            ))}
          </div>
        </main>
      </div>
      </div>

      {aiOpen && (
        <Chat
          messages={messages}
          status={chatStatus}
          busy={chatBusy}
          onSend={sendChat}
          onClose={() => setAiOpen(false)}
          onNew={newChat}
        />
      )}

      <CartDrawer />

      {localItem && (
        <LocalSuppliers item={localItem} onClose={() => setLocalItem(null)} />
      )}

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
                {detail.variants && detail.variants.length > 1 && (
                  <div className="rs-swatches rs-swatches--detail">
                    {detail.variants.map((vr) => (
                      <button
                        type="button"
                        key={vr.sku}
                        className={`rs-swatch${vr.sku === detail.sku ? " is-active" : ""}`}
                        title={vr.finish ?? ""}
                        aria-label={vr.finish ?? ""}
                        onClick={() => openProduct(vr.sku)}
                      >
                        <img src={vr.image ?? PLACEHOLDER_IMG} alt={vr.finish ?? ""} loading="lazy" />
                      </button>
                    ))}
                  </div>
                )}
                <ProductCta
                  priceType={detail.price_type}
                  item={{
                    sku: detail.sku, title: detail.title, image: detail.image,
                    price_rrp: detail.price_rrp, finish: detail.finish,
                    collection: detail.collection,
                    online: detail.price_type === "OnlineFrom",
                  }}
                  onBuyOnline={addToCart}
                  onFindLocal={openLocalSuppliers}
                />
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
