import type { PriceType } from "./api";

export default function ProductCta({ priceType }: { priceType: PriceType }) {
  if (priceType === "OnlineFrom") {
    return (
      <div className="rs-cta-row">
        <button
          type="button"
          className="rs-cta rs-cta--buy"
          onClick={(e) => e.stopPropagation()}
        >
          <svg className="rs-cta-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-1.99.9-1.99 2S15.9 22 17 22s2-.9 2-2-.9-2-2-2zM7.16 14h9.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49A1 1 0 0 0 21.05 5H5.21L4.27 2H1v2h2l3.6 7.59-1.35 2.44C4.52 15.37 5.48 17 7 17h12v-2H7.42l.74-1z"
            />
          </svg>
          Comprar online
        </button>
      </div>
    );
  }

  return (
    <div className="rs-cta-row">
      <button
        type="button"
        className="rs-cta rs-cta--where"
        onClick={(e) => e.stopPropagation()}
      >
        Donde comprar
        <span className="rs-cta-arrow" aria-hidden="true">→</span>
      </button>
    </div>
  );
}
