import type { OverviewPosition } from "./PositionTable";
import { ratioPercent } from "../lib/decimal";

export function AllocationBars({
  currency,
  records,
}: {
  currency: string;
  records: OverviewPosition[];
}) {
  const valued = records.filter(
    (record) =>
      record.summary.allocation !== null &&
      record.summary.market_value !== null,
  );
  if (valued.length === 0) return null;
  return (
    <section aria-labelledby="allocation-heading">
      <h2 id="allocation-heading">持仓配置</h2>
      <div className="allocation-bars" aria-hidden="true">
        {valued.map(({ position, summary }) => (
          <div className="allocation-bar-row" key={summary.position_id}>
            <span>{position.instrument.canonical_symbol}</span>
            <span className="allocation-track">
              <span
                style={{ width: ratioPercent(summary.allocation ?? "0") }}
              />
            </span>
          </div>
        ))}
      </div>
      <table className="allocation-table" aria-label="持仓配置">
        <thead>
          <tr>
            <th>证券</th>
            <th>占比</th>
            <th>市值</th>
          </tr>
        </thead>
        <tbody>
          {valued.map(({ position, summary }) => (
            <tr key={summary.position_id}>
              <th scope="row">{position.instrument.canonical_symbol}</th>
              <td>{ratioPercent(summary.allocation ?? "0")}</td>
              <td>
                {summary.market_value} {currency}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
