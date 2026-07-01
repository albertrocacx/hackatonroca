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

const CONCEPT_PARAM: Record<ConceptType, string> = {
  category: "category",
  subcategory: "subcategory",
  collection: "collection",
};

export interface SearchOpts {
  concept?: Suggestion;   // intención elegida (categoría/subcat/colección)
  filters?: Filter[];     // filtros auto-detectados
}

export async function search(
  q: string,
  opts: SearchOpts = {}
): Promise<{ total: number; results: ProductSummary[] }> {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  if (opts.concept) p.set(CONCEPT_PARAM[opts.concept.type], opts.concept.term);
  for (const f of opts.filters ?? []) {
    if (f.type === "finish") (f.values ?? []).forEach((v) => p.append("finish", v));
    if (f.type === "price") {
      if (f.min_price != null) p.set("min_price", String(f.min_price));
      if (f.max_price != null) p.set("max_price", String(f.max_price));
    }
  }
  const r = await fetch(`${API}/search?${p.toString()}`);
  if (!r.ok) throw new Error("Error en la busqueda");
  return r.json();
}

export async function getProduct(sku: string): Promise<ProductDetail> {
  const r = await fetch(`${API}/products/${encodeURIComponent(sku)}`);
  if (!r.ok) throw new Error("Producto no encontrado");
  return r.json();
}
