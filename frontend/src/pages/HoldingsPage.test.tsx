import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HoldingsPage } from "./HoldingsPage";

interface AccountFixture {
  id: string;
  name: string;
  currency: "CNY" | "HKD" | "USD";
  cash_balance: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

interface ApiFixtureOptions {
  accountFailures?: number;
  accountPatchFailures?: number;
  accountErrorCode?: string;
  accountPatchErrorCode?: string;
  accountPatchErrorFields?: Record<string, string | number>;
  accounts?: AccountFixture[];
  accountListHandler?: (archived: boolean) => Promise<Response>;
  candidateCurrency?: "CNY" | "HKD" | "USD";
  confirmationFailures?: number;
  confirmationHandler?: (candidateId: string) => Promise<Response>;
  conflictPositionPatch?: boolean;
  positionCreateErrorFields?: Record<string, string>;
  positionCreateFailures?: number;
  positionGetFailures?: number;
  positionPatchFailures?: number;
  positionPatchErrorCode?: string;
  positionOnLaterPage?: boolean;
  positions?: PositionFixture[];
  positionListHandler?: (archived: boolean) => Promise<Response>;
  searchHandler?: (
    query: string,
    signal: AbortSignal | undefined,
  ) => Promise<Response>;
}

interface InstrumentFixture {
  canonical_symbol: string;
  name: string;
  market: "CN_SH" | "CN_SZ" | "CN_BJ" | "HK" | "US";
  currency: "CNY" | "HKD" | "USD";
  asset_type: "equity" | "etf";
}

interface PositionFixture {
  id: string;
  account_id: string;
  instrument_id: string;
  instrument: InstrumentFixture;
  quantity: string;
  average_cost: string;
  note: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status });
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function candidateResponse(
  name: string,
  symbol: string,
  candidateId: string,
): Response {
  return json([
    {
      candidate_id: candidateId,
      canonical_symbol: symbol,
      name,
      market: "US",
      currency: "USD",
      asset_type: "equity",
      sources: ["synthetic-primary"],
    },
  ]);
}

function createApiFixture(options: ApiFixtureOptions = {}) {
  const accounts = (options.accounts ?? []).map((account) => ({ ...account }));
  const positions: PositionFixture[] = (options.positions ?? []).map(
    (position) => ({
      ...position,
    }),
  );
  let accountFailures = options.accountFailures ?? 0;
  let accountPatchFailures = options.accountPatchFailures ?? 0;
  let confirmationFailures = options.confirmationFailures ?? 0;
  let conflictPositionPatch = options.conflictPositionPatch ?? false;
  let positionPatchFailures = options.positionPatchFailures ?? 0;
  let positionGetFailures = options.positionGetFailures ?? 0;
  let positionCreateFailures = options.positionCreateFailures ?? 0;
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.startsWith("/api/v1/accounts") && method === "GET") {
      const archived =
        new URL(url, "http://test.local").searchParams.get("archived") ===
        "true";
      if (options.accountListHandler !== undefined) {
        return options.accountListHandler(archived);
      }
      return json({
        items: accounts.filter(
          (account) => (account.archived_at !== null) === archived,
        ),
        next_cursor: null,
      });
    }
    if (url.startsWith("/api/v1/positions?archived=") && method === "GET") {
      const archived =
        new URL(url, "http://test.local").searchParams.get("archived") ===
        "true";
      if (options.positionListHandler !== undefined) {
        return options.positionListHandler(archived);
      }
      if (positionGetFailures > 0) {
        positionGetFailures -= 1;
        throw new TypeError("private positions load detail");
      }
      const cursor = new URL(url, "http://test.local").searchParams.get(
        "cursor",
      );
      if (options.positionOnLaterPage && !archived && cursor === null) {
        return json({ items: [], next_cursor: "next-position-page" });
      }
      return json({
        items: positions.filter(
          (position) => (position.archived_at !== null) === archived,
        ),
        next_cursor: null,
      });
    }
    if (url === "/api/v1/accounts" && method === "POST") {
      if (options.accountErrorCode !== undefined) {
        return json({ error: { code: options.accountErrorCode } }, 409);
      }
      if (accountFailures > 0) {
        accountFailures -= 1;
        throw new TypeError("private network detail");
      }
      const body = JSON.parse(String(init?.body)) as {
        name: string;
        currency: "CNY" | "HKD" | "USD";
        cash_balance: string | null;
      };
      const account: AccountFixture = {
        id: `account-${String(accounts.length + 1)}`,
        ...body,
        version: 1,
        created_at: "2026-07-20T00:00:00Z",
        updated_at: "2026-07-20T00:00:00Z",
        archived_at: null,
      };
      accounts.push(account);
      return json(account, 201);
    }
    if (url.startsWith("/api/v1/instruments/search?") && method === "GET") {
      if (options.searchHandler !== undefined) {
        const query =
          new URL(url, "http://test.local").searchParams.get("q") ?? "";
        return options.searchHandler(query, init?.signal ?? undefined);
      }
      return json([
        {
          candidate_id: "00000000-0000-4000-8000-000000000013",
          canonical_symbol: "TEST.US",
          name: "示例科技",
          market: "US",
          currency: options.candidateCurrency ?? "USD",
          asset_type: "equity",
          sources: ["synthetic-primary", "synthetic-cache"],
        },
      ]);
    }
    if (url === "/api/v1/instruments/confirm" && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { candidate_id: string };
      if (options.confirmationHandler !== undefined) {
        return options.confirmationHandler(body.candidate_id);
      }
      if (confirmationFailures > 0) {
        confirmationFailures -= 1;
        throw new TypeError("private confirmation failure");
      }
      return json(
        {
          id: "00000000-0000-4000-8000-000000000113",
          canonical_symbol: "TEST.US",
          name: "示例科技",
          market: "US",
          currency: options.candidateCurrency ?? "USD",
          asset_type: "equity",
          created_at: "2026-07-20T00:00:00Z",
          updated_at: "2026-07-20T00:00:00Z",
        },
        201,
      );
    }
    if (url === "/api/v1/positions" && method === "POST") {
      if (positionCreateFailures > 0) {
        positionCreateFailures -= 1;
        throw new TypeError("private position create failure");
      }
      if (options.positionCreateErrorFields !== undefined) {
        return json(
          {
            error: {
              code: "VALIDATION_ERROR",
              fields: options.positionCreateErrorFields,
            },
          },
          422,
        );
      }
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const position: PositionFixture = {
        id: "00000000-0000-4000-8000-000000000213",
        account_id: String(body.account_id),
        instrument_id: String(body.instrument_id),
        instrument: {
          canonical_symbol: "TEST.US",
          name: "示例科技",
          market: "US",
          currency: options.candidateCurrency ?? "USD",
          asset_type: "equity",
        },
        quantity: String(body.quantity),
        average_cost: String(body.average_cost),
        note: body.note === null ? null : String(body.note),
        version: 1,
        created_at: "2026-07-20T00:00:00Z",
        updated_at: "2026-07-20T00:00:00Z",
        archived_at: null,
      };
      positions.push(position);
      return json(position, 201);
    }
    if (url.startsWith("/api/v1/accounts/") && method === "PATCH") {
      const id = url.split("/").at(-1);
      const account = accounts.find((item) => item.id === id);
      if (account === undefined) {
        return json({ error: { code: "NOT_FOUND" } }, 404);
      }
      if (options.accountPatchErrorCode !== undefined) {
        return json(
          {
            error: {
              code: options.accountPatchErrorCode,
              fields: options.accountPatchErrorFields,
            },
          },
          409,
        );
      }
      if (accountPatchFailures > 0) {
        accountPatchFailures -= 1;
        throw new TypeError("private account edit failure");
      }
      const body = JSON.parse(String(init?.body)) as {
        name?: string;
        cash_balance?: string | null;
        archived?: boolean;
      };
      Object.assign(account, body, {
        version: account.version + 1,
        archived_at: body.archived ? "2026-07-20T01:00:00Z" : null,
      });
      return json(account);
    }
    if (url.startsWith("/api/v1/positions/") && method === "PATCH") {
      const id = url.split("/").at(-1);
      const position = positions.find((item) => item.id === id);
      if (position === undefined) {
        return json({ error: { code: "NOT_FOUND" } }, 404);
      }
      if (positionPatchFailures > 0) {
        positionPatchFailures -= 1;
        throw new TypeError("private archive network detail");
      }
      if (options.positionPatchErrorCode !== undefined) {
        return json({ error: { code: options.positionPatchErrorCode } }, 409);
      }
      if (conflictPositionPatch) {
        conflictPositionPatch = false;
        position.version = 2;
        return json(
          {
            error: {
              code: "CONCURRENT_MODIFICATION",
              fields: { version: 2 },
            },
          },
          409,
        );
      }
      const body = JSON.parse(String(init?.body)) as {
        quantity?: string;
        average_cost?: string;
        note?: string | null;
        archived?: boolean;
      };
      Object.assign(position, body, {
        version: position.version + 1,
        archived_at:
          body.archived === undefined
            ? position.archived_at
            : body.archived
              ? "2026-07-20T01:00:00Z"
              : null,
      });
      return json(position);
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock };
}

function renderHoldings({ mutationRetry = false } = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: mutationRetry ? 1 : false, retryDelay: 0 },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return render(<HoldingsPage />, { wrapper: Wrapper });
}

function accountPostCalls(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input) === "/api/v1/accounts" && init?.method === "POST",
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("HoldingsPage account flow", () => {
  it("shows empty onboarding and performs no quote refresh on load", async () => {
    const { fetchMock } = createApiFixture();

    renderHoldings();

    expect(await screen.findByText("还没有账户")).toBeVisible();
    expect(screen.getByLabelText("账户名称")).toBeVisible();
    expect(screen.getByLabelText("账户币种")).toBeVisible();
    expect(screen.getByLabelText("现金余额（可选）")).toBeVisible();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/market-data/refresh"),
      ),
    ).toBe(false);
  });

  it("creates an account while preserving an omitted cash balance as unknown", async () => {
    const { fetchMock } = createApiFixture();
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("还没有账户");

    await user.type(screen.getByLabelText("账户名称"), "示例美元账户");
    await user.selectOptions(screen.getByLabelText("账户币种"), "USD");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("账户已创建");
    expect(within(status).getByRole("heading")).toHaveFocus();
    const post = accountPostCalls(fetchMock)[0];
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      name: "示例美元账户",
      currency: "USD",
      cash_balance: null,
    });
    expect(new Headers(post?.[1]?.headers).get("Idempotency-Key")).toMatch(
      /^portfolio-/,
    );
    expect(await screen.findByText("示例美元账户")).toBeVisible();
    expect(screen.getByText("现金余额未知")).toBeVisible();
  });

  it("keeps a money precision error adjacent to the cash field without posting", async () => {
    const { fetchMock } = createApiFixture();
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("还没有账户");

    await user.type(screen.getByLabelText("账户名称"), "精度测试账户");
    await user.type(screen.getByLabelText("现金余额（可选）"), "1.1234567");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    expect(screen.getByText("现金余额最多保留 6 位小数")).toHaveAttribute(
      "id",
      "account-cash-error",
    );
    expect(screen.getByLabelText("现金余额（可选）")).toHaveAttribute(
      "aria-describedby",
      "account-cash-error",
    );
    expect(accountPostCalls(fetchMock)).toHaveLength(0);
  });

  it("keeps a server account-name error adjacent to the name field", async () => {
    createApiFixture({ accountErrorCode: "DUPLICATE_ACCOUNT_NAME" });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("还没有账户");

    await user.type(screen.getByLabelText("账户名称"), "重复账户");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    expect(
      await screen.findByText("账户名称已存在，请换一个名称。", {
        selector: "#account-name-error",
      }),
    ).toHaveAttribute("id", "account-name-error");
    expect(screen.getByLabelText("账户名称")).toHaveAttribute(
      "aria-describedby",
      "account-name-error",
    );
  });

  it("preserves values and the idempotency key across a network retry", async () => {
    const { fetchMock } = createApiFixture({ accountFailures: 2 });
    const user = userEvent.setup();
    renderHoldings({ mutationRetry: true });
    await screen.findByText("还没有账户");

    await user.type(screen.getByLabelText("账户名称"), "重试账户");
    await user.type(screen.getByLabelText("现金余额（可选）"), "12.50");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("暂时无法保存账户，请重试");
    expect(error).not.toHaveTextContent("private network detail");
    expect(within(error).getByRole("heading")).toHaveFocus();
    expect(screen.getByLabelText("账户名称")).toHaveValue("重试账户");
    expect(screen.getByLabelText("现金余额（可选）")).toHaveValue("12.50");

    const firstAttemptKeys = accountPostCalls(fetchMock).map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    );
    expect(firstAttemptKeys).toHaveLength(2);
    expect(new Set(firstAttemptKeys).size).toBe(1);

    await user.click(screen.getByRole("button", { name: "创建账户" }));
    await screen.findByText("账户已创建");
    const allKeys = accountPostCalls(fetchMock).map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    );
    expect(new Set(allKeys).size).toBe(1);

    await waitFor(() =>
      expect(screen.getByLabelText("账户名称")).toHaveValue(""),
    );
  });
});

const usdAccount: AccountFixture = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "示例美元账户",
  currency: "USD",
  cash_balance: null,
  version: 1,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  archived_at: null,
};

describe("HoldingsPage trusted position flow", () => {
  it("searches only on submit and requires canonical candidate confirmation", async () => {
    const { fetchMock } = createApiFixture({ accounts: [usdAccount] });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "示例");
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).startsWith("/api/v1/instruments/search?"),
      ),
    ).toHaveLength(0);
    expect(screen.queryByLabelText("持仓数量")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "搜索" }));

    const candidate = await screen.findByRole("article", {
      name: "示例科技 TEST.US",
    });
    expect(candidate).toHaveTextContent("US");
    expect(candidate).toHaveTextContent("equity");
    expect(candidate).toHaveTextContent("USD");
    expect(candidate).toHaveTextContent("synthetic-primary、synthetic-cache");
    expect(screen.queryByLabelText("持仓数量")).not.toBeInTheDocument();

    await user.click(
      within(candidate).getByRole("button", {
        name: "确认 示例科技 TEST.US",
      }),
    );

    expect(await screen.findByLabelText("持仓数量")).toBeVisible();
    const confirmation = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/instruments/confirm" &&
        init?.method === "POST",
    );
    expect(JSON.parse(String(confirmation?.[1]?.body))).toEqual({
      candidate_id: "00000000-0000-4000-8000-000000000013",
    });
  });

  it("blocks a confirmed instrument whose currency differs from the account", async () => {
    const cnyAccount = {
      ...usdAccount,
      name: "示例人民币账户",
      currency: "CNY" as const,
    };
    const { fetchMock } = createApiFixture({
      accounts: [cnyAccount],
      candidateCurrency: "USD",
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例人民币账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "TEST");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 示例科技 TEST.US" }),
    );

    expect(
      await screen.findByText("证券币种 USD 与账户币种 CNY 不一致"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "添加持仓" })).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/positions" && init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("validates exact precision and creates a confirmed position", async () => {
    const { fetchMock } = createApiFixture({ accounts: [usdAccount] });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.type(screen.getByLabelText("证券代码或名称"), "TEST");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 示例科技 TEST.US" }),
    );

    await user.type(await screen.findByLabelText("持仓数量"), "1.123456789");
    await user.type(screen.getByLabelText("平均成本"), "10.1234567");
    await user.click(screen.getByRole("button", { name: "添加持仓" }));

    expect(screen.getByText("持仓数量最多保留 8 位小数")).toBeVisible();
    expect(screen.getByText("平均成本最多保留 6 位小数")).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/v1/positions" && init?.method === "POST",
      ),
    ).toHaveLength(0);

    await user.clear(screen.getByLabelText("持仓数量"));
    await user.type(screen.getByLabelText("持仓数量"), "1.25");
    await user.clear(screen.getByLabelText("平均成本"));
    await user.type(screen.getByLabelText("平均成本"), "10.125");
    await user.type(screen.getByLabelText("备注（可选）"), "仅作测试记录");
    await user.click(screen.getByRole("button", { name: "添加持仓" }));

    expect(await screen.findByText("持仓已创建")).toBeVisible();
    const creation = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/positions" && init?.method === "POST",
    );
    expect(JSON.parse(String(creation?.[1]?.body))).toEqual({
      account_id: usdAccount.id,
      instrument_id: "00000000-0000-4000-8000-000000000113",
      quantity: "1.25",
      average_cost: "10.125",
      note: "仅作测试记录",
    });
  });

  it("keeps a server note validation error adjacent to the field", async () => {
    createApiFixture({
      accounts: [usdAccount],
      positionCreateErrorFields: { note: "invalid" },
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.type(screen.getByLabelText("证券代码或名称"), "TEST");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 示例科技 TEST.US" }),
    );
    await user.type(await screen.findByLabelText("持仓数量"), "1");
    await user.type(screen.getByLabelText("平均成本"), "10");
    await user.type(screen.getByLabelText("备注（可选）"), "不可接受的备注");
    await user.click(screen.getByRole("button", { name: "添加持仓" }));

    expect(
      await screen.findByText("备注包含不支持的字符或长度过长"),
    ).toHaveAttribute("id", "position-note-error");
    expect(screen.getByLabelText("备注（可选）")).toHaveAttribute(
      "aria-describedby",
      "position-note-error",
    );
  });

  it("silently aborts an older search and keeps the newer result", async () => {
    const oldSearch = deferred<Response>();
    const newSearch = deferred<Response>();
    createApiFixture({
      accounts: [usdAccount],
      searchHandler: (query, signal) => {
        if (query === "旧") {
          signal?.addEventListener("abort", () =>
            oldSearch.reject(new DOMException("aborted", "AbortError")),
          );
          return oldSearch.promise;
        }
        return newSearch.promise;
      },
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "旧");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.clear(screen.getByLabelText("证券代码或名称"));
    await user.type(screen.getByLabelText("证券代码或名称"), "新");
    await user.click(screen.getByRole("button", { name: /搜索/ }));
    newSearch.resolve(candidateResponse("新证券", "NEW.US", "new-candidate"));

    expect(
      await screen.findByRole("article", { name: "新证券 NEW.US" }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("旧证券 OLD.US")).not.toBeInTheDocument();
  });

  it("ignores a stale successful search that completes after the latest result", async () => {
    const oldSearch = deferred<Response>();
    const newSearch = deferred<Response>();
    createApiFixture({
      accounts: [usdAccount],
      searchHandler: (query) =>
        query === "旧" ? oldSearch.promise : newSearch.promise,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "旧");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.clear(screen.getByLabelText("证券代码或名称"));
    await user.type(screen.getByLabelText("证券代码或名称"), "新");
    await user.click(screen.getByRole("button", { name: /搜索/ }));
    newSearch.resolve(candidateResponse("新证券", "NEW.US", "new-candidate"));
    await screen.findByRole("article", { name: "新证券 NEW.US" });
    oldSearch.resolve(candidateResponse("旧证券", "OLD.US", "old-candidate"));

    await waitFor(() =>
      expect(
        screen.queryByRole("article", { name: "旧证券 OLD.US" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("article", { name: "新证券 NEW.US" }),
    ).toBeVisible();
  });

  it("ignores a stale confirmation that completes after a newer search", async () => {
    const oldConfirmation = deferred<Response>();
    createApiFixture({
      accounts: [usdAccount],
      searchHandler: async (query) =>
        query === "旧"
          ? candidateResponse("旧证券", "OLD.US", "old-candidate")
          : candidateResponse("新证券", "NEW.US", "new-candidate"),
      confirmationHandler: () => oldConfirmation.promise,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "旧");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 旧证券 OLD.US" }),
    );
    await user.clear(screen.getByLabelText("证券代码或名称"));
    await user.type(screen.getByLabelText("证券代码或名称"), "新");
    await user.click(screen.getByRole("button", { name: /搜索/ }));
    const newCandidate = await screen.findByRole("article", {
      name: "新证券 NEW.US",
    });
    const newConfirm = within(newCandidate).getByRole("button", {
      name: "确认 新证券 NEW.US",
    });
    oldConfirmation.resolve(
      json({
        id: "00000000-0000-4000-8000-000000000199",
        canonical_symbol: "OLD.US",
        name: "旧证券",
        market: "US",
        currency: "USD",
        asset_type: "equity",
      }),
    );

    await waitFor(() => expect(newConfirm).toBeEnabled());
    expect(screen.queryByText(/已确认：旧证券/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "新证券 NEW.US" }),
    ).toBeVisible();
  });

  it("allows the current candidate to be confirmed while an obsolete confirmation never settles", async () => {
    const obsoleteConfirmation = deferred<Response>();
    createApiFixture({
      accounts: [usdAccount],
      searchHandler: async (query) =>
        query === "旧"
          ? candidateResponse("旧证券", "OLD.US", "old-candidate")
          : candidateResponse("新证券", "NEW.US", "new-candidate"),
      confirmationHandler: (candidateId) =>
        candidateId === "old-candidate"
          ? obsoleteConfirmation.promise
          : Promise.resolve(
              json(
                {
                  id: "00000000-0000-4000-8000-000000000200",
                  canonical_symbol: "NEW.US",
                  name: "新证券",
                  market: "US",
                  currency: "USD",
                  asset_type: "equity",
                  created_at: "2026-07-20T00:00:00Z",
                  updated_at: "2026-07-20T00:00:00Z",
                },
                201,
              ),
            ),
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.type(screen.getByLabelText("证券代码或名称"), "旧");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 旧证券 OLD.US" }),
    );
    await user.clear(screen.getByLabelText("证券代码或名称"));
    await user.type(screen.getByLabelText("证券代码或名称"), "新");
    await user.click(screen.getByRole("button", { name: /搜索/ }));

    const currentConfirm = await screen.findByRole("button", {
      name: "确认 新证券 NEW.US",
    });
    expect(currentConfirm).toBeEnabled();
    await user.click(currentConfirm);
    expect(await screen.findByText(/已确认：新证券 NEW.US/)).toBeVisible();
  });

  it("clears a prior search error after a successful new search", async () => {
    let attempts = 0;
    createApiFixture({
      accounts: [usdAccount],
      searchHandler: async () => {
        attempts += 1;
        return attempts === 1
          ? json({ error: { code: "SEARCH_UNAVAILABLE" } }, 503)
          : candidateResponse("恢复搜索", "BACK.US", "recovered-candidate");
      },
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.type(screen.getByLabelText("证券代码或名称"), "第一次");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "证券搜索暂时不可用，请重试",
    );
    await user.clear(screen.getByLabelText("证券代码或名称"));
    await user.type(screen.getByLabelText("证券代码或名称"), "第二次");
    await user.click(screen.getByRole("button", { name: /搜索/ }));

    await screen.findByRole("article", { name: "恢复搜索 BACK.US" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retains the confirmation key across a surfaced retry", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      confirmationFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.type(screen.getByLabelText("证券代码或名称"), "TEST");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    const confirm = await screen.findByRole("button", {
      name: "确认 示例科技 TEST.US",
    });
    await user.click(confirm);
    await screen.findByText("无法确认证券，请重新搜索");
    await user.click(confirm);
    await screen.findByLabelText("持仓数量");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === "/api/v1/instruments/confirm" &&
          init?.method === "POST",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });

  it("retains position values and key across a surfaced create retry", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positionCreateFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.type(screen.getByLabelText("证券代码或名称"), "TEST");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await user.click(
      await screen.findByRole("button", { name: "确认 示例科技 TEST.US" }),
    );
    await user.type(await screen.findByLabelText("持仓数量"), "3.25");
    await user.type(screen.getByLabelText("平均成本"), "9.5");
    await user.type(screen.getByLabelText("备注（可选）"), "保留这些值");
    await user.click(screen.getByRole("button", { name: "添加持仓" }));
    await screen.findByText("暂时无法保存持仓，请重试");
    expect(screen.getByLabelText("持仓数量")).toHaveValue("3.25");
    expect(screen.getByLabelText("平均成本")).toHaveValue("9.5");
    expect(screen.getByLabelText("备注（可选）")).toHaveValue("保留这些值");
    await user.click(screen.getByRole("button", { name: "添加持仓" }));
    await screen.findByText("持仓已创建");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === "/api/v1/positions" && init?.method === "POST",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });
});

const activePosition: PositionFixture = {
  id: "00000000-0000-4000-8000-000000000301",
  account_id: usdAccount.id,
  instrument_id: "00000000-0000-4000-8000-000000000113",
  instrument: {
    canonical_symbol: "TEST.US",
    name: "示例科技",
    market: "US",
    currency: "USD",
    asset_type: "equity",
  },
  quantity: "2.5",
  average_cost: "8.25",
  note: "测试持仓",
  version: 1,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  archived_at: null,
};

const secondSameQuantityPosition: PositionFixture = {
  ...activePosition,
  id: "00000000-0000-4000-8000-000000000302",
  instrument_id: "00000000-0000-4000-8000-000000000114",
  instrument: {
    canonical_symbol: "OTHER.US",
    name: "另一证券",
    market: "US",
    currency: "USD",
    asset_type: "etf",
  },
};

describe("HoldingsPage edit and archive flow", () => {
  it("distinguishes same-quantity positions by instrument and account identity", async () => {
    createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition, secondSameQuantityPosition],
    });
    renderHoldings();

    expect(
      await screen.findByRole("heading", { name: "示例科技 TEST.US" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "另一证券 OTHER.US" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "编辑持仓 示例科技 TEST.US 示例美元账户",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "编辑持仓 另一证券 OTHER.US 示例美元账户",
      }),
    ).toBeVisible();
  });

  it("edits an account without allowing its fixed currency to change", async () => {
    const { fetchMock } = createApiFixture({ accounts: [usdAccount] });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");

    await user.click(
      screen.getByRole("button", { name: "编辑账户 示例美元账户" }),
    );
    const edit = screen.getByRole("form", { name: "编辑账户 示例美元账户" });
    expect(within(edit).getByLabelText("账户币种")).toBeDisabled();
    await user.clear(within(edit).getByLabelText("账户名称"));
    await user.type(within(edit).getByLabelText("账户名称"), "更新后的账户");
    await user.click(within(edit).getByRole("button", { name: "保存账户" }));

    expect(await screen.findByText("账户已更新")).toBeVisible();
    const patch = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === `/api/v1/accounts/${usdAccount.id}` &&
        init?.method === "PATCH",
    );
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      version: 1,
      name: "更新后的账户",
      cash_balance: null,
    });
  });

  it("keeps a duplicate-name edit error adjacent to the account field", async () => {
    createApiFixture({
      accounts: [usdAccount],
      accountPatchErrorCode: "DUPLICATE_ACCOUNT_NAME",
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.click(
      screen.getByRole("button", { name: "编辑账户 示例美元账户" }),
    );
    const edit = screen.getByRole("form", { name: "编辑账户 示例美元账户" });
    await user.clear(within(edit).getByLabelText("账户名称"));
    await user.type(within(edit).getByLabelText("账户名称"), "重复账户");
    await user.click(within(edit).getByRole("button", { name: "保存账户" }));

    expect(
      await within(edit).findByText("账户名称已存在，请换一个名称。"),
    ).toHaveAttribute("id", `account-name-error-${usdAccount.id}`);
    expect(screen.queryByText("记录已在其他位置更新")).not.toBeInTheDocument();
  });

  it("retains account edit values and key across a surfaced retry", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      accountPatchFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.click(
      screen.getByRole("button", { name: "编辑账户 示例美元账户" }),
    );
    const edit = screen.getByRole("form", { name: "编辑账户 示例美元账户" });
    await user.clear(within(edit).getByLabelText("账户名称"));
    await user.type(within(edit).getByLabelText("账户名称"), "保留编辑账户");
    await user.type(within(edit).getByLabelText("现金余额（可选）"), "88.5");
    await user.click(within(edit).getByRole("button", { name: "保存账户" }));
    await screen.findByText("暂时无法保存账户，请重试");
    expect(within(edit).getByLabelText("账户名称")).toHaveValue("保留编辑账户");
    expect(within(edit).getByLabelText("现金余额（可选）")).toHaveValue("88.5");
    await user.click(within(edit).getByRole("button", { name: "保存账户" }));
    await screen.findByText("账户已更新");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === `/api/v1/accounts/${usdAccount.id}` &&
          init?.method === "PATCH",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });

  it("uses inline account archive confirmation and restores trigger focus on cancel", async () => {
    const { fetchMock } = createApiFixture({ accounts: [usdAccount] });
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    const trigger = screen.getByRole("button", {
      name: "归档账户 示例美元账户",
    });

    trigger.focus();
    await user.keyboard("[Enter]");
    const region = screen.getByRole("region", { name: "确认归档账户" });
    await user.click(within(region).getByRole("button", { name: "取消" }));
    expect(trigger).toHaveFocus();
    expect(confirmSpy).not.toHaveBeenCalled();

    await user.click(trigger);
    await user.click(
      within(screen.getByRole("region", { name: "确认归档账户" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );
    expect(await screen.findByText("账户已归档")).toBeVisible();
    const patch = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === `/api/v1/accounts/${usdAccount.id}` &&
        init?.method === "PATCH",
    );
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      version: 1,
      archived: true,
    });
    confirmSpy.mockRestore();
  });

  it("moves an archived account out of active records and restores it explicitly", async () => {
    const { fetchMock } = createApiFixture({ accounts: [usdAccount] });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    const archive = screen.getByRole("button", {
      name: "归档账户 示例美元账户",
    });
    await waitFor(() => expect(archive).toBeEnabled());
    await user.click(archive);
    await user.click(
      within(screen.getByRole("region", { name: "确认归档账户" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );
    await screen.findByText("账户已归档");
    await waitFor(() =>
      expect(screen.queryByText("示例美元账户")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    expect(
      await screen.findByRole("heading", { name: "已归档账户" }),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "恢复账户 示例美元账户" }),
    );
    expect(await screen.findByText("账户已恢复")).toBeVisible();
    expect(await screen.findByText("示例美元账户")).toBeVisible();
    const bodies = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === `/api/v1/accounts/${usdAccount.id}` &&
          init?.method === "PATCH",
      )
      .map(([, init]) => JSON.parse(String(init?.body)) as object);
    expect(bodies).toEqual([
      { version: 1, archived: true },
      { version: 2, archived: false },
    ]);
  });

  it("shows the authoritative version and reload action when account restore conflicts", async () => {
    const archivedAccount = {
      ...usdAccount,
      version: 4,
      archived_at: "2026-07-20T01:00:00Z",
    };
    const { fetchMock } = createApiFixture({
      accounts: [archivedAccount],
      accountPatchErrorCode: "CONCURRENT_MODIFICATION",
      accountPatchErrorFields: { version: 7 },
    });
    const user = userEvent.setup();
    renderHoldings();
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    await user.click(
      await screen.findByRole("button", { name: "恢复账户 示例美元账户" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("记录已在其他位置更新");
    expect(alert).toHaveTextContent("服务器当前版本 7");
    const beforeReload = fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/accounts?archived="),
    ).length;
    await user.click(within(alert).getByRole("button", { name: "重新载入" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).startsWith("/api/v1/accounts?archived="),
        ).length,
      ).toBeGreaterThan(beforeReload),
    );
  });

  it("waits for both account refetches when archive completes archived-first", async () => {
    const activeRefetch = deferred<Response>();
    const archivedRefetch = deferred<Response>();
    let activeReads = 0;
    let archivedReads = 0;
    createApiFixture({
      accounts: [usdAccount],
      accountListHandler: (archived) => {
        if (archived) {
          archivedReads += 1;
          return archivedReads === 1
            ? Promise.resolve(json({ items: [], next_cursor: null }))
            : archivedRefetch.promise;
        }
        activeReads += 1;
        return activeReads === 1
          ? Promise.resolve(json({ items: [usdAccount], next_cursor: null }))
          : activeRefetch.promise;
      },
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    const archive = screen.getByRole("button", {
      name: "归档账户 示例美元账户",
    });
    await waitFor(() => expect(archive).toBeEnabled());
    await user.click(archive);
    await user.click(
      within(screen.getByRole("region", { name: "确认归档账户" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );
    await waitFor(() => {
      expect(activeReads).toBe(2);
      expect(archivedReads).toBe(2);
    });

    archivedRefetch.resolve(
      json({
        items: [
          {
            ...usdAccount,
            version: 2,
            archived_at: "2026-07-20T01:00:00Z",
          },
        ],
        next_cursor: null,
      }),
    );
    await waitFor(() => expect(screen.queryByText("示例美元账户")).toBeNull());
    expect(screen.queryByText(/所属账户不可用/)).not.toBeInTheDocument();

    activeRefetch.resolve(json({ items: [], next_cursor: null }));
    expect(
      await screen.findByRole("button", { name: "恢复账户 示例美元账户" }),
    ).toBeVisible();
    expect(screen.getAllByText("示例美元账户")).toHaveLength(1);
  });

  it("fails closed on stale active account data when its archive refetch fails", async () => {
    let activeReads = 0;
    let archivedReads = 0;
    createApiFixture({
      accounts: [usdAccount],
      accountListHandler: (archived) => {
        if (archived) {
          archivedReads += 1;
          return Promise.resolve(
            json({
              items:
                archivedReads === 1
                  ? []
                  : [
                      {
                        ...usdAccount,
                        version: 2,
                        archived_at: "2026-07-20T01:00:00Z",
                      },
                    ],
              next_cursor: null,
            }),
          );
        }
        activeReads += 1;
        return Promise.resolve(
          activeReads === 1
            ? json({ items: [usdAccount], next_cursor: null })
            : json({ error: { code: "DATABASE_BUSY" } }, 503),
        );
      },
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    const archive = screen.getByRole("button", {
      name: "归档账户 示例美元账户",
    });
    await waitFor(() => expect(archive).toBeEnabled());
    await user.click(archive);
    await user.click(
      within(screen.getByRole("region", { name: "确认归档账户" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );

    expect(await screen.findByText("暂时无法加载账户，请重试")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "编辑账户 示例美元账户" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "恢复账户 示例美元账户" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "重新载入账户" })).toBeVisible();
  });

  it("waits for both account refetches when restore completes active-first", async () => {
    const archivedAccount = {
      ...usdAccount,
      version: 2,
      archived_at: "2026-07-20T01:00:00Z",
    };
    const activeRefetch = deferred<Response>();
    const archivedRefetch = deferred<Response>();
    let activeReads = 0;
    let archivedReads = 0;
    createApiFixture({
      accounts: [archivedAccount],
      accountListHandler: (archived) => {
        if (archived) {
          archivedReads += 1;
          return archivedReads === 1
            ? Promise.resolve(
                json({ items: [archivedAccount], next_cursor: null }),
              )
            : archivedRefetch.promise;
        }
        activeReads += 1;
        return activeReads === 1
          ? Promise.resolve(json({ items: [], next_cursor: null }))
          : activeRefetch.promise;
      },
    });
    const user = userEvent.setup();
    renderHoldings();
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    await user.click(
      await screen.findByRole("button", { name: "恢复账户 示例美元账户" }),
    );
    await waitFor(() => {
      expect(activeReads).toBe(2);
      expect(archivedReads).toBe(2);
    });

    activeRefetch.resolve(
      json({
        items: [{ ...usdAccount, version: 3, archived_at: null }],
        next_cursor: null,
      }),
    );
    await waitFor(() => expect(screen.queryByText("示例美元账户")).toBeNull());
    expect(screen.queryByText(/所属账户不可用/)).not.toBeInTheDocument();

    archivedRefetch.resolve(json({ items: [], next_cursor: null }));
    expect(
      await screen.findByRole("button", { name: "编辑账户 示例美元账户" }),
    ).toBeVisible();
    expect(screen.getAllByText("示例美元账户")).toHaveLength(1);
  });

  it("disables account archive while an active position exists", async () => {
    createApiFixture({ accounts: [usdAccount], positions: [activePosition] });
    renderHoldings();

    const archive = await screen.findByRole("button", {
      name: "归档账户 示例美元账户",
    });
    expect(archive).toBeDisabled();
    expect(screen.getByText("请先归档该账户的全部持仓")).toBeVisible();
  });

  it("announces active and archived position loading and empty states without empty lists", async () => {
    const archivedPage = deferred<Response>();
    createApiFixture({
      accounts: [usdAccount],
      positionListHandler: (archived) =>
        archived
          ? archivedPage.promise
          : Promise.resolve(json({ items: [], next_cursor: null })),
    });
    const user = userEvent.setup();
    renderHoldings();

    expect(screen.getByText("正在加载当前持仓…")).toHaveAttribute(
      "role",
      "status",
    );
    expect(await screen.findByText("暂无当前持仓")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(
      screen.queryByRole("list", { name: "当前持仓" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));
    expect(screen.getByText("正在加载已归档项目…")).toHaveAttribute(
      "role",
      "status",
    );
    archivedPage.resolve(json({ items: [], next_cursor: null }));
    expect(await screen.findByText("没有已归档账户或持仓")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(
      screen.queryByRole("list", { name: "已归档项目" }),
    ).not.toBeInTheDocument();
  });

  it("does not render a position list before its account join is available", async () => {
    const activeAccounts = deferred<Response>();
    createApiFixture({
      positions: [activePosition],
      accountListHandler: (archived) =>
        archived
          ? Promise.resolve(json({ items: [], next_cursor: null }))
          : activeAccounts.promise,
    });
    renderHoldings();

    expect(
      screen.queryByRole("list", { name: "当前持仓" }),
    ).not.toBeInTheDocument();
    activeAccounts.resolve(json({ items: [usdAccount], next_cursor: null }));
    expect(await screen.findByRole("list", { name: "当前持仓" })).toBeVisible();
  });

  it("does not render archived positions before both account sets are available", async () => {
    const archivedAccounts = deferred<Response>();
    const archivedPosition = {
      ...activePosition,
      archived_at: "2026-07-20T01:00:00Z",
    };
    createApiFixture({
      accounts: [usdAccount],
      positions: [archivedPosition],
      accountListHandler: (archived) =>
        archived
          ? archivedAccounts.promise
          : Promise.resolve(json({ items: [usdAccount], next_cursor: null })),
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("示例美元账户");
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));

    expect(screen.getByText("正在加载已归档项目…")).toBeVisible();
    expect(screen.queryByText("已归档持仓")).not.toBeInTheDocument();
    archivedAccounts.resolve(json({ items: [], next_cursor: null }));
    expect(await screen.findByText("已归档持仓")).toBeVisible();
  });

  it("loads later position pages before allowing account archive", async () => {
    createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      positionOnLaterPage: true,
    });
    renderHoldings();

    const archive = await screen.findByRole("button", {
      name: "归档账户 示例美元账户",
    });
    await waitFor(() => expect(archive).toBeDisabled());
    expect(screen.getByText("请先归档该账户的全部持仓")).toBeVisible();
  });

  it("explains an account active-position race and reloads positions", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      accountPatchErrorCode: "ACCOUNT_HAS_ACTIVE_POSITIONS",
    });
    const user = userEvent.setup();
    renderHoldings();
    const trigger = await screen.findByRole("button", {
      name: "归档账户 示例美元账户",
    });
    await waitFor(() => expect(trigger).toBeEnabled());
    await user.click(trigger);
    const before = fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/positions?archived=false"),
    ).length;
    await user.click(
      within(screen.getByRole("region", { name: "确认归档账户" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("账户已有当前持仓，无法归档");
    await user.click(
      within(alert).getByRole("button", { name: "重新载入持仓" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).startsWith("/api/v1/positions?archived=false"),
        ).length,
      ).toBeGreaterThan(before),
    );
  });

  it("shows a retryable state when active positions cannot be loaded", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positionGetFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法加载持仓，请重试");
    expect(alert).not.toHaveTextContent("private positions load detail");
    expect(
      screen.getByRole("button", { name: "归档账户 示例美元账户" }),
    ).toBeDisabled();
    await user.click(
      within(alert).getByRole("button", { name: "重新载入持仓" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([input]) => String(input) === "/api/v1/positions?archived=false",
        ),
      ).toHaveLength(2),
    );
  });

  it("archives and restores a position from the archived-items view", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");

    await user.click(
      screen.getByRole("button", {
        name: "归档持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    await user.click(
      within(screen.getByRole("region", { name: "确认归档持仓" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );
    expect(await screen.findByText("持仓已归档")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "查看已归档项目" }));

    expect(await screen.findByText("已归档持仓")).toBeVisible();
    await user.click(
      screen.getByRole("button", {
        name: "恢复持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    expect(await screen.findByText("持仓已恢复")).toBeVisible();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "恢复持仓 示例科技 TEST.US 示例美元账户",
        }),
      ).not.toBeInTheDocument(),
    );
    const patchBodies = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === `/api/v1/positions/${activePosition.id}` &&
          init?.method === "PATCH",
      )
      .map(([, init]) => JSON.parse(String(init?.body)) as object);
    expect(patchBodies).toEqual([
      { version: 1, archived: true },
      { version: 2, archived: false },
    ]);
  });

  it("retains the archive idempotency key across a surfaced network retry", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      positionPatchFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");
    await user.click(
      screen.getByRole("button", {
        name: "归档持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    const region = screen.getByRole("region", { name: "确认归档持仓" });
    await user.click(within(region).getByRole("button", { name: "确认归档" }));
    await screen.findByText("暂时无法归档持仓，请重试");
    await user.click(within(region).getByRole("button", { name: "确认归档" }));
    await screen.findByText("持仓已归档");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === `/api/v1/positions/${activePosition.id}` &&
          init?.method === "PATCH",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });

  it("offers authoritative reload when position archive conflicts", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      conflictPositionPatch: true,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");
    await user.click(
      screen.getByRole("button", {
        name: "归档持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    await user.click(
      within(screen.getByRole("region", { name: "确认归档持仓" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("记录已在其他位置更新");
    expect(alert).toHaveTextContent("服务器当前版本 2");
    const beforeReload = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/v1/positions?archived=false",
    ).length;
    await user.click(within(alert).getByRole("button", { name: "重新载入" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([input]) => String(input) === "/api/v1/positions?archived=false",
        ).length,
      ).toBeGreaterThan(beforeReload),
    );
  });

  it("reloads positions when an archive is already complete elsewhere", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      positionPatchErrorCode: "POSITION_ARCHIVED",
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");
    await user.click(
      screen.getByRole("button", {
        name: "归档持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    await user.click(
      within(screen.getByRole("region", { name: "确认归档持仓" })).getByRole(
        "button",
        { name: "确认归档" },
      ),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("持仓已在其他位置归档");
    const before = fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/positions?archived=false"),
    ).length;
    await user.click(
      within(alert).getByRole("button", { name: "重新载入持仓" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          String(input).startsWith("/api/v1/positions?archived=false"),
        ).length,
      ).toBeGreaterThan(before),
    );
    expect(
      screen.queryByRole("region", { name: "确认归档持仓" }),
    ).not.toBeInTheDocument();
  });

  it("shows a sanitized 409 reload prompt with the authoritative version", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      conflictPositionPatch: true,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");
    await user.click(
      screen.getByRole("button", {
        name: "编辑持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    const edit = screen.getByRole("form", {
      name: "编辑持仓 示例科技 TEST.US 示例美元账户",
    });
    await user.clear(within(edit).getByLabelText("持仓数量"));
    await user.type(within(edit).getByLabelText("持仓数量"), "3");
    await user.click(within(edit).getByRole("button", { name: "保存持仓" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("记录已在其他位置更新");
    expect(alert).toHaveTextContent("服务器当前版本 2");
    expect(alert).not.toHaveTextContent("private");
    const beforeReload = fetchMock.mock.calls.filter(
      ([input]) => String(input) === "/api/v1/positions?archived=false",
    ).length;
    await user.click(within(alert).getByRole("button", { name: "重新载入" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([input]) => String(input) === "/api/v1/positions?archived=false",
        ).length,
      ).toBeGreaterThan(beforeReload),
    );
  });

  it("retains position edit values and key across a surfaced retry", async () => {
    const { fetchMock } = createApiFixture({
      accounts: [usdAccount],
      positions: [activePosition],
      positionPatchFailures: 1,
    });
    const user = userEvent.setup();
    renderHoldings();
    await screen.findByText("数量 2.5");
    await user.click(
      screen.getByRole("button", {
        name: "编辑持仓 示例科技 TEST.US 示例美元账户",
      }),
    );
    const edit = screen.getByRole("form", {
      name: "编辑持仓 示例科技 TEST.US 示例美元账户",
    });
    await user.clear(within(edit).getByLabelText("持仓数量"));
    await user.type(within(edit).getByLabelText("持仓数量"), "4.5");
    await user.clear(within(edit).getByLabelText("平均成本"));
    await user.type(within(edit).getByLabelText("平均成本"), "7.25");
    await user.click(within(edit).getByRole("button", { name: "保存持仓" }));
    await screen.findByText("暂时无法保存持仓，请重试");
    expect(within(edit).getByLabelText("持仓数量")).toHaveValue("4.5");
    expect(within(edit).getByLabelText("平均成本")).toHaveValue("7.25");
    await user.click(within(edit).getByRole("button", { name: "保存持仓" }));
    await screen.findByText("持仓已更新");

    const keys = fetchMock.mock.calls
      .filter(
        ([input, init]) =>
          String(input) === `/api/v1/positions/${activePosition.id}` &&
          init?.method === "PATCH",
      )
      .map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });
});
