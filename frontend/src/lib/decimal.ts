function formatDecimal(
  value: string,
  places: number,
  trimTrailingZeroes: boolean,
): string {
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (match === null) return value;

  const [, sign = "", whole = "0", fraction = ""] = match;
  const scale = BigInt(10) ** BigInt(places);
  const kept = fraction.slice(0, places).padEnd(places, "0");
  let scaled = BigInt(whole) * scale + BigInt(kept || "0");
  if ((fraction[places] ?? "0") >= "5") scaled += BigInt(1);

  const normalizedSign = sign === "-" && scaled !== BigInt(0) ? "-" : "";
  const integer = scaled / scale;
  let decimals = (scaled % scale).toString().padStart(places, "0");
  if (trimTrailingZeroes) decimals = decimals.replace(/0+$/, "");
  return `${normalizedSign}${integer.toString()}${decimals === "" ? "" : `.${decimals}`}`;
}

export function formatMoney(value: string, currency: string): string {
  return `${formatDecimal(value, 2, false)} ${currency}`;
}

export function formatQuantity(value: string): string {
  const [whole = "", fraction = ""] = value.split(".");
  const decimals = fraction.replace(/0+$/, "");
  return decimals === "" ? whole : `${whole}.${decimals}`;
}

export function ratioPercent(value: string): string {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole = "0", fraction = ""] = unsigned.split(".");
  const scale = BigInt(10) ** BigInt(fraction.length);
  const numerator = BigInt(`${whole}${fraction}` || "0") * BigInt(100);
  const integer = numerator / scale;
  const remainder = numerator % scale;
  const decimals = remainder
    .toString()
    .padStart(fraction.length, "0")
    .replace(/0+$/, "");
  const exact = `${negative ? "-" : ""}${integer.toString()}${decimals === "" ? "" : `.${decimals}`}`;
  return `${formatDecimal(exact, 2, true)}%`;
}
