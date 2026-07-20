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
  return `${negative ? "-" : ""}${integer.toString()}${decimals === "" ? "" : `.${decimals}`}%`;
}
