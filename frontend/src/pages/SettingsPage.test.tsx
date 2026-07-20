import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "./SettingsPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderSettings() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return render(<SettingsPage />, { wrapper: Wrapper });
}

describe("SettingsPage", () => {
  it("shows only redacted local status and disabled provider names", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json({
        schema_revision: "20260719_0006",
        migration_healthy: true,
        database_path: "var/data/portfolio.db",
        backup_directory: "var/data",
        latest_backup_at: "2026-07-20T07:00:00Z",
        adapters: [
          { name: "eastmoney", enabled: true },
          { name: "yahoo", enabled: false },
          { name: "tencent", enabled: true },
        ],
        last_successful_refresh_at: "2026-07-20T08:00:00Z",
        latest_quote_count: 2,
        candidate_cache_count: 3,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderSettings();

    expect(await screen.findByText("var/data/portfolio.db")).toBeVisible();
    expect(screen.getByText("架构版本 20260719_0006")).toBeVisible();
    expect(screen.getByText("迁移健康")).toBeVisible();
    expect(screen.getByText("yahoo：已禁用")).toBeVisible();
    expect(screen.getByText("eastmoney：已启用")).toBeVisible();
    expect(screen.getByText("最新报价缓存 2 项")).toBeVisible();
    expect(screen.getByText("候选缓存 3 项")).toBeVisible();
    expect(screen.getByText("var/data")).toBeVisible();
    const backupTime = document.querySelector(
      'time[datetime="2026-07-20T07:00:00Z"]',
    );
    expect(backupTime).toHaveAttribute("datetime", "2026-07-20T07:00:00Z");
    expect(backupTime).not.toHaveTextContent("T");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/settings/status",
      expect.objectContaining({
        method: "GET",
        signal: expect.any(AbortSignal),
      }),
    );
    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const forbidden of [
      "http://",
      "https://",
      "token",
      "secret",
      "/users/",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
