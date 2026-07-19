import { api } from "./client";

async function assertGeneratedClientTypes() {
  const key = "compile-time-key";
  const account = await api.post(
    "/api/v1/accounts",
    { name: "测试账户", currency: "CNY" },
    key,
  );
  account.version satisfies number;

  const accounts = await api.get("/api/v1/accounts", {
    params: { query: { limit: 10 } },
  });
  accounts.items satisfies Array<{ id: string; version: number }>;

  const summary = await api.get("/api/v1/portfolio/summary", {
    params: { query: { currency: "USD" } },
    signal: new AbortController().signal,
  });
  summary.currency satisfies "CNY" | "HKD" | "USD";

  const run = await api.get("/api/v1/market-data/refresh/{run_id}", {
    params: { path: { run_id: "run-id" } },
  });
  run.run_id satisfies string;

  await api.patch(
    "/api/v1/accounts/{account_id}",
    { version: 1, name: "新名称" },
    key,
    { params: { path: { account_id: "account-id" } } },
  );

  // @ts-expect-error AccountCreate requires a supported currency.
  await api.post("/api/v1/accounts", { name: "缺少币种" }, key);

  // @ts-expect-error AccountView has no invented response property.
  void account.invented;

  // @ts-expect-error Query strings must be structured, not embedded in the path.
  await api.get("/api/v1/accounts?limit=10");

  // @ts-expect-error Portfolio summary requires the currency query parameter.
  await api.get("/api/v1/portfolio/summary");

  await api.get("/api/v1/portfolio/summary", {
    // @ts-expect-error Currency is constrained by the generated OpenAPI enum.
    params: { query: { currency: "EUR" } },
  });

  // @ts-expect-error Unknown query keys are rejected.
  await api.get("/api/v1/accounts", { params: { query: { page: 2 } } });

  // @ts-expect-error A path-bearing operation requires structured path parameters.
  await api.get("/api/v1/market-data/refresh/{run_id}");

  await api.get("/api/v1/market-data/refresh/{run_id}", {
    // @ts-expect-error Unknown path keys are rejected.
    params: { path: { id: "run-id" } },
  });

  await api.patch(
    // @ts-expect-error Expanded mutation paths are not part of the public API.
    "/api/v1/accounts/account-id",
    { version: 1 },
    key,
    { params: { path: { account_id: "account-id" } } },
  );
}

void assertGeneratedClientTypes;
