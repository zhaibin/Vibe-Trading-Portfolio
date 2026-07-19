import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsQuery, positionsQuery } from "./queries";

afterEach(() => {
  vi.unstubAllGlobals();
});

function page(items: object[], nextCursor: string | null) {
  return Response.json({ items, next_cursor: nextCursor });
}

describe("holdings query pagination", () => {
  it("loads every archived account page with the archived filter intact", async () => {
    const first = { id: "account-1" };
    const second = { id: "account-2" };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page([first], "next-account"))
      .mockResolvedValueOnce(page([second], null));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new QueryClient().fetchQuery(accountsQuery(true));

    expect(result).toEqual({ items: [first, second], next_cursor: null });
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/accounts?archived=true",
      "/api/v1/accounts?archived=true&cursor=next-account",
    ]);
  });

  it("rejects a repeated accounts cursor", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page([], "repeated"))
      .mockResolvedValueOnce(page([], "repeated"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new QueryClient().fetchQuery(accountsQuery(false)),
    ).rejects.toThrow("Repeated accounts cursor");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/accounts?archived=false",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a repeated positions cursor", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page([], "repeated"))
      .mockResolvedValueOnce(page([], "repeated"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new QueryClient().fetchQuery(positionsQuery(false)),
    ).rejects.toThrow("Repeated positions cursor");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/positions?archived=false",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
