import type { KeyboardEvent } from "react";

import type { Currency } from "../api/queries";

export function CurrencyTabs({
  currencies,
  selected,
  onSelect,
}: {
  currencies: Currency[];
  selected: Currency;
  onSelect: (currency: Currency) => void;
}) {
  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const target =
      event.key === "ArrowRight"
        ? (index + 1) % currencies.length
        : event.key === "ArrowLeft"
          ? (index - 1 + currencies.length) % currencies.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? currencies.length - 1
              : undefined;
    if (target === undefined) return;
    event.preventDefault();
    const currency = currencies[target];
    if (currency !== undefined) {
      onSelect(currency);
      const buttons =
        event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
          "[role=tab]",
        );
      buttons?.[target]?.focus();
    }
  }

  return (
    <div className="currency-tabs" role="tablist" aria-label="币种">
      {currencies.map((currency, index) => (
        <button
          type="button"
          role="tab"
          id={`currency-tab-${currency}`}
          aria-controls="currency-panel"
          aria-selected={currency === selected}
          tabIndex={currency === selected ? 0 : -1}
          key={currency}
          onClick={() => onSelect(currency)}
          onKeyDown={(event) => move(event, index)}
        >
          {currency}
        </button>
      ))}
    </div>
  );
}
