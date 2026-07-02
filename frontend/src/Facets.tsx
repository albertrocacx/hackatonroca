import { useState, type ReactNode } from "react";
import type { ColorGroup, Facets as FacetsData, FacetValue, Range, RangeSel, Selected } from "./api";

const N_SHOWN = 8;

function Section({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="fac-sec">
      <button type="button" className="fac-head" onClick={() => setOpen((o) => !o)}>
        <span>{title}</span>
        <span className="fac-chev">{open ? "⌃" : "⌄"}</span>
      </button>
      {open && children}
    </section>
  );
}

function CheckList({
  title, items, selectedVals, onToggle,
}: {
  title: string;
  items: FacetValue[];
  selectedVals: string[];
  onToggle: (v: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;
  const shown = expanded ? items : items.slice(0, N_SHOWN);
  const rest = items.length - N_SHOWN;
  return (
    <Section title={title}>
      <ul className="fac-list">
        {shown.map((it) => (
          <li key={it.value}>
            <label className="fac-check">
              <input
                type="checkbox"
                checked={selectedVals.includes(it.value)}
                onChange={() => onToggle(it.value)}
              />
              <span className="fac-val">{it.value}</span>
              <span className="fac-count">({it.count})</span>
            </label>
          </li>
        ))}
      </ul>
      {rest > 0 && (
        <button type="button" className="fac-more" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Mostrar menos" : `Mostrar más (${rest})`}
        </button>
      )}
    </Section>
  );
}

const fmt = (n: number) => Math.round(n).toLocaleString("es-ES");

/** Slider de rango con doble mango sobre una pista con relleno. */
function RangeRow({
  label, bounds, value, onChange, unit,
}: {
  label: string;
  bounds: Range | null;
  value: RangeSel;
  onChange: (r: RangeSel) => void;
  unit?: string;
}) {
  if (!bounds) return null;
  const single = bounds.min >= bounds.max;
  const lo = value.min ?? bounds.min;
  const hi = value.max ?? bounds.max;
  const span = bounds.max - bounds.min || 1;
  const loPct = ((lo - bounds.min) / span) * 100;
  const hiPct = ((hi - bounds.min) / span) * 100;
  const suffix = unit ? ` ${unit}` : "";

  return (
    <div className="fac-range">
      <div className="fac-range-label">
        <span className="fac-range-name">{label}</span>
        <span className="fac-range-cur">{fmt(lo)} – {fmt(hi)}{suffix}</span>
      </div>

      <div className={`fac-slider${single ? " is-single" : ""}`}>
        <div className="fac-slider-track">
          <div
            className="fac-slider-fill"
            style={{ left: `${loPct}%`, right: `${100 - hiPct}%` }}
          />
        </div>
        {!single && (
          <>
            <input
              type="range" className="fac-slider-input"
              min={bounds.min} max={bounds.max} value={lo}
              aria-label={`${label} mínimo`}
              onChange={(e) => onChange({ min: Math.min(+e.target.value, hi), max: value.max })}
            />
            <input
              type="range" className="fac-slider-input"
              min={bounds.min} max={bounds.max} value={hi}
              aria-label={`${label} máximo`}
              onChange={(e) => onChange({ min: value.min, max: Math.max(+e.target.value, lo) })}
            />
          </>
        )}
      </div>

      <div className="fac-slider-bounds">
        <span>{fmt(bounds.min)}{suffix}</span>
        <span>{fmt(bounds.max)}{suffix}</span>
      </div>
    </div>
  );
}

export default function Facets({
  facets, selected, onChange,
}: {
  facets: FacetsData;
  selected: Selected;
  onChange: (s: Selected) => void;
}) {
  const toggle = (key: "categories" | "collections" | "finishes") => (v: string) => {
    const cur = selected[key];
    const next = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v];
    onChange({ ...selected, [key]: next });
  };
  const setRange = (key: "price" | "length" | "width" | "height") => (r: RangeSel) =>
    onChange({ ...selected, [key]: r });

  // Colores principales: cada grupo agrega sus acabados compuestos (Negro -> Negro mate,
  // Porcelana negra...). Marcado SOLO si el grupo entero está seleccionado: un acabado
  // compartido ('Negro/Blanco' está en Negro Y en Blanco) no debe marcar el otro color.
  // Al desmarcar se conservan los acabados compartidos con otros colores aún marcados.
  const colorOn = (g: ColorGroup) =>
    g.finishes.length > 0 && g.finishes.every((f) => selected.finishes.includes(f));
  const toggleColor = (v: string) => {
    const g = (facets.color ?? []).find((x) => x.value === v);
    if (!g) return;
    let next: string[];
    if (colorOn(g)) {
      const keep = new Set(
        (facets.color ?? [])
          .filter((o) => o.value !== v && colorOn(o))
          .flatMap((o) => o.finishes)
      );
      next = selected.finishes.filter((f) => !g.finishes.includes(f) || keep.has(f));
    } else {
      next = [...selected.finishes, ...g.finishes.filter((f) => !selected.finishes.includes(f))];
    }
    onChange({ ...selected, finishes: next });
  };
  const colorItems = facets.color ?? [];

  const empty =
    facets.category.length === 0 &&
    facets.collection.length === 0 &&
    facets.finish.length === 0 &&
    !facets.price;
  if (empty) return <p className="fac-empty">Sin filtros disponibles.</p>;

  return (
    <div className="fac">
      <CheckList title="Categoría" items={facets.category}
        selectedVals={selected.categories} onToggle={toggle("categories")} />
      <CheckList title="Colecciones" items={facets.collection}
        selectedVals={selected.collections} onToggle={toggle("collections")} />
      {facets.price && (
        <Section title="Precio">
          <RangeRow label="Precio" bounds={facets.price}
            value={selected.price} onChange={setRange("price")} unit="€" />
        </Section>
      )}
      {(facets.dims.length || facets.dims.width || facets.dims.height) && (
        <Section title="Dimensiones">
          <RangeRow label="Largo (mm)" bounds={facets.dims.length}
            value={selected.length} onChange={setRange("length")} />
          <RangeRow label="Ancho (mm)" bounds={facets.dims.width}
            value={selected.width} onChange={setRange("width")} />
          <RangeRow label="Alto (mm)" bounds={facets.dims.height}
            value={selected.height} onChange={setRange("height")} />
        </Section>
      )}
      <CheckList title="Colores" items={colorItems}
        selectedVals={colorItems.filter(colorOn).map((g) => g.value)}
        onToggle={toggleColor} />
    </div>
  );
}
