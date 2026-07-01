import { useState, type ReactNode } from "react";
import type { Facets as FacetsData, FacetValue, Range, RangeSel, Selected } from "./api";

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

function RangeFilter({
  title, bounds, value, onChange, unit,
}: {
  title: string;
  bounds: Range | null;
  value: RangeSel;
  onChange: (r: RangeSel) => void;
  unit?: string;
}) {
  if (!bounds) return null;
  const single = bounds.min >= bounds.max;
  const lo = value.min ?? bounds.min;
  const hi = value.max ?? bounds.max;
  return (
    <Section title={title}>
      <div className="fac-range">
        <div className="fac-range-vals">
          {fmt(lo)} – {fmt(hi)}{unit ? ` ${unit}` : ""}
        </div>
        {!single && (
          <div className="fac-sliders">
            <input
              type="range" min={bounds.min} max={bounds.max} value={lo}
              onChange={(e) => onChange({ min: Math.min(+e.target.value, hi), max: value.max })}
            />
            <input
              type="range" min={bounds.min} max={bounds.max} value={hi}
              onChange={(e) => onChange({ min: value.min, max: Math.max(+e.target.value, lo) })}
            />
          </div>
        )}
      </div>
    </Section>
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
      <RangeFilter title="Precio" bounds={facets.price}
        value={selected.price} onChange={setRange("price")} unit="€" />
      <RangeFilter title="Largo (mm)" bounds={facets.dims.length}
        value={selected.length} onChange={setRange("length")} />
      <RangeFilter title="Ancho (mm)" bounds={facets.dims.width}
        value={selected.width} onChange={setRange("width")} />
      <RangeFilter title="Alto (mm)" bounds={facets.dims.height}
        value={selected.height} onChange={setRange("height")} />
      <CheckList title="Colores" items={facets.finish}
        selectedVals={selected.finishes} onToggle={toggle("finishes")} />
    </div>
  );
}
