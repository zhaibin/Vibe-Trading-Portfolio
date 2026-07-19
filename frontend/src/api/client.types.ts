import { api } from "./client";

async function assertGeneratedClientTypes() {
  const key = "compile-time-key";
  const account = await api.post(
    "/api/v1/accounts",
    { name: "测试账户", currency: "CNY" },
    key,
  );
  account.version satisfies number;

  const accounts = await api.get("/api/v1/accounts?limit=10");
  accounts.items satisfies Array<{ id: string; version: number }>;

  // @ts-expect-error AccountCreate requires a supported currency.
  await api.post("/api/v1/accounts", { name: "缺少币种" }, key);

  // @ts-expect-error AccountView has no invented response property.
  void account.invented;
}

void assertGeneratedClientTypes;
