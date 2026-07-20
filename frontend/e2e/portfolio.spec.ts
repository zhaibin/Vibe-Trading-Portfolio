import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function assertAccessible(page: Page, path: string) {
  await page.goto(path);
  await expect(page.locator("main")).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter(
      ({ impact }) => impact === "serious" || impact === "critical",
    ),
  ).toEqual([]);
}

async function createAccount(
  page: Page,
  name: string,
  currency: "CNY" | "HKD" | "USD",
  cash: string,
) {
  await page.getByLabel("账户名称").fill(name);
  await page.getByLabel("账户币种").selectOption(currency);
  await page.getByLabel("现金余额（可选）").fill(cash);
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("账户已创建");
  await expect(page.getByText(name, { exact: true })).toBeVisible();
  await expect(page.getByLabel("账户名称")).toHaveValue("");
}

async function createPosition(
  page: Page,
  account: string,
  symbol: string,
  name: string,
  quantity: string,
  cost: string,
) {
  const accountSelect = page.getByLabel("所属账户");
  const accountId = await accountSelect
    .getByRole("option", { name: new RegExp(account) })
    .getAttribute("value");
  expect(accountId).not.toBeNull();
  await accountSelect.selectOption(accountId!);
  await page.getByLabel("证券代码或名称").fill(symbol);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("button", { name: `确认 ${name} ${symbol}` }).click();
  await expect(
    page.getByText(new RegExp(`已确认：${name} ${symbol}`)),
  ).toBeVisible();
  await page.getByLabel("持仓数量", { exact: true }).fill(quantity);
  await page.getByLabel("平均成本", { exact: true }).fill(cost);
  await page.getByRole("button", { name: "添加持仓" }).click();
  await expect(page.getByRole("status")).toContainText("持仓已创建");
}

test.describe.configure({ mode: "serial" });

test("@phase1 builds a portfolio and exercises explicit refresh", async ({
  page,
}) => {
  const refreshRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/market-data/refresh")) {
      refreshRequests.push(request.method());
    }
  });

  await page.goto("/holdings");
  await createAccount(page, "人民币账户", "CNY", "91001.11");
  await createAccount(page, "港币账户", "HKD", "92002.22");
  await createAccount(page, "美元账户", "USD", "93003.33");
  await createPosition(
    page,
    "人民币账户",
    "600000.SH",
    "浦发银行",
    "17.12345678",
    "8101.01",
  );
  await createPosition(
    page,
    "港币账户",
    "00700.HK",
    "腾讯控股",
    "27.23456789",
    "8202.02",
  );
  await createPosition(
    page,
    "美元账户",
    "DEMO.US",
    "Demo Corp",
    "37.34567891",
    "8303.03",
  );

  await page.getByRole("link", { name: "总览" }).click();
  await expect(
    page.getByRole("heading", { name: "投资组合总览" }),
  ).toBeVisible();
  expect(refreshRequests).toEqual([]);
  await page.getByRole("button", { name: "刷新行情" }).click();
  await expect(page.getByRole("status")).toContainText("行情刷新部分完成");
  await expect(page.getByRole("tab", { name: "CNY" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByText("91172.344568 CNY").first()).toBeVisible();
  await page.getByRole("tab", { name: "HKD" }).click();
  await expect(page.getByText("102896.047156 HKD").first()).toBeVisible();
  await page.getByRole("tab", { name: "USD" }).click();
  await expect(page.getByText("93003.330000 USD").first()).toBeVisible();
  await expect(page.getByText("不可用", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "刷新行情" }).click();
  await expect(page.getByRole("status")).toContainText("行情刷新部分完成");
  await page.getByRole("tab", { name: "CNY" }).click();
  await expect(page.getByText("陈旧", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "持仓" }).click();
  await page.getByRole("button", { name: /编辑持仓 浦发银行/ }).click();
  const edit = page.getByRole("form", { name: /编辑持仓 浦发银行/ });
  await edit.getByLabel("持仓数量").fill("18.12345678");
  await edit.getByRole("button", { name: "保存持仓" }).click();
  await expect(page.getByRole("status")).toContainText("持仓已更新");
  await page.getByRole("button", { name: /归档持仓 浦发银行/ }).click();
  await page
    .getByRole("region", { name: "确认归档持仓" })
    .getByRole("button", { name: "确认归档" })
    .click();
  await expect(page.getByRole("status")).toContainText("持仓已归档");
  await page.reload();
  await expect(page.getByText("人民币账户", { exact: true })).toBeVisible();

  await assertAccessible(page, "/");
  await assertAccessible(page, "/holdings");
  await assertAccessible(page, "/settings");

  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳到主要内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  const cnyTab = page.getByRole("tab", { name: "CNY" });
  await cnyTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "HKD" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.setViewportSize({ width: 640, height: 900 });
  await page.evaluate(() => {
    document.documentElement.style.zoom = "2";
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("@phase2 persists across restart, keeps API 404 JSON, and recovers a conflict", async ({
  browser,
  page,
  request,
}) => {
  await page.goto("/holdings");
  await expect(page.getByText("人民币账户", { exact: true })).toBeVisible();
  await expect(
    page.getByText("现金余额 92002.22", { exact: true }),
  ).toBeVisible();
  const activeDemo = page
    .getByRole("listitem")
    .filter({ hasText: "Demo Corp DEMO.US" });
  await expect(activeDemo).toContainText("数量 37.34567891");
  await expect(activeDemo).toContainText("平均成本 8303.03 USD");
  await page.getByRole("button", { name: "查看已归档项目" }).click();
  const archivedPufa = page
    .getByRole("listitem")
    .filter({ hasText: "浦发银行 600000.SH" });
  await expect(archivedPufa).toContainText("数量 18.12345678");
  await expect(archivedPufa).toContainText("平均成本 8101.01 CNY");

  const missing = await request.get("/api/v1/does-not-exist");
  expect(missing.status()).toBe(404);
  expect(missing.headers()["content-type"]).toContain("application/json");
  expect(await missing.json()).toEqual({ detail: "Not Found" });

  const second = await browser.newPage();
  await second.goto("/holdings");
  await page.getByRole("button", { name: "编辑账户 人民币账户" }).click();
  await second.getByRole("button", { name: "编辑账户 人民币账户" }).click();
  await page
    .getByRole("form", { name: "编辑账户 人民币账户" })
    .getByLabel("现金余额（可选）")
    .fill("91011.11");
  await page
    .getByRole("form", { name: "编辑账户 人民币账户" })
    .getByRole("button", { name: "保存账户" })
    .click();
  await expect(page.getByRole("status")).toContainText("账户已更新");
  await second
    .getByRole("form", { name: "编辑账户 人民币账户" })
    .getByLabel("现金余额（可选）")
    .fill("91022.22");
  await second
    .getByRole("form", { name: "编辑账户 人民币账户" })
    .getByRole("button", { name: "保存账户" })
    .click();
  await expect(second.getByRole("alert")).toContainText("记录已在其他位置更新");
  await second.getByRole("button", { name: "重新载入" }).click();
  await expect(second.getByRole("status")).toContainText("已重新载入最新记录");
  await expect(second.getByText("现金余额 91011.11")).toBeVisible();
  await second.close();
});
