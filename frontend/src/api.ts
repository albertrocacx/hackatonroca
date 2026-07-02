const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type PriceType = "OnlineFrom" | "PVPR";

export interface ProductSummary {
  sku: string;
  title: string | null;
  category: string | null;
  collection: string | null;
  finish: string | null;
  price_rrp: number | null;
  price_type: PriceType;
  is_spare_part: boolean;
  image: string | null;
  dims: string | null;
}

// Variante (acabado) de un modelo, y tarjeta-modelo del grid
export interface Variant {
  sku: string;
  finish: string | null;
  image: string | null;
  price_rrp: number | null;
  price_type: PriceType;
  dims: string | null;
}
export interface ModelCard {
  model: string;
  title: string | null;
  collection: string | null;
  category: string | null;
  default: number;
  variants: Variant[];
}

export interface ProductDetail extends ProductSummary {
  model: string | null;
  subcategory: string | null;
  desc: { marketing: string | null; extended: string | null };
  variants: Variant[];
  relations: {
    compatible: ProductSummary[];
    optional: ProductSummary[];
    included: ProductSummary[];
    sparepart: ProductSummary[];
  };
}

export type ConceptType = "category" | "subcategory" | "collection";

export interface Suggestion {
  term: string;
  type: ConceptType;
  count: number;
  source: "prefix" | "semantic";
  score?: number;
}

export interface Filter {
  type: "finish" | "price";
  label: string;
  values?: string[];        // finish
  band?: "high" | "low";    // price
  min_price?: number;
  max_price?: number;
}

export interface SuggestResponse {
  query: string;
  intent_phrase: string;
  suggestions: Suggestion[];
  filters: Filter[];
}

export async function suggest(q: string): Promise<SuggestResponse> {
  const r = await fetch(`${API}/suggest?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error("Error en autocompletado");
  return r.json();
}

// ---- Facetas ----
export interface FacetValue { value: string; count: number; }
// Color principal (Negro, Blanco...) con los acabados del catálogo que agrupa
// (Negro -> Negro, Negro mate, Porcelana negra...). Marcar el color = seleccionarlos todos.
export interface ColorGroup extends FacetValue { finishes: string[]; }
export interface Range { min: number; max: number; }
export interface Facets {
  category: FacetValue[];
  collection: FacetValue[];
  finish: FacetValue[];
  color: ColorGroup[];
  price: Range | null;
  dims: { length: Range | null; width: Range | null; height: Range | null };
}
// Orden de la parrilla (lo aplica el backend sobre el total, antes del limit)
export type SortKey = "relevance" | "price_asc" | "price_desc" | "alpha_asc" | "alpha_desc";

// Tag visual de un filtro aplicado por el intérprete (chips sobre la parrilla).
export interface AppliedTag {
  id: "category" | "collection" | "finish" | "price" | "size";
  type: string;
  label: string;
  dimensions?: ("length" | "width" | "height")[];
}

// Filtros que el intérprete LLM del backend aplicó a una búsqueda auto=true
// (solo los que el usuario no fijó; valores exactos del catálogo, listos para el sidebar).
export interface AutoApplied {
  finish?: string[];
  category?: string[];
  collection?: string[];
  min_price?: number;
  max_price?: number;
  price_band?: "cheap" | "mid" | "expensive";
  size?: { band: string; dims: Partial<Record<"length" | "width" | "height", RangeSel>> };
  sort?: SortKey;
}

export interface SearchResponse {
  query: string;
  sort?: SortKey;
  total: number;
  results: ModelCard[];
  facets: Facets;
  auto?: {
    search_text: string;
    applied: AutoApplied;
    tags?: AppliedTag[];
    corrected_query?: string;
    corrected?: boolean;
  };
}

// ---- Selección del sidebar ----
export interface RangeSel { min: number | null; max: number | null; }
export interface Selected {
  categories: string[];
  collections: string[];
  finishes: string[];
  price: RangeSel;
  length: RangeSel;
  width: RangeSel;
  height: RangeSel;
}
const EMPTY_RANGE: RangeSel = { min: null, max: null };
export const EMPTY_SELECTED: Selected = {
  categories: [], collections: [], finishes: [],
  price: { ...EMPTY_RANGE }, length: { ...EMPTY_RANGE },
  width: { ...EMPTY_RANGE }, height: { ...EMPTY_RANGE },
};

const RANGE_PARAMS: [keyof Selected, string, string][] = [
  ["price", "min_price", "max_price"],
  ["length", "min_length", "max_length"],
  ["width", "min_width", "max_width"],
  ["height", "min_height", "max_height"],
];

export async function search(
  q: string,
  selected: Selected,
  subcategory?: string | null,
  auto = false,
  sort: SortKey = "relevance"
): Promise<SearchResponse> {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  if (auto) p.set("auto", "true");
  if (sort !== "relevance") p.set("sort", sort);
  if (subcategory) p.set("subcategory", subcategory);
  selected.categories.forEach((c) => p.append("category", c));
  selected.collections.forEach((c) => p.append("collection", c));
  selected.finishes.forEach((f) => p.append("finish", f));
  for (const [key, minP, maxP] of RANGE_PARAMS) {
    const r = selected[key] as RangeSel;
    if (r.min != null) p.set(minP, String(r.min));
    if (r.max != null) p.set(maxP, String(r.max));
  }
  const res = await fetch(`${API}/search?${p.toString()}`);
  if (!res.ok) throw new Error("Error en la busqueda");
  return res.json();
}

export async function getProduct(sku: string): Promise<ProductDetail> {
  const r = await fetch(`${API}/products/${encodeURIComponent(sku)}`);
  if (!r.ok) throw new Error("Producto no encontrado");
  return r.json();
}

// ---- Compra: online (carrito) y offline (distribuidores) --------------------
// Forma mínima que necesitan carrito y buscador de tiendas. La produce cualquier
// resultado del grid (card + variante). `online` = disponible para compra online
// (el buscador lo expone a partir del flag `ecommerce` del catálogo).
export interface ShopItem {
  sku: string;
  title: string | null;
  image: string | null;
  price_rrp: number | null;
  finish?: string | null;
  collection?: string | null;
  online?: boolean;
}

export interface Supplier {
  id: string;
  name: string;
  address: string | null;
  city: string | null;
  province: string | null;
  postal_code: string;
  phone: string | null;
  web: string | null;
  lat: number;
  lon: number;
  exposition: boolean;        // punto de venta con exposición Roca
  category: string | null;    // "Con exposición" | "Sin exposición"
  distance_km?: number;       // presente en /suppliers/nearby
}

export interface NearbyResponse {
  origin: { lat: number; lon: number };
  count: number;
  suppliers: Supplier[];
}

export async function nearbySuppliers(
  lat: number, lon: number, limit = 8
): Promise<NearbyResponse> {
  const p = new URLSearchParams({ lat: String(lat), lon: String(lon), limit: String(limit) });
  const r = await fetch(`${API}/suppliers/nearby?${p.toString()}`);
  if (!r.ok) throw new Error("No se han podido cargar los distribuidores");
  return r.json();
}

// ---- Chat IA (opcional) ----
export async function getHealth(): Promise<{
  chat_ready: boolean; image_ready?: boolean; design_ready?: boolean;
}> {
  const r = await fetch(`${API}/health`);
  if (!r.ok) throw new Error("health");
  return r.json();
}

// ---- Diseña tu baño (render IA) ----
export interface DesignRequest {
  skus: string[];
  room_image?: string | null;   // dataURL/base64 de la foto del espacio (opcional)
  style?: string | null;
  instruction?: string | null;  // con session_id y sin skus: itera el render anterior
  session_id?: string | null;
}

export interface DesignResponse {
  session_id: string;
  image_b64: string;
  products: ProductSummary[];
  skipped: string[];            // SKUs sin foto de catálogo (no se pudieron usar)
}

// Renueva tu baño: foto del baño actual -> elementos detectados + candidatos Roca.
export interface RenewalProduct {
  sku: string;
  title: string | null;
  image: string | null;
  price_rrp: number | null;
  finish: string | null;
  collection: string | null;
}
export interface RenewalItem {
  label: string;                // lo detectado: "lavabo de pedestal blanco"
  query: string;                // búsqueda usada contra el catálogo
  products: RenewalProduct[];
}

export async function analyzeBathroom(room_image: string): Promise<{ items: RenewalItem[] }> {
  const r = await fetch(`${API}/api/design/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room_image }),
  });
  if (!r.ok) {
    let msg = "Error analizando el baño";
    try { msg = (await r.json()).detail ?? msg; } catch { /* cuerpo no JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

export async function designBathroom(req: DesignRequest): Promise<DesignResponse> {
  const r = await fetch(`${API}/api/design`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    let msg = "Error generando el diseño";
    try { msg = (await r.json()).detail ?? msg; } catch { /* cuerpo no JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

// Producto seleccionado (último abierto): el agente lo usa para "el manual de este producto"
export interface SelectedProduct { sku: string; model?: string | null; title?: string | null; }
export interface ChatView { query?: string; visible?: string[]; selected?: SelectedProduct; }
export interface ChatRequest { text: string; session_id?: string | null; view?: ChatView; }

export interface ChatFilters {
  category?: string;
  collection?: string;
  subcategory?: string;
  finish?: string[];
  sort?: SortKey;
  min_price?: number;
  max_price?: number;
  min_length?: number;
  max_length?: number;
  min_width?: number;
  max_width?: number;
  min_height?: number;
  max_height?: number;
}

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string; label?: string }
  | { type: "tool_error"; name: string; error: string }
  | { type: "grid"; query: string | null; filters?: ChatFilters; data: SearchResponse }
  | { type: "done"; session_id?: string }
  | { type: "error"; message: string };

// POST en streaming: el backend responde NDJSON (un evento JSON por línea).
export async function* streamChat(req: ChatRequest): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok || !res.body) throw new Error("Error en el chat");
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });    // un chunk puede cortar una línea a la mitad
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";                        // guarda el fragmento final hasta el próximo read
    for (const line of lines) {
      const s = line.trim();
      if (s) yield JSON.parse(s) as ChatEvent;
    }
  }
  const tail = buf.trim();
  if (tail) yield JSON.parse(tail) as ChatEvent;
}

// ---- Búsqueda por imagen (endpoint DINOv2 vía backend) ----
export type ImageMode = "same" | "distinct";

export interface ImageSearchGroup { photo: number; total: number; results: ModelCard[]; }
export interface ImageSearchSameResponse {
  query: string; total: number; results: ModelCard[]; facets: null;
}
export interface ImageSearchDistinctResponse {
  query: string; mode: "distinct"; groups: ImageSearchGroup[];
}

export async function searchByImage(
  photos: Blob[], q: string, mode: ImageMode
): Promise<ImageSearchSameResponse | ImageSearchDistinctResponse> {
  const fd = new FormData();
  photos.forEach((b, i) => fd.append("images", b, `foto-${i + 1}.jpg`));
  if (q) fd.set("q", q);
  fd.set("mode", mode);
  const r = await fetch(`${API}/search/image`, { method: "POST", body: fd });
  if (!r.ok) {
    const detail = await r.json().then((j) => j.detail).catch(() => null);
    throw new Error(detail || "Error en la búsqueda por imagen");
  }
  return r.json();
}
