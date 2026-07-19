# Portfolio Experience WebUI and Independent Market Data Design

**Date:** 2026-07-19

**Status:** Design content approved by the user; written specification and audit awaiting final review

**Scope:** Experience Milestone 1A — local holdings snapshot, independent quotes, and a user-facing WebUI

## 1. Decision

The next product milestone turns the Sidecar into a usable personal-portfolio experience. It adds a Simplified Chinese WebUI, local accounts and current-position snapshots, and Sidecar-owned security search and quote refresh. The portfolio experience must work when Vibe-Trading is stopped and must not obtain quotes through Vibe-Trading.

This milestone is deliberately narrower than the formal MVP in [the umbrella product design](2026-07-18-personal-portfolio-sidecar-design.md). It is an experience milestone, not a declaration that the immutable event ledger, CSV import, reconciliation, FX conversion, research, reports, backup/restore UI, or formal MVP release gates are complete.

The approved implementation shape is:

```text
Browser
  -> React + TypeScript SPA
  -> same-origin FastAPI /api/v1
      -> portfolio application services
          -> SQLite accounts and position snapshots
          -> independent market-data provider adapters

FastAPI compatibility routes
  -> existing VibeGateway boundary
  -> Vibe public REST/OpenAPI/SSE and operator-installed MCP only
```

The market-data path and the Vibe compatibility path are separate. Neither may import the other's private implementation or use the other's storage.

## 2. Goals

- Let a person open the Sidecar and immediately understand what it is for.
- Support multiple CNY, HKD, and USD accounts.
- Support manually maintained long positions in A-shares, Hong Kong stocks, US stocks, and ETFs.
- Search instruments by code or name and require confirmation before adding a position.
- Refresh latest available or delayed prices only when the user explicitly requests it.
- Preserve the last valid price when a provider fails and make stale or missing valuation visible.
- Show valuation, cost, unrealized profit/loss, cash, allocation, freshness, and unvalued positions separately for each currency.
- Store all portfolio data locally with exact decimal semantics and forward-only schema migrations.
- Preserve all existing compatibility, MCP, fail-closed, and no-trading boundaries.
- Ship one production process on `127.0.0.1:8765` that serves the built SPA and API from the same origin.

## 3. Non-goals

- Transactions, tax lots, realized profit/loss, dividends, fees, transfers, corporate actions, or an immutable accounting ledger.
- CSV or broker import, reconciliation, or broker synchronization.
- Cross-currency totals, FX conversion, or a consolidated CNY view.
- Historical performance charts or return calculations.
- Streaming, realtime, automatic, scheduled, or background quote refresh.
- Vibe-powered analysis, recommendations, research reports, or an operations console in this milestone.
- Short positions, options, futures, leverage, margin, cryptocurrency, private funds, or unsupported instruments.
- Order entry, broker writes, trade execution, or UI language that implies those capabilities.
- LAN or internet exposure, multiple users, roles, or remote authentication.
- Treating public provider endpoints as a guaranteed long-term contract.

## 4. Relationship to the Umbrella Product Design

The umbrella design remains authoritative for the final product. This document defines a staged subset and records four intentional deviations:

| Topic | Umbrella MVP | Experience Milestone 1A | Resolution |
|---|---|---|---|
| Holdings source of truth | Immutable event ledger | Editable current-position snapshot | Milestone 1A tables are provisional input state; they do not masquerade as ledger events. A later migration must create ledger events explicitly and retain migration provenance. |
| Account currencies | Multiple cash currencies and base currency | One fixed currency per account | No implicit conversion. A later ledger account model may replace or wrap the snapshot account. |
| Portfolio total | Consolidated CNY valuation | Separate CNY/HKD/USD views | No combined total or FX observation is produced in this milestone. |
| Local security | Administration secret and browser session | No authentication on loopback | Compensating same-origin, Host, Origin, Fetch Metadata, CSP, and bind controls apply. This milestone does not satisfy the umbrella MVP authentication gate. |

These deviations are approved staging decisions, not silent amendments to the umbrella design. If the long-term design changes, that change must be made explicitly in the umbrella specification.

## 5. User Experience and Information Architecture

### 5.1 Navigation

The production SPA has three routes:

- `/` — portfolio overview; this is the default and primary experience.
- `/holdings` — accounts and position creation, editing, and archiving.
- `/settings` — local database location, quote-provider availability, cache status, and recovery guidance.

Compatibility diagnostics remain API capabilities. They are not the home page and are not promoted into an operator dashboard in this milestone.

The UI language is Simplified Chinese. Symbols, provider names, currencies, protocol names, and timestamps retain standard notation. Every important numeric chart has a table or text alternative.

### 5.2 First run

The empty overview explains that no holdings exist and offers one primary action: create an account. The first-run path is:

```text
Create account
-> choose fixed currency
-> search instrument
-> confirm normalized instrument
-> enter quantity and average cost
-> view locally calculated holdings
-> explicitly refresh quotes
-> review valuation and freshness
```

Vibe compatibility is not a prerequisite for this path. No provider call occurs merely because the dashboard was opened.

### 5.3 Overview page

The overview is grouped by currency tabs: CNY, HKD, and USD. A currency tab appears when it has an active account or position. Each tab shows:

- market value of valued positions;
- position cost;
- unrealized profit/loss and percentage when cost is non-zero;
- account cash;
- an explicit count and cost of unvalued positions;
- allocation by position, excluding unvalued positions from the denominator;
- quote source, quote `as_of`, last fetch time, and freshness for every row;
- the last refresh summary and a visible `刷新行情` action.

When any position is unvalued, totals are labeled `估算` and explain what is excluded. Zero holdings, zero cost, unavailable quotes, stale quotes, partial refresh, and archived accounts each have an explicit state; none render as a misleading zero-value success.

### 5.4 Holdings page

Account and position maintenance is separate from the overview. The form flow supports:

- account name, fixed currency, optional cash balance, edit, and archive;
- instrument search by exact or partial code/name;
- display of canonical symbol, name, market, asset type, quote currency, and source before confirmation;
- positive quantity, non-negative average cost, and optional plain-text note;
- edit and archive with optimistic-concurrency protection;
- a clear currency-mismatch error if an instrument cannot belong to the selected account.

Archiving is reversible at the data layer, but the first UI may expose restoration only from the archived-items view. Accounts with active positions cannot be archived until those positions are archived or moved through a future supported flow.

### 5.5 Settings page

Settings is intentionally small. It shows:

- the redacted/relative local database location;
- schema version and migration health;
- each market-data adapter's enabled/disabled state, without secrets or raw endpoints;
- last successful refresh and cache health;
- recovery guidance and the location of automatic pre-migration backups.

It does not allow custom provider URLs, arbitrary filesystem paths, Vibe Session MCP overrides, or trading configuration.

## 6. Application Architecture

### 6.1 Backend boundaries

The Python package gains separate portfolio modules for domain types, persistence, application services, API routers, and market-data adapters. The existing `VibeGateway` remains the only component that knows Vibe HTTP details. Market-data adapters use a separate bounded HTTP client and must not import from `/Users/zhaibin/Dev/AInvest`, Vibe internals, or the Sidecar's Vibe adapter package.

`create_app` composes both domains while keeping their dependencies injectable. Existing compatibility-route semantics and failure behavior remain unchanged. Portfolio initialization failure must not be misreported as successful portfolio readiness.

The runtime OpenAPI document is published at `/api/v1/openapi.json`; interactive Swagger and ReDoc pages remain disabled. The frontend API client is generated or type-checked against this document in CI.

### 6.2 Frontend boundary

Source lives under `frontend/` as a React + TypeScript application built by Vite. Development uses Vite's same-origin-style `/api` proxy. Production output is immutable hashed assets plus `index.html`, served by FastAPI.

Routes are registered in this order:

1. `/api/v1/*` API routers;
2. static `/assets/*` files;
3. explicit SPA document routes and a final GET-only client-route fallback.

The SPA fallback never handles `/api/*`, non-GET requests, missing asset-like paths, or traversal-like paths. An unknown API path remains a JSON 404 rather than returning HTML. Existing compatibility routes retain their paths and response shapes.

### 6.3 Production startup

The normal `portfolio-api` command binds only to `127.0.0.1:8765`, verifies the data directory, checks database integrity, applies safe migrations, and serves the built SPA. A missing frontend build is an explicit startup/configuration error in production mode, not a blank page.

The Vite development server is a developer convenience only and is not part of the user-facing launch path.

## 7. Portfolio Domain and Storage

### 7.1 Database

SQLite is the sole source of portfolio state for this milestone. The default database is `var/data/portfolio.db`, which is ignored by Git. SQLAlchemy owns database access and Alembic owns forward-only schema migrations.

The data directory is created with owner-only permissions where the platform supports them. Startup rejects a symlinked database path, an unreadable database, a failed integrity check, and a schema newer than the application understands. Before a schema-changing migration, the application creates a timestamped local backup and verifies it can be opened. A failed or half-applied migration fails closed and never serves write endpoints as healthy.

### 7.2 Exact numbers

Binary floating point is prohibited for stored or calculated financial values. API decimal values are canonical strings. Python uses `Decimal`. SQLite stores canonical decimal strings through one reviewed SQLAlchemy type, and calculations occur in Python rather than SQLite floating-point aggregates.

- quantity: up to 8 fractional digits, strictly greater than zero;
- average cost, price, and cash: up to 6 fractional digits, non-negative except that accepted quote price must be strictly positive;
- percentages: calculated with an explicit context and rounded only for display;
- currency: explicit ISO-style enum `CNY`, `HKD`, or `USD` on every monetary boundary.

Input magnitude limits are defined in the API schema to prevent unreasonable memory or display behavior. Values are never silently rounded on write; excess precision is rejected with a field error.

### 7.3 Entities

`Account`

- UUID identifier;
- user-visible name;
- fixed currency (`CNY`, `HKD`, or `USD`);
- optional cash balance in that currency;
- integer version;
- created/updated timestamps;
- optional archived timestamp.

Active account names are unique after Unicode normalization and whitespace trimming. Account currency cannot be changed while it has an active position.

`Instrument`

- UUID identifier;
- canonical symbol;
- display name;
- market (`CN`, `HK`, or `US`);
- quote currency;
- asset type (`equity` or `etf`);
- normalized search identity;
- created/updated timestamps.

Canonical symbols follow `600519.SH`, `000001.SZ`, `00700.HK`, and `AAPL.US` conventions. Provider-specific symbols live in an explicit mapping table keyed by provider; they do not replace canonical identity. Instruments are created only from a confirmed, validated search result or a prevalidated test fixture.

`PositionSnapshot`

- UUID identifier;
- account and instrument identifiers;
- quantity;
- average cost in the account currency;
- optional plain-text note with a bounded length;
- integer version;
- created/updated timestamps;
- optional archived timestamp.

A partial unique index permits only one active position for an account/instrument pair. Quantity and average cost describe the current user-entered state and do not imply a transaction history. Notes are data, never instructions.

`LatestQuote`

- instrument identifier;
- accepted positive price and quote currency;
- provider;
- provider `as_of` timestamp with timezone;
- local `fetched_at` timestamp with timezone;
- last successful refresh-run identifier.

Only a fully validated quote may replace `LatestQuote`. Failed or malformed responses never overwrite the last valid value. Freshness is derived when data is read from the timestamps and the latest refresh outcome; it is not stored as a value that can silently become outdated.

`QuoteRefreshRun` and `QuoteRefreshItem`

- UUID run identifier, requested scope, start/end timestamps, and aggregate counts;
- per-instrument outcome (`updated`, `stale`, or `unavailable`);
- selected provider and stable sanitized error code;
- no raw provider response, URL query string, stack trace, account identifier, or holding value.

Refresh-run detail has bounded retention; the latest valid quote remains until explicitly superseded or the instrument is purged through a future data-deletion flow.

`InstrumentCandidateCache`

- opaque candidate identifier and bounded expiration time;
- normalized symbol, name, market, currency, asset type, provider, and provider mapping;
- no raw response body or arbitrary provider field.

Search writes only validated candidate fields into this short-lived server-side cache. Confirmation consumes an unexpired candidate and creates or returns the matching `Instrument`; the browser cannot promote a modified provider payload directly into the portfolio.

`IdempotencyRecord`

- endpoint scope and hash of the idempotency key;
- canonical request hash;
- resulting resource identifier/status and bounded response metadata;
- creation and expiration timestamps.

The raw key, full request body, holding values, and account names are not retained in this table. Records survive process restart for a documented retry window and are pruned with bounded retention.

### 7.4 Archive and concurrency

User-visible deletion is archive-first. Database foreign keys are enabled on every connection. Archived positions do not contribute to summaries. Archived accounts do not accept new positions.

PATCH requests carry the last observed integer `version`. A mismatch returns `409 CONCURRENT_MODIFICATION` with the current version and no partial write. Every POST and PATCH request requires an `Idempotency-Key`; replay with the same key and equivalent body returns the original result, while a different body returns `409 IDEMPOTENCY_CONFLICT`. For PATCH, a valid idempotent replay returns the original success even though the resource version has since advanced.

## 8. Valuation Rules

All calculations are deterministic and currency-local.

For a valued position:

```text
position_cost = quantity * average_cost
market_value = quantity * latest_price
unrealized_pnl = market_value - position_cost
unrealized_pnl_pct = unrealized_pnl / position_cost, only when position_cost > 0
```

For each currency, account cash is added only to that currency's estimated total. No FX conversion, synthetic exchange rate, or combined headline number is produced.

An instrument is:

- `fresh` when its most recent explicit refresh attempt accepted a quote whose `as_of` is not in the future and is no more than 72 hours old;
- `stale` when a prior valid quote exists but its most recent refresh attempt failed, or its accepted `as_of` is older than 72 hours;
- `unavailable` when no valid quote exists.

The 72-hour rule is intentionally conservative and does not claim market-session awareness. Long weekends and holidays may be labeled stale; the timestamp remains visible so the user can judge it. A later trading-calendar feature may refine this without changing stored quote provenance.

When any position is stale or unavailable, the affected row is marked. Unavailable positions are excluded from market-value and allocation denominators, while their cost and count are shown separately. Stale prices may contribute to the displayed estimate only with an explicit stale badge and estimated-total label. An omitted cash balance means `unknown`, not zero: it is excluded from the total and also forces the account/currency total to be labeled estimated.

## 9. Independent Market Data

### 9.1 Provider contract

Every adapter implements a Sidecar-owned protocol equivalent to:

```python
class MarketDataProvider(Protocol):
    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]: ...
    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]: ...
```

Provider DTOs are translated immediately into Sidecar domain values. No provider object crosses the adapter boundary. The adapter contract is tested with stored synthetic fixtures, not copied Vibe code or private Vibe data.

### 9.2 Routing

The initial routing policy mirrors useful source-selection ideas observed in Vibe-Trading while remaining independently implemented:

- A-shares and mainland ETFs: Eastmoney primary, Tencent fallback.
- Hong Kong stocks and ETFs: Yahoo primary, Eastmoney fallback.
- US stocks and ETFs: Yahoo primary; an EOD-only fallback may be enabled only after its adapter, licensing/usage conditions, and timestamp semantics pass review.
- Search: Eastmoney and Yahoo adapters may run with bounded parallelism; results are normalized, deduplicated, and ranked before confirmation.

An undocumented or unstable public endpoint is treated as a replaceable external dependency, not a guaranteed contract. If a market lacks an approved fallback, failure remains visible instead of silently substituting fabricated or mismatched data.

### 9.3 Request safety

- Provider hosts, schemes, paths, redirect behavior, batch sizes, and response-size limits are code-defined.
- Only HTTPS fixed-host requests are allowed; user-supplied URLs, proxies, and arbitrary headers are prohibited.
- Redirects are disabled unless an exact redirect target is separately allowlisted.
- Connect, read, total-operation, and concurrency limits are bounded.
- Search query length, character set, and result count are bounded.
- Refresh groups instruments by provider/market and uses bounded concurrency.
- Provider errors are mapped to stable local codes and sanitized before logging or persistence.

### 9.4 Validation

Before acceptance, a quote must have:

- an exact mapping to the requested canonical instrument;
- the expected market and quote currency;
- a positive finite decimal price within configured magnitude and precision limits;
- a timezone-aware `as_of` no more than five minutes in the future;
- no duplicate conflict within the same provider response.

Unknown extras, ambiguous mappings, conflicting duplicate prices, HTML/error payloads, truncated data, and malformed timestamps are rejected. Provider name alone is not evidence of correctness.

### 9.5 Refresh behavior

Refresh begins only from the explicit `刷新行情` action or an explicit API request. One request:

1. snapshots the active instrument IDs in scope;
2. groups them by market and provider route;
3. calls primaries with bounded concurrency;
4. calls fallbacks only for failed/missing instruments;
5. validates each result independently;
6. commits valid latest quotes and the run summary in one database transaction;
7. returns updated/stale/unavailable counts, providers used, and timestamps.

A partial provider failure does not roll back valid quotes for other instruments. It also does not turn a stale quote into a fresh one. Concurrent refresh requests are serialized or deduplicated by a process-local lock plus a database run-state check; a second conflicting request receives the in-progress run identifier rather than starting a duplicate fan-out.

No quote network call occurs on dashboard load, process startup, health checks, database reads, or Vibe compatibility checks.

## 10. API Contract

All resources live under `/api/v1`. Decimal values are strings, timestamps are timezone-aware ISO 8601 values, list responses are cursor-paginated, and errors use stable codes with field-safe details.

Initial resources:

- `GET /api/v1/portfolio/summary?currency=CNY`
- `GET /api/v1/accounts`
- `POST /api/v1/accounts`
- `PATCH /api/v1/accounts/{account_id}`
- `GET /api/v1/positions`
- `POST /api/v1/positions`
- `PATCH /api/v1/positions/{position_id}`
- `GET /api/v1/instruments/search?q=...&limit=...`
- `POST /api/v1/instruments/confirm`
- `POST /api/v1/market-data/refresh`
- `GET /api/v1/market-data/refresh/{run_id}`
- `GET /api/v1/settings/status`
- existing `/api/v1/system/*` compatibility endpoints, unchanged in meaning.

The summary endpoint reads SQLite only. Search is an explicit user action and may call approved search adapters. It returns short-lived opaque candidate identifiers; confirmation resolves one server-side candidate into a durable instrument. Refresh is synchronous within a bounded operation deadline; if the client disconnects, the server completes or aborts according to the committed run state and never leaves a run falsely marked successful.

Representative stable errors include:

- `VALIDATION_ERROR`
- `CURRENCY_MISMATCH`
- `DUPLICATE_POSITION`
- `ACCOUNT_HAS_ACTIVE_POSITIONS`
- `CONCURRENT_MODIFICATION`
- `IDEMPOTENCY_CONFLICT`
- `INSTRUMENT_NOT_CONFIRMED`
- `QUOTE_PARTIAL_FAILURE`
- `QUOTE_UNAVAILABLE`
- `QUOTE_RESPONSE_INVALID`
- `QUOTE_REFRESH_IN_PROGRESS`
- `DATABASE_BUSY`
- `DATABASE_UNAVAILABLE`
- `SCHEMA_UNSUPPORTED`

Errors never return provider bodies, database paths, stack traces, secrets, account identifiers not already present in the request, or personal holdings beyond the resource being edited.

## 11. Security and Privacy

### 11.1 Loopback-only profile

The first milestone intentionally has no login. This is safe only for the explicit local profile:

- bind to `127.0.0.1`, never `0.0.0.0`;
- allow only configured `Host` values for `127.0.0.1` and `localhost` with the expected port;
- serve frontend and API from the same origin;
- emit no permissive CORS headers;
- require exact allowed `Origin` and same-origin Fetch Metadata for browser state-changing requests;
- require JSON content type and bounded request bodies;
- use `SameSite` only if cookies are later introduced; this milestone stores no auth cookie;
- reject non-loopback deployment until authentication, TLS, CSRF, and secret management are designed and enabled.

This blocks normal cross-site browser writes and DNS-rebinding Host abuse. It does not protect against a malicious local process running as the user; the UI and documentation state that limitation. Remote access remains unsupported.

### 11.2 Browser hardening

Production assets are self-hosted with no CDN runtime dependency. Responses set at least:

- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- a restrictive `Permissions-Policy`;
- `Cache-Control: no-store` for personal API responses;
- immutable cache headers only for content-hashed static assets.

React text rendering is used for names and notes. Raw HTML rendering is prohibited. Formula-style content, provider text, and notes remain inert data.

### 11.3 Data handling

Portfolio records, account names, notes, quote mappings, and backups remain under ignored local runtime directories. Logs use request/run correlation IDs but redact holdings, balances, database absolute paths, provider query strings, credentials, and raw payloads. Tests and docs use synthetic examples only.

No portfolio data, generated database, backup, provider response, token, account identifier, or personal symbol list may enter Git, handoff documents, screenshots, or test fixtures.

## 12. Failure and Recovery Semantics

- Vibe stopped, unsupported, or malformed: portfolio CRUD, cached quotes, and deterministic summaries continue; deep analysis remains fail-closed.
- Quote provider unavailable: retain last valid quote, mark stale, and show a recovery action.
- Some quotes fail: commit valid results, preserve failures independently, and label the total estimated.
- All quotes fail: retain prior valid quotes, create a failed run summary, and never report refresh success.
- Database locked: perform bounded retry, then return `503 DATABASE_BUSY` without a partial write.
- Database read-only/full: stop writes, preserve reads where safe, and show backup/recovery guidance.
- Migration fails: preserve the pre-migration backup, fail portfolio readiness, and do not operate on an uncertain schema.
- Built SPA missing: fail production startup with a clear packaging error.
- Search ambiguous: require user confirmation; never guess the instrument.
- Currency mismatch: reject position creation; never auto-convert.
- Concurrent edit: return 409 and let the UI compare/reload before retry.

Loading, empty, partial, stale, unavailable, validation, conflict, and retry states are all separate UI states. A generic toast alone is not sufficient for a failed financial-data action.

## 13. Testing and Release Gates

### 13.1 Backend

- Unit tests for decimal parsing, precision rejection, currency separation, valuation formulas, zero cost, archive rules, and freshness classification.
- Migration tests from an empty database, the previous schema, unsupported future schema, locked database, failed migration, backup creation, and restart persistence.
- Constraint tests for duplicate active positions, foreign keys, account currency changes, and optimistic concurrency.
- Adapter contract tests for symbol normalization, deduplication, currency mismatch, duplicate conflict, future timestamps, non-positive/non-finite values, malformed payloads, timeouts, response-size limits, and fallback selection.
- API tests for accounts, positions, search, refresh, pagination, idempotency, conflicts, partial failures, database failure, security headers, Host/Origin validation, and sanitized errors.
- Routing tests proving SPA fallback does not swallow API 404s, missing assets, or non-GET methods.
- Regression tests proving the three existing compatibility operations keep their semantics and only the intended new API/OpenAPI/static routes are added.

### 13.2 Frontend

- Vitest and React Testing Library coverage for empty onboarding, currency tabs, estimated totals, stale/unavailable states, search confirmation, form validation, conflicts, archive, and request failures.
- Minimum 80% frontend line coverage for milestone-owned application code.
- Accessibility tests for keyboard operation, focus restoration, labels, errors, contrast, 200% zoom, reduced motion, and chart alternatives.
- Type checking and a production Vite build with no runtime CDN dependency.

### 13.3 End-to-end

Playwright runs against a real FastAPI process serving the production SPA and a fake local market-data adapter; the default E2E suite never accesses the internet. It covers:

1. create CNY, HKD, and USD accounts;
2. search and confirm one instrument for each currency;
3. add current positions and cash;
4. explicitly refresh quotes;
5. verify separated summaries and exact values;
6. edit and archive a position;
7. restart the service and verify persistence;
8. exercise partial refresh, stale fallback, unavailable quote, edit conflict, and API-404 routing.

Real-provider smoke tests are opt-in, rate-bounded, use no personal holdings, and report `not run` when configuration or explicit flags are absent. A skipped provider or Vibe gate is never described as passed.

### 13.4 Required commands

The existing hermetic gate remains mandatory:

```bash
uv sync --frozen --extra dev
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
```

The implementation plan must add locked frontend commands for install verification, lint, type check, unit coverage, build, and Playwright. It must retain the documented opt-in Vibe route/runtime/MCP gates and add a separate, clearly labeled opt-in market-provider gate.

Release requires all hermetic backend, frontend, security, migration, and E2E gates to pass. It also requires a clean review for secrets and personal data. It does not require Vibe to be running for the portfolio experience.

## 14. Delivery Boundaries

- AInvest remains read-only and receives no patch.
- No Vibe internal module, database, private file, or runtime patch is used.
- No `ALLOW_SESSION_MCP_SERVERS=1` or Session `mcpServers` override is introduced.
- Portfolio MCP remains loopback-only, bearer-authenticated, read-only, and allowlisted.
- Existing system compatibility endpoints continue to fail closed.
- The dashboard never claims realtime pricing.
- No recommendation or execution wording is added.
- Generated frontend output may be packaged for releases but source-of-truth build artifacts and local databases follow an explicit repository policy in the implementation plan.

## 15. Comprehensive Design Audit

The audit was performed against `AGENTS.md`, the current handoff, the umbrella product design, the live CodeGraph index, and current FastAPI route composition.

### 15.1 Findings resolved in this specification

1. **Milestone identity conflict — resolved.** A current-position model contradicts the umbrella MVP ledger if presented as the MVP. It is now explicitly Experience Milestone 1A with a later provenance-preserving migration requirement.
2. **Currency-model conflict — resolved.** Fixed-currency accounts and separated totals are staged behavior. The API never emits an implicit consolidated value.
3. **Vibe quote dependency — removed.** Current Vibe public routes do not provide a suitable structured quote contract. Market data is a separate Sidecar-owned adapter boundary, and cached portfolio use does not require Vibe.
4. **Existing API route invariant — resolved.** Adding portfolio routes, OpenAPI, and static files necessarily changes total route count. Tests must preserve the semantics and exact paths of the existing three diagnostic operations rather than asserting that the application can never gain routes.
5. **SPA fallback risk — resolved.** API precedence and GET-only fallback rules prevent HTML 200 responses for missing API paths or assets.
6. **Precision risk — resolved.** Decimal strings and Python `Decimal` are required end-to-end; SQLite floating-point aggregation is prohibited.
7. **Partial quote corruption risk — resolved.** Only validated quotes replace cache entries; per-instrument failures preserve stale data and cannot claim complete success.
8. **Duplicate writes and lost edits — resolved.** Idempotency keys and integer versions cover retries and concurrent browser tabs.
9. **Untrusted search-result promotion — resolved.** A short-lived server-side candidate plus explicit confirmation prevents the browser from modifying symbol/currency/provider fields before instrument creation.
10. **CSRF/DNS-rebinding risk without auth — mitigated.** Loopback bind, Host allowlist, exact Origin, Fetch Metadata, no CORS, and CSP are mandatory. The remaining malicious-local-process risk is explicit.
11. **SSRF/provider injection risk — resolved.** Provider destinations and limits are code-defined; settings cannot accept arbitrary URLs.
12. **Migration/data-loss risk — resolved.** Integrity checks, owner-only paths, pre-migration backups, version checks, and fail-closed startup are release gates.
13. **Test false-confidence risk — resolved.** Default tests use fake providers. Live provider, Vibe runtime, and MCP checks remain separate opt-in gates with `not run` semantics.
14. **Operations-first UX drift — resolved.** The overview and holdings experience are primary; compatibility diagnostics do not become the home page.
15. **Trading-scope creep — resolved.** All broker writes, order controls, and execution implications remain prohibited in UI, API, and provider adapters.

### 15.2 Accepted residual risks and later work

- Public quote endpoints can change, rate-limit, or impose usage constraints. Adapter isolation, provenance, fallback, and opt-in smoke tests reduce but do not remove this risk. Provider usage and redistribution conditions must be reviewed before any non-personal or packaged distribution.
- The no-auth loopback profile cannot defend against malicious software running as the same local user. Non-loopback support remains blocked until the umbrella authentication profile is implemented.
- Snapshot holdings cannot calculate realized returns, transaction-derived cost basis, or reconciled performance. The UI must not imply otherwise.
- The conservative 72-hour freshness rule is not exchange-calendar aware and may mark a valid holiday close stale.
- Backup restore UI and permanent data deletion remain formal MVP work; automatic pre-migration backup only reduces near-term migration risk.
- A later ledger migration requires an explicit user-reviewed conversion of current snapshots into opening events. It must never invent historical transactions.

### 15.3 Planning gate

The design is implementation-plan ready only after the user reviews this written specification and accepts the staged deviations and residual risks. Implementation must then use TDD, proceed in reviewable slices, and update the current handoff with actual—not planned—verification evidence.

## 16. Acceptance Summary

The user approved the design direction and its five sections before this document was written:

1. React/TypeScript overview-first WebUI served by FastAPI.
2. Local SQLite account and current-position snapshot model.
3. Sidecar-owned explicit-refresh market data independent of Vibe-Trading.
4. Currency-separated API and user flows with stale-data preservation.
5. Loopback security, failure behavior, hermetic E2E, and proportional release gates.

Final written-spec review remains the gate before producing the implementation plan.
