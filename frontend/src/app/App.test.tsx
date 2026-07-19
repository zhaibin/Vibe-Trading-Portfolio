import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  App,
  GlobalErrorBoundary,
  browserRouter,
  routes,
  shouldRetry,
} from "./App";

function ThrowingChild(): never {
  throw new Error("synthetic render failure");
}

describe("App", () => {
  it("shows Chinese navigation and owns all three routes", async () => {
    await browserRouter.navigate("/");
    render(<App />);
    const user = userEvent.setup();

    expect(screen.getByRole("link", { name: "总览" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "持仓" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(
      screen.getByRole("heading", { name: "投资组合总览" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "持仓" }));
    expect(
      screen.getByRole("heading", { name: "持仓管理" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "设置" }));
    expect(
      screen.getByRole("heading", { name: "设置与状态" }),
    ).toBeInTheDocument();
  });

  it("does not retry 4xx API errors", () => {
    expect(shouldRetry(0, new ApiError(422, "VALIDATION_ERROR"))).toBe(false);
    expect(shouldRetry(0, new ApiError(503, "DATABASE_UNAVAILABLE"))).toBe(
      true,
    );
    expect(shouldRetry(2, new Error("network"))).toBe(false);
  });

  it("renders a global fallback without logging the error payload", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    render(
      <GlobalErrorBoundary>
        <ThrowingChild />
      </GlobalErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("页面暂时无法显示");
    consoleError.mockRestore();
  });

  it("sanitizes errors thrown while rendering a route", () => {
    const rootRoute = routes[0];
    if (rootRoute === undefined) {
      throw new Error("root route is missing");
    }
    const router = createMemoryRouter([
      {
        ...rootRoute,
        index: false,
        children: [{ index: true, element: <ThrowingChild /> }],
      },
    ]);
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    render(<RouterProvider router={router} />);

    expect(screen.getByRole("alert")).toHaveTextContent("页面暂时无法显示");
    expect(
      screen.queryByText("synthetic render failure"),
    ).not.toBeInTheDocument();
    consoleError.mockRestore();
  });
});
