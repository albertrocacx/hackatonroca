import { useState } from "react";

export const PLACEHOLDER_IMG =
  "https://www.roca.es/o/roca-restyle-theme/images/product-thumbnail.jpg";

export default function Tile({ image, title }: { image: string | null; title: string | null }) {
  const [broken, setBroken] = useState(false);
  const src = image && !broken ? image : PLACEHOLDER_IMG;
  return (
    <div className="rs-tile">
      <img src={src} alt={title ?? ""} loading="lazy" onError={() => setBroken(true)} />
    </div>
  );
}
