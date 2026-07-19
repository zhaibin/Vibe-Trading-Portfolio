import { queryOptions } from "@tanstack/react-query";
import type { components } from "./schema";

import { api } from "./client";

export type Account = components["schemas"]["AccountView"];
export type Currency = components["schemas"]["Currency"];
export type Instrument = components["schemas"]["InstrumentView"];
export type InstrumentCandidate = components["schemas"]["InstrumentSearchView"];
export type Position = components["schemas"]["PositionView"];

export const holdingsKeys = {
  accounts: ["accounts"] as const,
  positions: (archived: boolean) => ["positions", { archived }] as const,
  summary: (currency: Currency) => ["summary", currency] as const,
};

export function accountsQuery() {
  return queryOptions({
    queryKey: holdingsKeys.accounts,
    queryFn: async ({ signal }) => {
      let page = await api.get("/api/v1/accounts", { signal });
      const items = [...page.items];
      const cursors = new Set<string>();
      while (page.next_cursor !== null) {
        if (cursors.has(page.next_cursor)) {
          throw new Error("Repeated accounts cursor");
        }
        cursors.add(page.next_cursor);
        page = await api.get("/api/v1/accounts", {
          params: { query: { cursor: page.next_cursor } },
          signal,
        });
        items.push(...page.items);
      }
      return { items, next_cursor: null };
    },
  });
}

export function positionsQuery(archived: boolean) {
  return queryOptions({
    queryKey: holdingsKeys.positions(archived),
    queryFn: async ({ signal }) => {
      let page = await api.get("/api/v1/positions", {
        params: { query: { archived } },
        signal,
      });
      const items = [...page.items];
      const cursors = new Set<string>();
      while (page.next_cursor !== null) {
        if (cursors.has(page.next_cursor)) {
          throw new Error("Repeated positions cursor");
        }
        cursors.add(page.next_cursor);
        page = await api.get("/api/v1/positions", {
          params: { query: { archived, cursor: page.next_cursor } },
          signal,
        });
        items.push(...page.items);
      }
      return { items, next_cursor: null };
    },
  });
}
