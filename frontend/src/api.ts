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

export interface ProductDetail extends ProductSummary {
  subcategory: string | null;
  desc: { marketing: string | null; extended: string | null };
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
  results: ProductSummary[];
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
