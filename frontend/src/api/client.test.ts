import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, newIdempotencyKey } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(response: Response) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

interface RuntimeOptions {
  params?: {
    path?: Record<string, unknown>;
    query?: Record<string, unknown>;
  };
  signal?: AbortSignal;
}

type RuntimeGet = (path: string, options?: RuntimeOptions) => Promise<unknown>;
describe("api client", () => {
  it("uses same-origin credentials and JSON acceptance for reads", async () => {
    const fetchMock = mockFetch(Response.json({ status: "ok" }));

    await expect(api.get("/api/v1/system/status")).resolves.toEqual({
      status: "ok",
    });

    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe("/api/v1/system/status");
    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe("same-origin");
    expect(new Headers(init?.headers).get("Accept")).toBe("application/json");
  });

  it("serializes structured path parameters with percent encoding", async () => {
    const fetchMock = mockFetch(Response.json({ run_id: "run/one" }));
    const get = api.get as RuntimeGet;

    await get("/api/v1/market-data/refresh/{run_id}", {
      params: { path: { run_id: "run/one" } },
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/market-data/refresh/run%2Fone",
    );
  });

  it("serializes generated query parameters with URLSearchParams", async () => {
    const fetchMock = mockFetch(Response.json([]));
    const get = api.get as RuntimeGet;

    await get("/api/v1/instruments/search", {
      params: { query: { q: "腾讯 / US", limit: 5 } },
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/instruments/search?q=%E8%85%BE%E8%AE%AF+%2F+US&limit=5",
    );
  });

  it("serializes query arrays as repeated OpenAPI form values", async () => {
    const fetchMock = mockFetch(Response.json({ items: [] }));
    const get = api.get as RuntimeGet;

    await get("/api/v1/accounts", {
      params: { query: { cursor: ["first", "second"] } },
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/accounts?cursor=first&cursor=second",
    );
  });

  it("rejects missing or empty required path parameters before fetch", async () => {
    const fetchMock = mockFetch(Response.json({}));
    const get = api.get as RuntimeGet;

    await expect(
      get("/api/v1/market-data/refresh/{run_id}", {
        params: { path: {} },
      }),
    ).rejects.toThrow("Missing path parameter: run_id");
    await expect(
      get("/api/v1/market-data/refresh/{run_id}", {
        params: { path: { run_id: "" } },
      }),
    ).rejects.toThrow("Missing path parameter: run_id");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes the exact AbortSignal through a read", async () => {
    const fetchMock = mockFetch(Response.json({ status: "ok" }));
    const controller = new AbortController();
    const get = api.get as RuntimeGet;

    await get("/api/v1/system/status", { signal: controller.signal });

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("retains the caller's idempotency key for JSON writes", async () => {
    const fetchMock = mockFetch(
      Response.json({ id: "account-1" }, { status: 201 }),
    );
    const idempotencyKey = "account-create-123";
    const controller = new AbortController();

    await api.post(
      "/api/v1/accounts",
      { name: "测试账户", currency: "CNY" },
      idempotencyKey,
      { signal: controller.signal },
    );

    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers);
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ name: "测试账户", currency: "CNY" }),
    );
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe(idempotencyKey);
    expect(init?.signal).toBe(controller.signal);
  });

  it("sends PATCH requests with the same write contract", async () => {
    const fetchMock = mockFetch(Response.json({ version: 2 }));

    await api.patch(
      "/api/v1/accounts/{account_id}",
      { version: 1 },
      "account-patch-123",
      { params: { path: { account_id: "account-1" } } },
    );

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.method).toBe("PATCH");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "account-patch-123",
    );
  });

  it("retains write headers while passing params and AbortSignal", async () => {
    const fetchMock = mockFetch(Response.json({ version: 2 }));
    const controller = new AbortController();

    await api.patch(
      "/api/v1/accounts/{account_id}",
      { version: 1 },
      "account-patch-signal",
      {
        params: { path: { account_id: "account/one" } },
        signal: controller.signal,
      },
    );

    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe("/api/v1/accounts/account%2Fone");
    expect(init?.signal).toBe(controller.signal);
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "account-patch-signal",
    );
  });

  it("parses the stable API error envelope", async () => {
    mockFetch(
      Response.json(
        { error: { code: "VALIDATION_ERROR", fields: { name: "required" } } },
        { status: 422 },
      ),
    );

    const error = await api
      .get("/api/v1/accounts")
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR",
      fields: { name: "required" },
    });
  });

  it("fails predictably for a malformed error response", async () => {
    mockFetch(new Response("upstream failed", { status: 502 }));

    await expect(api.get("/api/v1/accounts")).rejects.toMatchObject({
      status: 502,
      code: "HTTP_ERROR",
    });
  });

  it("falls back when an error JSON document is not the stable envelope", async () => {
    mockFetch(Response.json({ detail: "Bad Gateway" }, { status: 502 }));

    await expect(api.get("/api/v1/accounts")).rejects.toMatchObject({
      status: 502,
      code: "HTTP_ERROR",
    });
  });

  it("accepts an empty successful response", async () => {
    mockFetch(new Response(null, { status: 204 }));

    await expect(
      api.post(
        "/api/v1/system/compatibility/mcp-probe",
        undefined,
        "probe-key-123",
      ),
    ).resolves.toBe(undefined);
  });

  it("rejects a malformed successful response", async () => {
    mockFetch(new Response("not-json", { status: 200 }));

    await expect(api.get("/api/v1/accounts")).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
    });
  });

  it("creates valid and distinct idempotency keys", () => {
    const first = newIdempotencyKey();
    const second = newIdempotencyKey();

    expect(first).toMatch(/^portfolio-[0-9a-f-]{36}$/);
    expect(second).not.toBe(first);
  });
});
