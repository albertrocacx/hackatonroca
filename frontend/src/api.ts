const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export interface ProductSummary {
  sku: string;
  title: string | null;
  category: string | null;
  collection: string | null;
  finish: string | null;
  price_rrp: number | null;
  is_spare_part: boolean;
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

export async function search(q: string): Promise<{ total: number; results: ProductSummary[] }> {
  const r = await fetch(`${API}/search?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error("Error en la busqueda");
  return r.json();
}

export async function getProduct(sku: string): Promise<ProductDetail> {
  const r = await fetch(`${API}/products/${encodeURIComponent(sku)}`);
  if (!r.ok) throw new Error("Producto no encontrado");
  return r.json();
}
