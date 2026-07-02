import { useState, useEffect, useRef, type FormEvent } from "react";
import "./styles.css";
import Facets from "./Facets";
import Chat, { type ChatMsg } from "./Chat";
import Tile, { PLACEHOLDER_IMG } from "./Tile";
import ProductCard from "./ProductCard";
import { useCart, CartDrawer } from "./cart";
import LocalSuppliers from "./LocalSuppliers";
import ProductCta from "./ProductCta";
import Design from "./Design";
import Compare from "./Compare";
import {
  search, suggest, getProduct, getHealth, streamChat, EMPTY_SELECTED, searchByImage,
  type ProductSummary, type ProductDetail, type Suggestion, type Filter, type AppliedTag,
  type Selected, type Facets as FacetsData, type ModelCard, type ShopItem,
  type ImageSearchGroup,
  type ImageSearchContext,
  type SortKey,
  type SelectedProduct,
} from "./api";
import { ImageDropPanel, CameraIcon, type Photo } from "./ImageSearch";
import { downscalePhoto } from "./imageUtils";
import { useSpeech, MicIcon } from "./speech";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "websort", label: "Recomendados" },
  { value: "relevance", label: "Relevancia semántica" },
  { value: "price_asc", label: "Precio: de menor a mayor" },
  { value: "price_desc", label: "Precio: de mayor a menor" },
  { value: "alpha_asc", label: "Alfabético (A–Z)" },
  { value: "alpha_desc", label: "Alfabético (Z–A)" },
];

const CHAT_INTRO =
  "Hola. Dime qué buscas —un lavabo, un plato de ducha, una grifería— y te muestro opciones en la parrilla. Puedo filtrar por precio o acabado y afinar la búsqueda.";
const TOOL_LABEL: Record<string, string> = {
  search_catalog: "Buscando en el catálogo",
  find_local_suppliers: "Buscando distribuidores cercanos",
  show_product: "Abriendo la ficha del producto",
};

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
  // último producto abierto: NO se borra al cerrar la ficha; es el "producto seleccionado"
  // que el chat usa para resolver "el manual de este producto" sin pedir el SKU.
  const [lastProduct, setLastProduct] = useState<SelectedProduct | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- búsqueda por imagen ---
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [imgPanelOpen, setImgPanelOpen] = useState(false);
  const [sameProduct, setSameProduct] = useState(true);
  const [imageGroups, setImageGroups] = useState<ImageSearchGroup[] | null>(null);
  // contexto de la última búsqueda por imagen: la Búsqueda IA parte de estas coincidencias
  const [imageContext, setImageContext] = useState<ImageSearchContext | null>(null);
  const [imageReady, setImageReady] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // --- compra: carrito online + buscador de distribuidores offline ---
  const { addToCart, count: cartCount, setOpen: setCartOpen } = useCart();
  const [localItem, setLocalItem] = useState<ShopItem | null>(null);

  // Seam para los botones del buscador (los añade el colega):
  //   "Compra online"            -> addToCart(item)
  //   "Encuentra proveedor local"-> openLocalSuppliers(item)
  function openLocalSuppliers(item: ShopItem) { setLocalItem(item); }

  // --- comparador: selección en el grid (modelo -> sku de la variante mostrada) ---
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [compareSkus, setCompareSkus] = useState<string[] | null>(null);
  const nSel = Object.keys(selected).length;

  function toggleSelect(model: string, sku: string) {
    setSelected((s) => {
      const c = { ...s };
      if (c[model]) delete c[model]; else c[model] = sku;
      return c;
    });
  }

  // --- diseña tu baño (render IA) ---
  const [designOpen, setDesignOpen] = useState(false);
  const [designReady, setDesignReady] = useState(false);

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
  // websort: la carga inicial es el escaparate; cada búsqueda nueva vuelve a "relevance"
  const [sort, setSort] = useState<SortKey>("websort");
  const debounce = useRef<number | undefined>(undefined);

  // --- filtros en móvil (el sidebar se pliega bajo un botón "Filtros") ---
  const [filtersOpen, setFiltersOpen] = useState(false);

  // --- interpretacion NL -> filtros aplicados (tags + sidebar) ---
  const [appliedTags, setAppliedTags] = useState<AppliedTag[]>([]);
  const [tagsHidden, setTagsHidden] = useState(false);
  const [correction, setCorrection] = useState<{ from: string; to: string } | null>(null);

  // --- autocompletado ---
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [autoFilters, setAutoFilters] = useState<Filter[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef<HTMLDivElement>(null);
  const skipSuggest = useRef(false);

  // --- búsqueda por voz: dicta la frase y, al callar, se lanza interpretada (auto=1) ---
  const voice = useSpeech({
    onInterim: (t) => { skipSuggest.current = true; setQ(t); },
    onFinal: (t) => {
      if (photos.length > 0) {
        setAppliedTags([]); setCorrection(null);   // en modo imagen no hay tags NL
        skipSuggest.current = true; setQ(t);
        runImageSearch(t);
      } else {
        launchSearch(t);
      }
    },
  });

  function toggleVoice() {
    if (!voice.listening) { setOpen(false); skipSuggest.current = true; setQ(""); }
    voice.toggle();
  }

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

  // Única función que llama al backend. Con auto=true (búsqueda nueva de texto), el LLM
  // del backend puede convertir atributos ("rojos") en filtros reales del catálogo y
  // detectar el orden pedido ("descendente por precio"): se reflejan en sidebar/selector
  // y el texto base queda limpio (solo el producto) para las re-consultas de facetas.
  async function runSearch(text: string, sc: string | null, s: Selected,
                           auto = false, sortKey: SortKey = sort) {
    setLoading(true); setError(null); setDetail(null); setOpen(false);
    setImageGroups(null);                    // una búsqueda de texto sale del modo imagen
    setImageContext(null);
    try {
      const r = await search(text, s, sc, auto, sortKey);
      setResults(r.results); setTotal(r.total); setFacets(r.facets);
      if (auto && r.auto) {
        const a = r.auto.applied;
        if (Object.keys(a).length > 0) {
          const dims = a.size?.dims ?? {};
          const rangeOf = (d?: { min: number | null; max: number | null }, prev?: { min: number | null; max: number | null }) =>
            d ? { min: d.min ?? null, max: d.max ?? null } : prev!;
          setSel({
            ...s,
            categories: a.category ?? s.categories,
            collections: a.collection ?? s.collections,
            finishes: a.finish ?? s.finishes,
            price: { min: a.min_price ?? s.price.min, max: a.max_price ?? s.price.max },
            length: rangeOf(dims.length, s.length),
            width: rangeOf(dims.width, s.width),
            height: rangeOf(dims.height, s.height),
          });
        }
        if (a.sort) setSort(a.sort);
        if (r.auto.search_text) setBaseText(r.auto.search_text);
        setAppliedTags(r.auto.tags ?? []); setTagsHidden(false);
        const corrected = r.auto.corrected && r.auto.corrected_query;
        setCorrection(corrected ? { from: text, to: r.auto.corrected_query! } : null);
        if (corrected) setSubmitted(r.auto.corrected_query!);
        // traza reducida para la demo: qué entendió el intérprete de la frase
        console.groupCollapsed(`%c[interpret] "${text}"`, "color:#1c5c6e;font-weight:bold");
        console.log("texto de búsqueda:", r.auto.search_text);
        console.log("filtros aplicados:", a);
        console.log("tags:", r.auto.tags ?? []);
        if (corrected) console.log("corrección:", text, "->", r.auto.corrected_query);
        console.groupEnd();
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  const MAX_PHOTOS = 6;

  async function addPhotos(files: FileList | File[]) {
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    const room = MAX_PHOTOS - photos.length;
    const add: Photo[] = [];
    for (const f of list.slice(0, room)) {
      try {
        const blob = await downscalePhoto(f);
        add.push({ id: `${f.name}-${f.size}-${Math.random()}`, blob, url: URL.createObjectURL(blob) });
      } catch { /* foto ilegible: se ignora */ }
    }
    if (add.length) {
      setPhotos((p) => [...p, ...add]);
      setImgPanelOpen(true);
      setImageContext(null);   // fotos nuevas: el contexto de la última búsqueda ya no las representa
    }
  }

  function removePhoto(id: string) {
    setPhotos((p) => {
      const ph = p.find((x) => x.id === id);
      if (ph) URL.revokeObjectURL(ph.url);
      return p.filter((x) => x.id !== id);
    });
    // las secciones por foto dejan de corresponder a las fotos actuales -> salir del modo distinct
    if (imageGroups) {
      setImageGroups(null); setImageContext(null);
      setResults([]); setTotal(null); setSubmitted("");
    }
  }

  // Búsqueda por imagen: las fotos mandan; el texto (si hay) filtra los matches visuales.
  // Los mejores matches (SKU + título) se guardan en imageContext para que la Búsqueda IA
  // parta de ellos ("esto que sale en mi foto").
  const topMatches = (cards: ModelCard[], n = 6) =>
    cards.slice(0, n)
      .map((c) => ({ sku: c.variants[c.default]?.sku ?? c.variants[0]?.sku, title: c.title }))
      .filter((m): m is { sku: string; title: string | null } => !!m.sku);

  async function runImageSearch(text: string): Promise<ImageSearchContext | null> {
    setLoading(true); setError(null); setDetail(null); setOpen(false); setImgPanelOpen(false);
    try {
      const mode = photos.length > 1 && !sameProduct ? "distinct" : "same";
      const r = await searchByImage(photos.map((p) => p.blob), text.trim(), mode);
      const nf = `${photos.length} foto${photos.length > 1 ? "s" : ""}`;
      setSubmitted(text.trim() ? `${text.trim()} · ${nf}` : `Búsqueda por imagen (${nf})`);
      setFacets(null);                       // sin sidebar en modo imagen
      const ctx: ImageSearchContext = {
        photos: photos.length, mode, refine: text.trim() || undefined, groups: [],
      };
      if ("groups" in r) {
        setImageGroups(r.groups);
        setResults([]);
        setTotal(r.groups.reduce((n, g) => n + g.total, 0));
        ctx.groups = r.groups.map((g) => ({ photo: g.photo, matches: topMatches(g.results) }));
      } else {
        setImageGroups(null);
        setResults(r.results);
        setTotal(r.total);
        ctx.groups = [{ photo: 0, matches: topMatches(r.results) }];
      }
      const ok = ctx.groups.some((g) => g.matches.length) ? ctx : null;
      setImageContext(ok);
      return ok;             // para encadenar (Búsqueda IA directa) sin esperar al re-render
    } catch (err) {
      setError(String(err));
      return null;
    } finally {
      setLoading(false);
    }
  }

  // Nueva búsqueda desde el buscador: con fotos manda la imagen; si no, /search?auto=1
  // interpreta la frase en el backend (erratas, filtros, tamaño, orden) en UNA llamada
  // y la respuesta fija sidebar, tags y banner de corrección (en runSearch).
  function doSearch(e: FormEvent) {
    e.preventDefault();
    if (photos.length > 0) {
      setAppliedTags([]); setCorrection(null);   // en modo imagen no hay tags NL
      runImageSearch(q);
      return;
    }
    if (!q.trim()) return;
    const s = withAutoFilters(EMPTY_SELECTED, autoFilters);
    setBaseText(q); setSubcat(null); setSel(s); setSubmitted(q); setSort("relevance");
    setAppliedTags([]); setCorrection(null); setTagsHidden(false);
    clearTimeout(debounce.current);
    runSearch(q, null, s, true, "relevance");
  }

  // Búsqueda de texto desde el panel al enfocar (interpretada) o desde el enlace
  // "buscar en su lugar por…" del banner (literal=true: sin reinterpretar, para que
  // el LLM no vuelva a "corregir" lo que el usuario pidió literalmente).
  function launchSearch(term: string, literal = false) {
    skipSuggest.current = true;
    setQ(term);
    setBaseText(term); setSubcat(null); setSel(EMPTY_SELECTED); setSubmitted(term);
    setSort("relevance");
    setAppliedTags([]); setCorrection(null); setTagsHidden(false); setOpen(false);
    clearTimeout(debounce.current);
    runSearch(term, null, EMPTY_SELECTED, !literal, "relevance");
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
    setBaseText(""); setSubcat(sc); setSel(s); setSubmitted(label); setSort("relevance");
    setAppliedTags([]); setCorrection(null);
    clearTimeout(debounce.current);
    runSearch("", sc, s, false, "relevance");
  }

  // ¿Sigue activo en la selección el filtro que representa este tag?
  function tagActive(tag: AppliedTag, s: Selected): boolean {
    switch (tag.id) {
      case "category": return s.categories.length > 0;
      case "collection": return s.collections.length > 0;
      case "finish": return s.finishes.length > 0;
      case "price": return s.price.min != null || s.price.max != null;
      case "size":
        return (tag.dimensions ?? []).some((d) => s[d].min != null || s[d].max != null);
      default: return true;
    }
  }

  // Quita un tag concreto: limpia esa parte de la selección y re-busca.
  function removeTag(tag: AppliedTag) {
    const next: Selected = {
      ...sel,
      categories: [...sel.categories], collections: [...sel.collections], finishes: [...sel.finishes],
      price: { ...sel.price }, length: { ...sel.length }, width: { ...sel.width }, height: { ...sel.height },
    };
    if (tag.id === "category") next.categories = [];
    else if (tag.id === "collection") next.collections = [];
    else if (tag.id === "finish") next.finishes = [];
    else if (tag.id === "price") next.price = { min: null, max: null };
    else if (tag.id === "size") for (const d of tag.dimensions ?? []) next[d] = { min: null, max: null };
    setSel(next);
    setAppliedTags((t) => t.filter((x) => x.id !== tag.id));
    clearTimeout(debounce.current);
    runSearch(baseText, subcat, next);
  }

  // Limpia todos los filtros aplicados (mantiene el texto/SCOPE de la búsqueda).
  function clearAllFilters() {
    setSel(EMPTY_SELECTED);
    setAppliedTags([]);
    clearTimeout(debounce.current);
    runSearch(baseText, subcat, EMPTY_SELECTED);
  }

  // Cambio en el sidebar: actualiza selección, reconcilia tags, mantiene SCOPE y re-busca
  function onFacetsChange(next: Selected) {
    setSel(next);
    setAppliedTags((tags) => tags.filter((t) => tagActive(t, next)));
    clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => runSearch(baseText, subcat, next), 220);
  }

  // Cambio manual del orden: re-busca la misma consulta con el nuevo criterio
  function onSortChange(v: SortKey) {
    setSort(v);
    clearTimeout(debounce.current);
    runSearch(baseText, subcat, sel, false, v);
  }

  async function openProduct(sku: string) {
    setError(null);
    try {
      const d = await getProduct(sku);
      setDetail(d);
      setLastProduct({ sku: d.sku, model: d.model, title: d.title });
      window.scrollTo({ top: 0 });
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    getHealth().then((h) => {
      setChatReady(!!h.chat_ready);
      setImageReady(!!h.image_ready);
      setDesignReady(!!h.design_ready);
    }).catch(() => {});
  }, []);

  // Carga inicial (escaparate): al abrir la app, todo el catálogo ordenado por
  // websort (el orden de escaparate de roca.es); el backend devuelve los primeros 30.
  useEffect(() => {
    setSubmitted("Catálogo");
    runSearch("", null, EMPTY_SELECTED, false, "websort");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Envía un turno al agente y consume el stream NDJSON, actualizando chat + parrilla.
  // imageCtx: contexto de imagen recién calculado (Búsqueda IA directa), aún no visible
  // en el estado por el re-render pendiente; si no llega, se usa el del estado.
  async function sendChat(text: string, imageCtx?: ImageSearchContext | null) {
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
        selected: lastProduct ?? undefined,
        image_search: (imageCtx !== undefined ? imageCtx : imageContext) ?? undefined,
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
          setChatStatus(ev.label ?? TOOL_LABEL[ev.name] ?? "");
        } else if (ev.type === "grid") {
          asstOpen = false;                       // tras la parrilla, el próximo texto abre burbuja nueva
          setImageGroups(null);   // el chat pinta la parrilla normal: salir del modo por-foto
          setImageContext(null);  // ya inyectado en la sesión; la parrilla nueva manda
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
          setSort(f.sort ?? "relevance");
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
        } else if (ev.type === "suppliers") {
          // compra offline: abre el buscador de distribuidores cercanos, como el
          // botón "Dónde comprar" del grid (geolocalización → /suppliers/nearby → lista + mapa).
          openLocalSuppliers({ sku: "", title: ev.product ?? null, image: null, price_rrp: null });
          setMessages((m) => [...m, { role: "note", text: "Buscador de distribuidores abierto ↗" }]);
        } else if (ev.type === "product") {
          // compra online: abre la ficha del producto (con su botón "Comprar online" → carrito).
          openProduct(ev.sku);
          setMessages((m) => [...m, { role: "note", text: "Ficha del producto abierta ↗" }]);
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
  // Con fotos subidas hace ÉL MISMO la búsqueda visual (si aún no se hizo) y arranca el
  // chat desde lo identificado; tras un Buscar previo reutiliza ese contexto sin repetirla.
  async function openAI() {
    setAiOpen(true);
    let seed = q.trim();
    if (!chatReady) {
      if (messages.length === 0) {
        setMessages([{ role: "error",
          text: "Chat en modo demo. Para activarlo, añade CLAUDE_API_KEY a backend/.env (o defínela como variable de entorno) y reinicia el servidor. La búsqueda ya funciona." }]);
      }
      return;
    }
    let ctx: ImageSearchContext | null | undefined;   // undefined = usar el del estado
    if (photos.length > 0 && !imageContext && !chatBusy) {
      setChatStatus("Buscando por imagen");
      ctx = await runImageSearch(q);
      setChatStatus(null);
    }
    const effective = ctx !== undefined ? ctx : imageContext;
    if (!seed && effective) {
      seed = "He buscado por foto. Preséntame brevemente lo que has identificado y ayúdame a elegir o afinar.";
    }
    if (seed) sendChat(seed, ctx);
    else if (messages.length === 0) setMessages([{ role: "assistant", text: CHAT_INTRO }]);
  }

  function newChat() {
    sessionId.current = null;
    setMessages(chatReady ? [{ role: "assistant", text: CHAT_INTRO }] : []);
    setChatStatus(null);
  }

  // nº de filtros activos, para el badge del botón "Filtros" en móvil
  const rangeOn = (r: { min: number | null; max: number | null }) =>
    r.min != null || r.max != null ? 1 : 0;
  const activeFilters =
    sel.categories.length + sel.collections.length + sel.finishes.length +
    rangeOn(sel.price) + rangeOn(sel.length) + rangeOn(sel.width) + rangeOn(sel.height);

  return (
    <>
      <div className={`rs-app${aiOpen ? " rs-app--chat" : ""}`}>
      <header className="rs-header">
        <a className="rs-logo" href="#" onClick={(e) => e.preventDefault()} aria-label="Roca">
          <img src="https://www.roca.es/documents/20126/346080475/roca-logo.svg/4dc29d13-1df3-b628-786b-7c63db57cdcd?t=1753429104544" alt="Roca" />
        </a>
        <form className="rs-searchform" onSubmit={doSearch}>
          <div
            className={`rs-searchbox${dragOver ? " is-dragover" : ""}`}
            ref={boxRef}
            onDragOver={(e) => { if (imageReady) { e.preventDefault(); setDragOver(true); } }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              if (!imageReady) return;
              e.preventDefault(); setDragOver(false);
              // el drop sobre la dropzone del panel ya lo procesa ImageDropPanel;
              // sin este guard el evento burbujea hasta aquí y la foto se añade dos veces
              if ((e.target as HTMLElement).closest(".rs-dropzone")) return;
              if (e.dataTransfer.files.length) addPhotos(e.dataTransfer.files);
            }}
          >
            <button type="submit" className="rs-search-ico" aria-label="Buscar">
              <SearchIcon />
            </button>
            {photos.map((p) => (
              <span key={p.id} className="rs-photo-chip">
                <img src={p.url} alt="" />
                <button type="button" aria-label="Quitar foto" onClick={() => removePhoto(p.id)}>×</button>
              </span>
            ))}
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              onFocus={() => setOpen(true)}
              placeholder={
                voice.listening ? "Escuchando…"
                : photos.length ? "Añade texto para refinar (opcional)"
                : "Introduce tu búsqueda"
              }
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

            {voice.supported && (
              <button
                type="button"
                className={`rs-mic-btn${voice.listening ? " is-rec" : ""}`}
                aria-label={voice.listening ? "Parar dictado" : "Buscar por voz"}
                title={voice.listening ? "Parar dictado" : "Buscar por voz"}
                onClick={toggleVoice}
              >
                <MicIcon />
              </button>
            )}

            {imageReady && (
              <button
                type="button"
                className="rs-cam-btn"
                aria-label="Buscar por imagen"
                title="Buscar por imagen"
                onClick={() => { setImgPanelOpen((v) => !v); setOpen(false); }}
              >
                <CameraIcon />
              </button>
            )}

            {!imgPanelOpen && open && q.trim() === "" && (
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

            {!imgPanelOpen && open && q.trim() !== "" && (suggestions.length > 0 || autoFilters.length > 0) && (
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

            {imgPanelOpen && (
              <ImageDropPanel
                photos={photos}
                sameProduct={sameProduct}
                busy={loading}
                onAdd={addPhotos}
                onRemove={removePhoto}
                onToggleSame={setSameProduct}
                onSearch={() => runImageSearch(q)}
              />
            )}
          </div>
          <button type="submit" className="rs-search-btn">Buscar</button>
          <button type="button" className="rs-ai-btn" onClick={openAI} title="Buscar y conversar con IA" aria-label="Búsqueda IA">
            <SparkIcon />
            <span className="rs-ai-label">Búsqueda IA</span>
          </button>
        </form>
        <button
          type="button"
          className="rs-design-btn"
          onClick={() => setDesignOpen(true)}
          title="Visualiza los productos de tu cesta en un baño generado por IA"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <path d="M21 15l-5-5L5 21" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>Diseña tu baño</span>
        </button>
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
            <button
              type="button"
              className="rs-filters-toggle"
              onClick={() => setFiltersOpen((o) => !o)}
              aria-expanded={filtersOpen}
            >
              <span>Filtros{activeFilters > 0 ? ` (${activeFilters})` : ""}</span>
              <span className="fac-chev">{filtersOpen ? "⌃" : "⌄"}</span>
            </button>
            <div className={`rs-sidebar-body${filtersOpen ? " is-open" : ""}`}>
              <Facets facets={facets} selected={sel} onChange={onFacetsChange} />
            </div>
          </aside>
        )}

        <main className="rs-main">
          {loading && <p className="rs-state">Buscando…</p>}
          {error && <p className="rs-state rs-error">{error}</p>}

          {!loading && appliedTags.length > 0 && (
            <div className="rs-filters-bar">
              <button
                type="button"
                className="rs-tagsbar-toggle"
                onClick={() => setTagsHidden((h) => !h)}
              >
                {tagsHidden ? "Mostrar filtros →" : "Ocultar filtros ←"}
              </button>
              {!tagsHidden && (
                <>
                  {appliedTags.map((t) => (
                    <span key={t.id} className={`rs-filter-tag rs-filter-tag--${t.type}`}>
                      {t.label}
                      <button
                        type="button"
                        className="rs-filter-tag-x"
                        aria-label={`Quitar ${t.label}`}
                        onClick={() => removeTag(t)}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <button type="button" className="rs-filters-clear" onClick={clearAllFilters}>
                    Limpiar todos los filtros
                  </button>
                </>
              )}
            </div>
          )}

          {!loading && correction && (
            <p className="rs-correction">
              Mostrando resultados para <b>{correction.to}</b>. Buscar en su lugar por{" "}
              <button
                type="button"
                className="rs-correction-link"
                onClick={() => launchSearch(correction.from, true)}
              >
                {correction.from}
              </button>
            </p>
          )}

          {!loading && total !== null && total > 0 && (
            <div className="rs-toolbar">
              <h1 className="rs-count"><span>{total} resultado{total === 1 ? "" : "s"}</span></h1>
              <label className="rs-sort">
                Ordenar por
                <select
                  value={sort}
                  onChange={(e) => onSortChange(e.target.value as SortKey)}
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {!loading && total === 0 && (
            <p className="rs-state">No se han encontrado productos para «{submitted}».</p>
          )}

          {imageGroups ? (
            imageGroups.map((g, i) => (
              <section key={g.photo} className="rs-imgsec">
                <h2 className="rs-imgsec-head">
                  {photos[i] && <img src={photos[i].url} alt={`Foto ${g.photo}`} />}
                  <span>Foto {g.photo} · {g.total} resultado{g.total === 1 ? "" : "s"}</span>
                </h2>
                {g.total === 0 && <p className="rs-state">Sin resultados para esta foto.</p>}
                <div className="rs-grid">
                  {g.results.map((c) => (
                    <ProductCard
                      key={`${g.photo}-${c.model}`}
                      card={c}
                      onOpen={openProduct}
                      onBuyOnline={addToCart}
                      onFindLocal={openLocalSuppliers}
                      selected={!!selected[c.model]}
                      onToggleSelect={toggleSelect}
                    />
                  ))}
                </div>
              </section>
            ))
          ) : (
            <div className="rs-grid">
              {results.map((c) => (
                <ProductCard
                  key={c.model}
                  card={c}
                  onOpen={openProduct}
                  onBuyOnline={addToCart}
                  onFindLocal={openLocalSuppliers}
                  selected={!!selected[c.model]}
                  onToggleSelect={toggleSelect}
                />
              ))}
            </div>
          )}
        </main>
      </div>
      </div>

      {/* Barra fija del comparador: aparece con 2+ productos marcados */}
      <div className={`rs-cmpbar${nSel >= 2 ? " is-show" : ""}`} aria-hidden={nSel < 2}>
        <span>{nSel} seleccionado{nSel !== 1 ? "s" : ""}</span>
        <button type="button" className="rs-cmp-do" onClick={() => setCompareSkus(Object.values(selected))}>
          Comparar
        </button>
        <button type="button" className="rs-cmp-clr" onClick={() => setSelected({})}>
          Limpiar
        </button>
      </div>

      {compareSkus && (
        <Compare
          skus={compareSkus}
          onClose={() => setCompareSkus(null)}
          onOpen={(sku) => { setCompareSkus(null); openProduct(sku); }}
        />
      )}

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

      {designOpen && (
        <Design ready={designReady} onClose={() => setDesignOpen(false)} />
      )}

      <CartDrawer onDesign={() => setDesignOpen(true)} />

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
