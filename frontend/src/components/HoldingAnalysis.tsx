import type { PortfolioSummary } from "../api/queries";
import { ratioPercent } from "../lib/decimal";
import type { OverviewPosition } from "./PositionTable";

function compareNonNegativeDecimals(left: string, right: string): number {
  const [leftWhole = "0", leftFraction = ""] = left.split(".");
  const [rightWhole = "0", rightFraction = ""] = right.split(".");
  const leftScaled =
    BigInt(`${leftWhole}${leftFraction}`) *
    BigInt(10) ** BigInt(rightFraction.length);
  const rightScaled =
    BigInt(`${rightWhole}${rightFraction}`) *
    BigInt(10) ** BigInt(leftFraction.length);
  return leftScaled === rightScaled ? 0 : leftScaled > rightScaled ? 1 : -1;
}

function pnlState(value: string | null): "gain" | "loss" | "flat" | "unknown" {
  if (value === null) return "unknown";
  if (/^[+-]?0*(?:\.0*)?$/.test(value)) return "flat";
  return value.startsWith("-") ? "loss" : "gain";
}

export function HoldingAnalysis({
  records,
  summary,
}: {
  records: OverviewPosition[];
  summary: PortfolioSummary;
}) {
  const distribution = { gain: 0, loss: 0, flat: 0, unknown: 0 };
  let largest: OverviewPosition | undefined;
  let fresh = 0;
  for (const record of records) {
    distribution[pnlState(record.summary.unrealized_pnl)] += 1;
    if (record.summary.quote_state === "fresh") fresh += 1;
    const allocation = record.summary.allocation;
    const largestAllocation = largest?.summary.allocation;
    if (
      allocation !== null &&
      (largestAllocation === null ||
        largestAllocation === undefined ||
        compareNonNegativeDecimals(allocation, largestAllocation) > 0)
    ) {
      largest = record;
    }
  }

  return (
    <section aria-labelledby="holding-analysis-heading">
      <h2 id="holding-analysis-heading">持仓分析</h2>
      <p>基于当前成本与最近有效行情，仅分析当前币种，不构成投资建议。</p>
      <div className="analysis-grid">
        <article className="analysis-card">
          <h3>估值覆盖</h3>
          <p>
            <strong>{summary.valued_count}</strong> / {summary.position_count}
          </p>
        </article>
        <article className="analysis-card">
          <h3>盈亏分布</h3>
          <p>
            盈利 {distribution.gain} · 亏损 {distribution.loss} · 持平{" "}
            {distribution.flat} · 未估值 {distribution.unknown}
          </p>
        </article>
        <article className="analysis-card">
          <h3>最大持仓</h3>
          <p>
            {largest === undefined
              ? "暂无可估值持仓"
              : `${largest.position.instrument.canonical_symbol} · ${ratioPercent(
                  largest.summary.allocation ?? "0",
                )}`}
          </p>
        </article>
        <article className="analysis-card">
          <h3>行情质量</h3>
          <p>
            新鲜 {fresh} · 陈旧 {summary.stale_count} · 不可用{" "}
            {summary.unvalued_count}
          </p>
        </article>
      </div>
    </section>
  );
}
