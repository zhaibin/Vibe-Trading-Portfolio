import type { PortfolioSummary } from "../api/queries";
import { formatMoney, ratioPercent } from "../lib/decimal";

export function SummaryCards({ summary }: { summary: PortfolioSummary }) {
  const currency = summary.currency;
  return (
    <section aria-labelledby="summary-heading">
      <h2 id="summary-heading">
        {summary.estimated ? "估算总资产" : "总资产"}
      </h2>
      <p className="headline-value">
        {formatMoney(summary.total_value, currency)}
      </p>
      {summary.estimated ? (
        <p>包含陈旧行情或未知项目，未估值持仓不计入总资产。</p>
      ) : null}
      <div className="summary-grid">
        <article className="summary-card">
          <h3>持仓市值</h3>
          <p>{formatMoney(summary.market_value, currency)}</p>
        </article>
        <article className="summary-card">
          <h3>持仓成本</h3>
          <p>{formatMoney(summary.position_cost, currency)}</p>
        </article>
        <article className="summary-card">
          <h3>已知现金</h3>
          <p>{formatMoney(summary.known_cash, currency)}</p>
        </article>
        <article className="summary-card">
          <h3>未实现盈亏</h3>
          <p>{formatMoney(summary.unrealized_pnl, currency)}</p>
          {summary.unrealized_pnl_pct === null ? null : (
            <p>{ratioPercent(summary.unrealized_pnl_pct)}</p>
          )}
        </article>
      </div>
      {summary.unknown_cash_account_count > 0 ? (
        <p>{summary.unknown_cash_account_count} 个账户现金未知</p>
      ) : null}
      {summary.unvalued_count > 0 ? (
        <div className="unavailable-summary">
          <strong>{summary.unvalued_count} 项未估值</strong>
          <span>未估值成本 {formatMoney(summary.unvalued_cost, currency)}</span>
        </div>
      ) : null}
    </section>
  );
}
