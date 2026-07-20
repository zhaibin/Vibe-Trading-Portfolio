import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api, newIdempotencyKey } from "../api/client";
import {
  accountsQuery,
  holdingsKeys,
  positionsQuery,
  settingsQuery,
  summaryQuery,
  type Currency,
  type RefreshRun,
} from "../api/queries";
import { AllocationBars } from "../components/AllocationBars";
import { CurrencyTabs } from "../components/CurrencyTabs";
import { HoldingAnalysis } from "../components/HoldingAnalysis";
import {
  PositionTable,
  type OverviewPosition,
} from "../components/PositionTable";
import { StatusMessage, type Notice } from "../components/StatusMessage";
import { SummaryCards } from "../components/SummaryCards";
import { formatLocalDateTime } from "../lib/dateTime";

const CURRENCY_ORDER: Currency[] = ["CNY", "HKD", "USD"];
const POLL_DELAY_MS = 250;
const MAX_POLL_ATTEMPTS = 80;

class RefreshPollingTimeout extends Error {}

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, POLL_DELAY_MS);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function runFromError(error: ApiError): RefreshRun | undefined {
  const value = error.fields?.run;
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return undefined;
  const run = value as Partial<RefreshRun>;
  return typeof run.run_id === "string" && typeof run.status === "string"
    ? (run as RefreshRun)
    : undefined;
}

async function pollRun(
  runId: string,
  signal: AbortSignal,
): Promise<RefreshRun> {
  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
    const run = await api.get("/api/v1/market-data/refresh/{run_id}", {
      params: { path: { run_id: runId } },
      signal,
    });
    if (run.status !== "running") return run;
    if (attempt + 1 < MAX_POLL_ATTEMPTS) await waitForPoll(signal);
  }
  throw new RefreshPollingTimeout();
}

export function OverviewPage() {
  const queryClient = useQueryClient();
  const accounts = useQuery(accountsQuery(false));
  const positions = useQuery(positionsQuery(false));
  const [selected, setSelected] = useState<Currency>();
  const [notice, setNotice] = useState<Notice>();
  const refreshKey = useRef<string | undefined>(undefined);
  const refreshController = useRef<AbortController | undefined>(undefined);
  const currencies = useMemo(() => {
    const available = new Set<Currency>();
    for (const item of accounts.data?.items ?? []) available.add(item.currency);
    for (const item of positions.data?.items ?? [])
      available.add(item.instrument.currency);
    return CURRENCY_ORDER.filter((currency) => available.has(currency));
  }, [accounts.data, positions.data]);

  useEffect(() => () => refreshController.current?.abort(), []);

  const activeCurrency =
    selected !== undefined && currencies.includes(selected)
      ? selected
      : currencies[0];

  const activeEmpty =
    accounts.isSuccess && positions.isSuccess && currencies.length === 0;
  const archivedAccounts = useQuery({
    ...accountsQuery(true),
    enabled: activeEmpty,
  });
  const archivedPositions = useQuery({
    ...positionsQuery(true),
    enabled: activeEmpty,
  });
  const archivedLoaded =
    activeEmpty && archivedAccounts.isSuccess && archivedPositions.isSuccess;
  const hasArchived =
    archivedLoaded &&
    ((archivedAccounts.data?.items.length ?? 0) > 0 ||
      (archivedPositions.data?.items.length ?? 0) > 0);

  const summary = useQuery({
    ...summaryQuery(activeCurrency ?? "CNY"),
    enabled: activeCurrency !== undefined,
  });
  const settings = useQuery({
    ...settingsQuery(),
    enabled: activeCurrency !== undefined,
  });
  const records = useMemo<OverviewPosition[]>(() => {
    if (summary.data === undefined || positions.data === undefined) return [];
    return summary.data.positions.flatMap((item) => {
      const position = positions.data.items.find(
        (candidate) => candidate.id === item.position_id,
      );
      return position === undefined ? [] : [{ summary: item, position }];
    });
  }, [positions.data, summary.data]);

  const refresh = useMutation({
    mutationFn: async ({
      key,
      signal,
    }: {
      key: string;
      signal: AbortSignal;
    }) => {
      let run: RefreshRun;
      try {
        run = await api.post("/api/v1/market-data/refresh", {}, key, {
          signal,
        });
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.code === "QUOTE_REFRESH_IN_PROGRESS"
        ) {
          const runId = error.fields?.run_id;
          if (typeof runId === "string") return pollRun(runId, signal);
        }
        if (error instanceof ApiError && error.code === "QUOTE_UNAVAILABLE") {
          const failed = runFromError(error);
          if (failed !== undefined) return failed;
        }
        throw error;
      }
      return run.status === "running" ? pollRun(run.run_id, signal) : run;
    },
    onSuccess: async (run) => {
      refreshKey.current = undefined;
      setNotice({
        kind: run.status === "failed" ? "error" : "success",
        title:
          run.status === "partial"
            ? "行情刷新部分完成"
            : run.status === "failed"
              ? "行情刷新失败"
              : "行情刷新完成",
        detail: `更新 ${run.updated} · 陈旧 ${run.stale} · 不可用 ${run.unavailable}`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["summary"] }),
        queryClient.invalidateQueries({ queryKey: holdingsKeys.settings }),
      ]);
    },
    onError: (error) => {
      if (error instanceof Error && error.name === "AbortError") return;
      if (error instanceof RefreshPollingTimeout) {
        setNotice({
          kind: "error",
          title: "行情刷新等待超时，请稍后重试",
        });
        return;
      }
      setNotice({ kind: "error", title: "暂时无法刷新行情，请重试" });
    },
  });

  function startRefresh() {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    refreshKey.current ??= newIdempotencyKey();
    refresh.mutate({ key: refreshKey.current, signal: controller.signal });
  }

  const firstRun = archivedLoaded && !hasArchived;
  return (
    <section aria-labelledby="overview-heading">
      <h1 id="overview-heading">投资组合总览</h1>
      <p>按币种独立查看本地账户与持仓估值，不进行跨币种合并。</p>
      {notice === undefined ? null : <StatusMessage notice={notice} />}
      {accounts.isPending ||
      positions.isPending ||
      (activeEmpty &&
        (archivedAccounts.isPending || archivedPositions.isPending)) ? (
        <p role="status">正在加载投资组合…</p>
      ) : null}
      {accounts.isError ||
      positions.isError ||
      (activeEmpty &&
        (archivedAccounts.isError || archivedPositions.isError)) ? (
        <StatusMessage
          notice={{ kind: "error", title: "暂时无法加载投资组合，请重试" }}
        />
      ) : null}
      {firstRun ? (
        <section
          className="empty-state"
          aria-labelledby="overview-empty-heading"
        >
          <h2 id="overview-empty-heading">还没有持仓</h2>
          <p>先创建一个固定币种账户，再添加持仓。</p>
          <Link className="primary-link" to="/holdings">
            创建账户
          </Link>
        </section>
      ) : null}
      {hasArchived ? (
        <section
          className="empty-state"
          aria-labelledby="overview-archived-heading"
        >
          <h2 id="overview-archived-heading">没有当前账户或持仓</h2>
          <p>投资组合中只有已归档项目。</p>
          <Link className="primary-link" to="/holdings">
            查看已归档项目
          </Link>
        </section>
      ) : null}
      {activeCurrency === undefined ? null : (
        <>
          <CurrencyTabs
            currencies={currencies}
            selected={activeCurrency}
            onSelect={setSelected}
          />
          <section
            id="currency-panel"
            role="tabpanel"
            aria-labelledby={`currency-tab-${activeCurrency}`}
          >
            {summary.isPending ? (
              <p role="status">正在加载 {activeCurrency} 概览…</p>
            ) : null}
            {summary.isError ? (
              <StatusMessage
                notice={{
                  kind: "error",
                  title: `暂时无法加载 ${activeCurrency} 概览`,
                }}
              />
            ) : null}
            {summary.data === undefined ? null : (
              <>
                <SummaryCards summary={summary.data} />
                {summary.data.position_count === 0 ? (
                  <p>该币种暂无当前持仓。</p>
                ) : null}
                {records.length === summary.data.positions.length &&
                records.length > 0 ? (
                  <>
                    <HoldingAnalysis records={records} summary={summary.data} />
                    <AllocationBars
                      currency={activeCurrency}
                      records={records}
                    />
                    <PositionTable
                      currency={activeCurrency}
                      records={records}
                    />
                  </>
                ) : null}
                {records.length !== summary.data.positions.length ? (
                  <StatusMessage
                    notice={{
                      kind: "error",
                      title: "持仓明细暂时不完整，请重新载入",
                    }}
                  />
                ) : null}
              </>
            )}
          </section>
          <section className="refresh-panel" aria-labelledby="refresh-heading">
            <h2 id="refresh-heading">行情刷新</h2>
            <p>行情仅在你明确操作时刷新，不会在页面加载时自动请求提供方。</p>
            {settings.data?.last_refresh === null ||
            settings.data?.last_refresh === undefined ? null : (
              <section aria-labelledby="last-refresh-heading">
                <h3 id="last-refresh-heading">上次行情刷新</h3>
                <p>
                  {settings.data.last_refresh.status === "partial"
                    ? "部分完成"
                    : settings.data.last_refresh.status === "failed"
                      ? "失败"
                      : "完成"}
                </p>
                <p>
                  更新 {settings.data.last_refresh.updated} · 陈旧{" "}
                  {settings.data.last_refresh.stale} · 不可用{" "}
                  {settings.data.last_refresh.unavailable}
                </p>
                <time dateTime={settings.data.last_refresh.finished_at}>
                  {formatLocalDateTime(settings.data.last_refresh.finished_at)}
                </time>
              </section>
            )}
            <button
              type="button"
              disabled={refresh.isPending}
              onClick={startRefresh}
            >
              {refresh.isPending ? "正在刷新行情…" : "刷新行情"}
            </button>
          </section>
        </>
      )}
    </section>
  );
}
