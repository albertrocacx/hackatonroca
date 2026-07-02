import { useRef, type DragEvent } from "react";

// Una foto añadida al buscador: blob reescalado + objectURL para la preview.
// El dueño del estado (App) debe revocar `url` al quitar la foto.
export interface Photo { id: string; blob: Blob; url: string; }

export function CameraIcon({ small = false }: { small?: boolean }) {
  const s = small ? 16 : 20;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"
            strokeLinejoin="round" />
      <circle cx="12" cy="14" r="3.4" />
    </svg>
  );
}

// Panel dropzone bajo la barra (mismo patrón visual que el panel de sugerencias):
// zona de drop + selector de ficheros, miniaturas con borrado, toggle de modo y CTA.
export function ImageDropPanel({
  photos, sameProduct, busy, onAdd, onRemove, onToggleSame, onSearch,
}: {
  photos: Photo[];
  sameProduct: boolean;
  busy: boolean;
  onAdd: (files: FileList | File[]) => void;
  onRemove: (id: string) => void;
  onToggleSame: (v: boolean) => void;
  onSearch: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.files.length) onAdd(e.dataTransfer.files);
  }

  return (
    <div className="rs-suggest rs-imgpanel">
      <div
        className="rs-dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <CameraIcon />
        <p>Arrastra tus fotos aquí o <u>haz click para elegir</u></p>
        <p className="rs-dropzone-hint">Hasta 6 fotos · varios ángulos mejoran el resultado</p>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => { if (e.target.files) onAdd(e.target.files); e.target.value = ""; }}
        />
      </div>

      {photos.length > 0 && (
        <div className="rs-drop-thumbs">
          {photos.map((p) => (
            <span key={p.id} className="rs-drop-thumb">
              <img src={p.url} alt="" />
              <button type="button" aria-label="Quitar foto" onClick={() => onRemove(p.id)}>×</button>
            </span>
          ))}
        </div>
      )}

      {photos.length >= 2 && (
        <label className="rs-imgpanel-toggle">
          <input
            type="checkbox"
            checked={sameProduct}
            onChange={(e) => onToggleSame(e.target.checked)}
          />
          Las fotos son del mismo producto
        </label>
      )}

      <button
        type="button"
        className="rs-imgpanel-cta"
        disabled={photos.length === 0 || busy}
        onClick={onSearch}
      >
        {busy ? "Buscando…" : "Buscar por imagen"}
      </button>
    </div>
  );
}
