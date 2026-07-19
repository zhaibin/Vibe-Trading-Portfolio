import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ReactNode } from "react";
import {
  NavLink,
  Outlet,
  RouterProvider,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom";

import { ApiError } from "../api/client";
import { HoldingsPage } from "../pages/HoldingsPage";
import { OverviewPage } from "../pages/OverviewPage";
import { SettingsPage } from "../pages/SettingsPage";
import "./styles.css";

export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 2;
}

export function createPortfolioQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry },
      mutations: { retry: shouldRetry },
    },
  });
}

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  failed: boolean;
}

function SanitizedErrorPage() {
  return (
    <main className="fatal-error" role="alert">
      <h1>页面暂时无法显示</h1>
      <p>请刷新页面后重试。</p>
    </main>
  );
}

export class GlobalErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <SanitizedErrorPage />;
    }
    return this.props.children;
  }
}

function AppLayout() {
  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <header className="app-header">
        <p className="app-name">个人投资组合</p>
        <nav aria-label="主导航">
          <NavLink end to="/">
            总览
          </NavLink>
          <NavLink to="/holdings">持仓</NavLink>
          <NavLink to="/settings">设置</NavLink>
        </nav>
      </header>
      <main id="main-content" className="page-content" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    errorElement: <SanitizedErrorPage />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "holdings", element: <HoldingsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
];

export const queryClient = createPortfolioQueryClient();
export const browserRouter = createBrowserRouter(routes);

export function App() {
  return (
    <GlobalErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={browserRouter} />
      </QueryClientProvider>
    </GlobalErrorBoundary>
  );
}
