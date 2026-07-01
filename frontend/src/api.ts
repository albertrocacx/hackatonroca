const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export interface ProductSummary {
  sku: string;
  title: string | null;
  category: string | null;
  collection: string | null;
  finish: string | null;
  price_rrp: number | null;
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
export interface Range { min: number; max: number; }
export interface Facets {
  category: FacetValue[];
  collection: FacetValue[];
  finish: FacetValue[];
  price: Range | null;
  dims: { length: Range | null; width: Range | null; height: Range | null };
}
export interface SearchResponse {
  query: string;
  total: number;
  results: ModelCard[];
  facets: Facets;
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
  subcategory?: string | null
): Promise<SearchResponse> {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
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

// ---- Chat IA (opcional) ----
export async function getHealth(): Promise<{ chat_ready: boolean }> {
  const r = await fetch(`${API}/health`);
  if (!r.ok) throw new Error("health");
  return r.json();
}

export interface ChatView { query?: string; visible?: string[]; }
export interface ChatRequest { text: string; session_id?: string | null; view?: ChatView; }

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string }
  | { type: "tool_error"; name: string; error: string }
  | { type: "grid"; query: string | null; data: SearchResponse }
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
