import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OverviewPage } from "./OverviewPage";

const account = {
  id: "10000000-0000-4000-8000-000000000001",
  name: "人民币账户",
  currency: "CNY",
  cash_balance: null,
  version: 1,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  archived_at: null,
};

const positions = [
  {
    id: "30000000-0000-4000-8000-000000000001",
    account_id: account.id,
    instrument_id: "20000000-0000-4000-8000-000000000001",
    instrument: {
      canonical_symbol: "600519.SH",
      name: "示例白酒",
      market: "CN_SH",
      currency: "CNY",
      asset_type: "equity",
    },
    quantity: "10",
    average_cost: "8",
    note: null,
    version: 1,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    archived_at: null,
  },
  {
    id: "30000000-0000-4000-8000-000000000002",
    account_id: account.id,
    instrument_id: "20000000-0000-4000-8000-000000000002",
    instrument: {
      canonical_symbol: "000001.SZ",
      name: "示例银行",
      market: "CN_SZ",
      currency: "CNY",
      asset_type: "equity",
    },
    quantity: "5",
    average_cost: "0",
    note: null,
    version: 1,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    archived_at: null,
  },
  {
    id: "30000000-0000-4000-8000-000000000003",
    account_id: account.id,
    instrument_id: "20000000-0000-4000-8000-000000000003",
    instrument: {
      canonical_symbol: "510300.SH",
      name: "示例指数",
      market: "CN_SH",
      currency: "CNY",
      asset_type: "etf",
    },
    quantity: "2",
    average_cost: "20",
    note: null,
    version: 1,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    archived_at: null,
  },
];

const cnySummary = {
  currency: "CNY",
  account_count: 1,
  position_count: 3,
  valued_count: 2,
  stale_count: 1,
  unvalued_count: 1,
  market_value: "150",
  position_cost: "120",
  valued_position_cost: "80",
  unvalued_cost: "40",
  unrealized_pnl: "70",
  unrealized_pnl_pct: "0.875",
  known_cash: "0",
  unknown_cash_account_count: 1,
  total_value: "150",
  estimated: true,
  positions: [
    {
      position_id: positions[0]!.id,
      account_id: account.id,
      instrument_id: positions[0]!.instrument_id,
      quantity: "10",
      average_cost: "8",
      position_cost: "80",
      quote_price: "10",
      market_value: "100",
      unrealized_pnl: "20",
      unrealized_pnl_pct: "0.25",
      allocation: "0.666666",
      quote_state: "stale",
      quote_provider: "eastmoney",
      quote_as_of: "2026-07-19T01:00:00Z",
      quote_fetched_at: "2026-07-20T01:00:00Z",
    },
    {
      position_id: positions[1]!.id,
      account_id: account.id,
      instrument_id: positions[1]!.instrument_id,
      quantity: "5",
      average_cost: "0",
      position_cost: "0",
      quote_price: "10",
      market_value: "50",
      unrealized_pnl: "50",
      unrealized_pnl_pct: null,
      allocation: "0.333334",
      quote_state: "fresh",
      quote_provider: "tencent",
      quote_as_of: "2026-07-20T00:30:00Z",
      quote_fetched_at: "2026-07-20T01:00:00Z",
    },
    {
      position_id: positions[2]!.id,
      account_id: account.id,
      instrument_id: positions[2]!.instrument_id,
      quantity: "2",
      average_cost: "20",
      position_cost: "40",
      quote_price: null,
      market_value: null,
      unrealized_pnl: null,
      unrealized_pnl_pct: null,
      allocation: null,
      quote_state: "unavailable",
      quote_provider: null,
      quote_as_of: null,
      quote_fetched_at: null,
    },
  ],
};

function settingsStatus(
  lastRefresh: {
    status: "succeeded" | "partial" | "failed";
    updated: number;
    stale: number;
    unavailable: number;
    finished_at: string;
  } | null = null,
) {
  return {
    schema_revision: "20260719_0006",
    migration_healthy: true,
    database_path: "var/data/portfolio.db",
    backup_directory: "var/data",
    latest_backup_at: null,
    adapters: [],
    last_successful_refresh_at: lastRefresh?.finished_at ?? null,
    last_refresh: lastRefresh,
    latest_quote_count: 0,
    candidate_cache_count: 0,
  };
}

function json(body: unknown, status = 200) {
  return Response.json(body, { status });
}

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </MemoryRouter>
    );
  }
  return render(<OverviewPage />, { wrapper: Wrapper });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("OverviewPage", () => {
  it("shows a first-run holdings CTA and never refreshes on load", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/v1/accounts?archived=false")
        return json({ items: [], next_cursor: null });
      if (url === "/api/v1/positions?archived=false")
        return json({ items: [], next_cursor: null });
      if (url === "/api/v1/accounts?archived=true")
        return json({ items: [], next_cursor: null });
      if (url === "/api/v1/positions?archived=true")
        return json({ items: [], next_cursor: null });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderOverview();

    expect(
      await screen.findByRole("link", { name: "创建账户" }),
    ).toHaveAttribute("href", "/holdings");
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("market-data/refresh"),
      ),
    ).toHaveLength(0);
  });

  it("shows an explicit archived state instead of first-run guidance", async () => {
    const archivedAccount = {
      ...account,
      version: 2,
      archived_at: "2026-07-20T01:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input);
        if (url === "/api/v1/accounts?archived=false")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/positions?archived=false")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/accounts?archived=true")
          return json({ items: [archivedAccount], next_cursor: null });
        if (url === "/api/v1/positions?archived=true")
          return json({ items: [], next_cursor: null });
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    renderOverview();

    expect(await screen.findByText("没有当前账户或持仓")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "查看已归档项目" }),
    ).toHaveAttribute("href", "/holdings");
    expect(
      screen.queryByRole("link", { name: "创建账户" }),
    ).not.toBeInTheDocument();
  });

  it("renders the persisted last refresh summary after page load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input);
        if (url === "/api/v1/accounts?archived=false")
          return json({ items: [account], next_cursor: null });
        if (url === "/api/v1/positions?archived=false")
          return json({ items: positions, next_cursor: null });
        if (url === "/api/v1/accounts?archived=true")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/positions?archived=true")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/portfolio/summary?currency=CNY")
          return json(cnySummary);
        if (url === "/api/v1/settings/status")
          return json(
            settingsStatus({
              status: "partial",
              updated: 1,
              stale: 1,
              unavailable: 1,
              finished_at: "2026-07-20T01:00:02Z",
            }),
          );
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    renderOverview();

    const summary = await screen.findByRole("region", {
      name: "上次行情刷新",
    });
    expect(summary).toHaveTextContent("部分完成");
    expect(summary).toHaveTextContent("更新 1 · 陈旧 1 · 不可用 1");
    const refreshTime = summary.querySelector("time");
    expect(refreshTime).toHaveAttribute("datetime", "2026-07-20T01:00:02Z");
    expect(refreshTime).not.toHaveTextContent("T");
  });

  it("keeps currencies independent and explains estimated, stale, unavailable, and zero-cost values", async () => {
    const usdAccount = {
      ...account,
      id: "10000000-0000-4000-8000-000000000009",
      currency: "USD",
      name: "美元账户",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input);
        if (url === "/api/v1/accounts?archived=false") {
          return json({ items: [account, usdAccount], next_cursor: null });
        }
        if (url === "/api/v1/positions?archived=false") {
          return json({ items: positions, next_cursor: null });
        }
        if (url === "/api/v1/portfolio/summary?currency=CNY")
          return json(cnySummary);
        if (url === "/api/v1/portfolio/summary?currency=USD") {
          return json({
            ...cnySummary,
            currency: "USD",
            position_count: 0,
            valued_count: 0,
            stale_count: 0,
            unvalued_count: 0,
            market_value: "0",
            position_cost: "0",
            valued_position_cost: "0",
            unvalued_cost: "0",
            unrealized_pnl: "0",
            unrealized_pnl_pct: null,
            known_cash: "25",
            unknown_cash_account_count: 0,
            total_value: "25",
            estimated: false,
            positions: [],
          });
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderOverview();

    const tabs = await screen.findByRole("tablist", { name: "币种" });
    expect(within(tabs).getByRole("tab", { name: "CNY" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(tabs).getByRole("tab", { name: "USD" })).toBeVisible();
    expect(
      within(tabs).queryByRole("tab", { name: "HKD" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/合计|折合/)).not.toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "估算总资产" }),
    ).toBeVisible();
    expect(screen.getByText("1 个账户现金未知")).toBeVisible();
    expect(screen.getByText("1 项未估值")).toBeVisible();
    expect(screen.getByText("未估值成本 40.00 CNY")).toBeVisible();

    const details = screen.getByRole("table", { name: "持仓估值明细" });
    const staleRow = within(details).getByRole("row", { name: /600519\.SH/ });
    expect(staleRow).toHaveTextContent("陈旧");
    expect(staleRow).toHaveTextContent("eastmoney");
    const quoteTime = staleRow.querySelector(
      'time[datetime="2026-07-19T01:00:00Z"]',
    );
    expect(quoteTime).toHaveAttribute("datetime", "2026-07-19T01:00:00Z");
    expect(quoteTime).not.toHaveTextContent("T");
    expect(
      within(details).getByRole("row", { name: /510300\.SH/ }),
    ).toHaveTextContent("不可用");
    const zeroCost = within(details).getByRole("row", { name: /000001\.SZ/ });
    expect(zeroCost).toHaveTextContent("50.00 CNY");
    expect(zeroCost).not.toHaveTextContent("%");

    const allocation = screen.getByRole("table", { name: "持仓配置" });
    expect(allocation).toHaveTextContent("600519.SH");
    expect(allocation).toHaveTextContent("66.67%");
    expect(allocation).not.toHaveTextContent("510300.SH");

    const analysis = screen.getByRole("region", { name: "持仓分析" });
    expect(analysis).toHaveTextContent("估值覆盖2 / 3");
    expect(analysis).toHaveTextContent("盈利 2 · 亏损 0 · 持平 0 · 未估值 1");
    expect(analysis).toHaveTextContent("最大持仓600519.SH · 66.67%");
    expect(analysis).toHaveTextContent("新鲜 1 · 陈旧 1 · 不可用 1");

    await user.click(within(tabs).getByRole("tab", { name: "USD" }));
    expect(
      await screen.findByRole("heading", { name: "总资产" }),
    ).toBeVisible();
    expect(screen.getAllByText("25.00 USD")[0]).toBeVisible();
    expect(screen.queryByText("150.00 CNY")).not.toBeInTheDocument();
  });

  it("starts refresh only on demand, reports partial completion, and focuses status", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/v1/accounts?archived=false")
        return json({ items: [account], next_cursor: null });
      if (url === "/api/v1/positions?archived=false")
        return json({ items: positions, next_cursor: null });
      if (url === "/api/v1/portfolio/summary?currency=CNY")
        return json(cnySummary);
      if (url === "/api/v1/market-data/refresh" && init?.method === "POST") {
        return json({
          run_id: "40000000-0000-4000-8000-000000000001",
          status: "partial",
          updated: 1,
          stale: 1,
          unavailable: 1,
          terminal_error: null,
          providers_used: ["eastmoney"],
          started_at: "2026-07-20T01:00:00Z",
          finished_at: "2026-07-20T01:00:02Z",
          items: [],
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderOverview();
    expect((await screen.findAllByText("150.00 CNY"))[0]).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/refresh"),
      ),
    ).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "刷新行情" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("行情刷新部分完成");
    expect(status).toHaveTextContent("更新 1 · 陈旧 1 · 不可用 1");
    expect(within(status).getByRole("heading")).toHaveFocus();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/refresh"),
      ),
    ).toHaveLength(1);
  });

  it("polls the existing run when refresh is already in progress", async () => {
    let polls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input, init) => {
        const url = String(input);
        if (url === "/api/v1/accounts?archived=false")
          return json({ items: [account], next_cursor: null });
        if (url === "/api/v1/positions?archived=false")
          return json({ items: positions, next_cursor: null });
        if (url === "/api/v1/portfolio/summary?currency=CNY")
          return json(cnySummary);
        if (url === "/api/v1/market-data/refresh" && init?.method === "POST") {
          return json(
            {
              error: {
                code: "QUOTE_REFRESH_IN_PROGRESS",
                fields: { run_id: "40000000-0000-4000-8000-000000000002" },
              },
            },
            409,
          );
        }
        if (
          url ===
          "/api/v1/market-data/refresh/40000000-0000-4000-8000-000000000002"
        ) {
          polls += 1;
          return json({
            run_id: "40000000-0000-4000-8000-000000000002",
            status: polls === 1 ? "running" : "succeeded",
            updated: polls === 1 ? 0 : 3,
            stale: 0,
            unavailable: 0,
            terminal_error: null,
            providers_used: polls === 1 ? [] : ["eastmoney"],
            started_at: "2026-07-20T01:00:00Z",
            finished_at: polls === 1 ? null : "2026-07-20T01:00:02Z",
            items: [],
          });
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderOverview();
    expect((await screen.findAllByText("150.00 CNY"))[0]).toBeVisible();
    await user.click(screen.getByRole("button", { name: "刷新行情" }));
    expect(await screen.findByText("行情刷新完成")).toBeVisible();
    expect(polls).toBe(2);
  });

  it("stops polling a stuck refresh and surfaces a bounded timeout", async () => {
    let polls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input, init) => {
        const url = String(input);
        if (url === "/api/v1/accounts?archived=false")
          return json({ items: [account], next_cursor: null });
        if (url === "/api/v1/positions?archived=false")
          return json({ items: positions, next_cursor: null });
        if (url === "/api/v1/accounts?archived=true")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/positions?archived=true")
          return json({ items: [], next_cursor: null });
        if (url === "/api/v1/portfolio/summary?currency=CNY")
          return json(cnySummary);
        if (url === "/api/v1/settings/status") return json(settingsStatus());
        if (url === "/api/v1/market-data/refresh" && init?.method === "POST") {
          return json(
            {
              error: {
                code: "QUOTE_REFRESH_IN_PROGRESS",
                fields: {
                  run_id: "40000000-0000-4000-8000-000000000099",
                },
              },
            },
            409,
          );
        }
        if (
          url ===
          "/api/v1/market-data/refresh/40000000-0000-4000-8000-000000000099"
        ) {
          polls += 1;
          return json({
            run_id: "40000000-0000-4000-8000-000000000099",
            status: "running",
            updated: 0,
            stale: 0,
            unavailable: 0,
            terminal_error: null,
            providers_used: [],
            started_at: "2026-07-20T01:00:00Z",
            finished_at: null,
            items: [],
          });
        }
        throw new Error(`Unexpected request ${url}`);
      }),
    );
    renderOverview();
    expect((await screen.findAllByText("150.00 CNY"))[0]).toBeVisible();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "刷新行情" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getByText("行情刷新等待超时，请稍后重试")).toBeVisible();
    expect(polls).toBeLessThanOrEqual(100);
    vi.useRealTimers();
  });

  it("retains one refresh idempotency key across a surfaced network retry", async () => {
    let attempts = 0;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/v1/accounts?archived=false")
        return json({ items: [account], next_cursor: null });
      if (url === "/api/v1/positions?archived=false")
        return json({ items: positions, next_cursor: null });
      if (url === "/api/v1/portfolio/summary?currency=CNY")
        return json(cnySummary);
      if (url === "/api/v1/market-data/refresh" && init?.method === "POST") {
        attempts += 1;
        if (attempts === 1) throw new TypeError("private network detail");
        return json({
          run_id: "40000000-0000-4000-8000-000000000003",
          status: "succeeded",
          updated: 3,
          stale: 0,
          unavailable: 0,
          terminal_error: null,
          providers_used: ["eastmoney"],
          started_at: "2026-07-20T01:00:00Z",
          finished_at: "2026-07-20T01:00:02Z",
          items: [],
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderOverview();
    expect((await screen.findAllByText("150.00 CNY"))[0]).toBeVisible();
    await user.click(screen.getByRole("button", { name: "刷新行情" }));
    await screen.findByText("暂时无法刷新行情，请重试");
    await user.click(screen.getByRole("button", { name: "刷新行情" }));
    await screen.findByText("行情刷新完成");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input).endsWith("/refresh") && init?.method === "POST",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });
});
