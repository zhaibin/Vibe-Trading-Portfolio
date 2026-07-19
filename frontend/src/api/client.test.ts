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

  it("retains the caller's idempotency key for JSON writes", async () => {
    const fetchMock = mockFetch(
      Response.json({ id: "account-1" }, { status: 201 }),
    );
    const idempotencyKey = "account-create-123";

    await api.post(
      "/api/v1/accounts",
      { name: "测试账户", currency: "CNY" },
      idempotencyKey,
    );

    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers);
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ name: "测试账户", currency: "CNY" }),
    );
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe(idempotencyKey);
  });

  it("sends PATCH requests with the same write contract", async () => {
    const fetchMock = mockFetch(Response.json({ version: 2 }));

    await api.patch(
      "/api/v1/accounts/account-1",
      { version: 1 },
      "account-patch-123",
    );

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.method).toBe("PATCH");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "account-patch-123",
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
