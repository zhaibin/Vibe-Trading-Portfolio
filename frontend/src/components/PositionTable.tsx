import type { PortfolioSummary, Position } from "../api/queries";
import { formatLocalDateTime } from "../lib/dateTime";
import { formatMoney, formatQuantity, ratioPercent } from "../lib/decimal";

export interface OverviewPosition {
  position: Position;
  summary: PortfolioSummary["positions"][number];
}

const stateLabel = {
  fresh: "新鲜",
  stale: "陈旧",
  unavailable: "不可用",
} as const;

export function PositionTable({
  currency,
  records,
}: {
  currency: string;
  records: OverviewPosition[];
}) {
  return (
    <section aria-labelledby="position-detail-heading">
      <h2 id="position-detail-heading">持仓估值明细</h2>
      <div className="responsive-table">
        <table aria-label="持仓估值明细">
          <thead>
            <tr>
              <th>证券</th>
              <th>数量与成本</th>
              <th>报价与市值</th>
              <th>盈亏</th>
              <th>行情状态</th>
            </tr>
          </thead>
          <tbody>
            {records.map(({ position, summary }) => (
              <tr key={summary.position_id}>
                <th scope="row">
                  <strong>{position.instrument.canonical_symbol}</strong>
                  <span>{position.instrument.name}</span>
                </th>
                <td>
                  {formatQuantity(summary.quantity)} ·{" "}
                  {formatMoney(summary.average_cost, currency)}
                </td>
                <td>
                  {summary.quote_price === null
                    ? "暂无报价"
                    : `${formatMoney(summary.quote_price, currency)} / ${
                        summary.market_value === null
                          ? "暂无市值"
                          : formatMoney(summary.market_value, currency)
                      }`}
                </td>
                <td>
                  {summary.unrealized_pnl === null
                    ? "未估值"
                    : formatMoney(summary.unrealized_pnl, currency)}
                  {summary.unrealized_pnl_pct === null ? null : (
                    <span> {ratioPercent(summary.unrealized_pnl_pct)}</span>
                  )}
                </td>
                <td>
                  <span className={`quote-badge quote-${summary.quote_state}`}>
                    {stateLabel[summary.quote_state]}
                  </span>
                  {summary.quote_provider === null ? null : (
                    <span>来源 {summary.quote_provider}</span>
                  )}
                  {summary.quote_as_of === null ? null : (
                    <span>
                      报价{" "}
                      <time dateTime={summary.quote_as_of}>
                        {formatLocalDateTime(summary.quote_as_of)}
                      </time>
                    </span>
                  )}
                  {summary.quote_fetched_at === null ? null : (
                    <span>
                      获取{" "}
                      <time dateTime={summary.quote_fetched_at}>
                        {formatLocalDateTime(summary.quote_fetched_at)}
                      </time>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
