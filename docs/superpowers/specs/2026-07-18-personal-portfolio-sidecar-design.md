# Personal Portfolio Sidecar Design

**Date:** 2026-07-18  
**Status:** Approved design v2  
**Product:** Vibe-Trading Portfolio  
**Upstream integration target:** `zhaibin/Vibe-Trading`

## 1. Summary

Vibe-Trading Portfolio is a local, self-hosted personal portfolio product for A-shares, Hong Kong equities, US equities, ETFs, and public funds. It maintains a multi-account, multi-currency investment ledger; produces reproducible portfolio snapshots and deterministic risk analysis; and uses Vibe-Trading through public REST, SSE, and MCP boundaries for market research, evidence gathering, backtest validation, and later multi-agent review.

The product is a separate sidecar repository. It must not modify, import internal code from, or share persistence with the upstream Vibe-Trading checkout. Vibe-Trading must remain independently upgradeable. If Vibe-Trading is stopped or incompatible, the portfolio ledger, snapshots, and deterministic analytics remain available.

The default mode is personal research assistance. The product does not place orders, expose write-capable broker tools, promise returns, or enable commercial/multi-user advisory use.

This document is the umbrella product design. Implementation is deliberately decomposed into separate plans for Milestone 0, MVP, v1.1, and v1.2; approval of this document does not authorize bundling those milestones into one delivery.

## 2. Goals

- Maintain multiple investment accounts with account-level base currencies and a CNY consolidated view.
- Import a standard CSV format and recognize common exports from Tonghuashun, Eastmoney, and Futu.
- Preserve an auditable, immutable event ledger for trades, cash, income, expenses, tax, FX, transfers, and supported corporate actions.
- Distinguish reconciled history from opening-position or partial-history accounts.
- Calculate reproducible positions, cash, realized and unrealized results, income, expenses, tax, and FX contribution.
- Produce immutable portfolio snapshots with data quality, price freshness, source, and calculation-version metadata.
- Capture a versioned investor risk profile and check it for contradictions.
- Provide deterministic portfolio diagnostics and evidence-backed research assistance.
- Create bounded, versioned analysis context and invoke Vibe-Trading through stable external interfaces.
- Require structured recommendations with evidence, counterevidence, uncertainty, invalidation conditions, and validation.
- Support on-demand analysis in the MVP and daily/weekly briefs in v1.1.
- Remain removable and independently versioned without changing Vibe-Trading source files.

## 3. Non-goals

The MVP does not support:

- Automatic order creation or execution.
- Write-capable broker integration.
- Broker API synchronization.
- Short selling, margin, futures, options, forex trading, or crypto assets.
- Automated tax filing or jurisdiction-specific tax reporting.
- Real-time quote streaming.
- Commercial or multi-user robo-advisory service.
- Swarm investment committees, Shadow Account integration, or target-portfolio optimization in the MVP.
- Direct imports of Vibe-Trading `agent/` or `backtest/` Python modules.

## 4. Repository and Deployment Boundary

The products live in separate repositories:

```text
Vibe-Trading/                  # Upstream fork; no Portfolio product changes
Vibe-Trading-Portfolio/        # Independent sidecar product
```

Runtime topology:

```text
Portfolio Web
    |
    v
Portfolio Sidecar API ------ Portfolio SQLite
    |
    +-- VibeGateway -- REST/SSE --> Vibe-Trading API
    |
    +-- Portfolio MCP <---------- Vibe-Trading Agent
```

Hard boundaries:

- No Vibe-Trading source changes.
- No shared database.
- No dependency on Vibe-Trading local file paths.
- No internal Python imports from Vibe-Trading.
- No automatic modification of Vibe-Trading operator configuration.
- Portfolio can be stopped or removed without changing Vibe-Trading behavior.
- Vibe-Trading can be upgraded independently; compatibility is negotiated at runtime.

## 5. MCP Installation and Trust Boundary

Vibe-Trading strips API-supplied `mcpServers` by default because MCP definitions can carry commands, arguments, and environment variables. Portfolio must not enable `ALLOW_SESSION_MCP_SERVERS=1`.

Instead:

1. Portfolio provides an installer that generates an operator configuration snippet.
2. The user explicitly reviews and installs the snippet into Vibe-Trading's operator-controlled configuration.
3. Vibe-Trading loads the Portfolio MCP as an operator-approved server.
4. Portfolio Session requests contain analysis settings and bounded context, not MCP definitions.

The MCP server:

- Exposes read-only tools only.
- Uses a dedicated access token.
- Binds to a protected local address or an internal container network.
- Returns structured JSON.
- Paginates position and history results.
- Records tool name, time, and requested scope without logging full financial results.
- Does not expose unrestricted filesystem access, raw database queries, or mutation tools.

If MCP is absent, Portfolio enters a degraded mode and sends a limited core context through the Session message.

## 6. VibeGateway

`VibeGateway` is the only component that knows Vibe-Trading API details. It provides stable Portfolio-facing interfaces for:

- Liveness and readiness checks.
- Version and capability discovery.
- Compatibility negotiation.
- Session creation.
- Creation of a research-only goal.
- Message submission.
- SSE authentication ticket acquisition.
- SSE event streaming and reconnection.
- Message polling as an SSE fallback.
- Cancellation.
- Stable error mapping.

Startup compatibility states:

- `compatible`: all required capabilities passed.
- `degraded`: local functions work, some Vibe features are disabled.
- `unsupported`: deep analysis is disabled, local financial data remains usable.

Unknown versions fail closed for deep analysis. Adapters declare the Vibe version range and capabilities they support. CI covers the minimum supported version, current stable version, and latest upstream. Failure against latest upstream generates a compatibility alert; it does not silently redefine the stable support range.

## 7. Ledger Domain

### 7.1 Core entities

- `Account`
- `Instrument`
- `InstrumentAlias`
- `LedgerEvent`
- `LedgerEntry`
- `ImportBatch`
- `ImportIssue`
- `Reconciliation`
- `AccountingPolicy`
- `PriceObservation`
- `FxObservation`
- `RiskProfileVersion`
- `PortfolioSnapshot`
- `AnalysisRun`
- `Evidence`
- `RecommendationSet`
- `Report`

### 7.2 Account

An account stores:

- Name and account type.
- Broker or manual source.
- Reporting base currency.
- Market and time-zone configuration.
- Cost-basis policy.
- Data completeness and reconciliation status.
- Created and archived timestamps.

Each account may hold multiple cash currencies. The account base currency is a reporting choice, not a single-currency restriction. The consolidated portfolio reports in CNY.

### 7.3 Event and entry model

`LedgerEvent` is immutable after posting and contains one or more `LedgerEntry` records. Supported event types are:

- Buy and sell.
- Cash deposit and withdrawal.
- Dividend and interest.
- Fee and tax.
- Security transfer in and out.
- Cash transfer and FX conversion.
- Split and reverse split.
- Symbol change.
- Opening position and opening cash.
- Reversal.

A buy event, for example, contains security quantity, cash, fee, and tax entries. Event-specific balance invariants must pass before commit. Posted errors are corrected with reversal and replacement events rather than in-place changes.

All quantities, prices, exchange rates, and monetary amounts use Decimal semantics and are stored as fixed-scale integers or canonical decimal strings. Binary floating point is forbidden in accounting persistence and calculation boundaries.

### 7.4 Time model

Events record:

- `executed_at` with exchange time zone.
- `trade_date`.
- `settlement_date`.
- `recorded_at`.
- `effective_at` for corrections and corporate actions.

The position and cash engines distinguish executed, unsettled, settled, and available balances.

### 7.5 Cost basis

The account selects FIFO or moving weighted average before its first posted trade. The policy is then locked. An alternative method can be evaluated only as a separately versioned calculation projection; it cannot rewrite ledger history.

Broker-reported display cost is retained as reference data and never overwrites system-derived cost. Differences appear in reconciliation.

### 7.6 Completeness and reconciliation

Account states are:

- `unreconciled`
- `position_only`
- `partial_history`
- `reconciled`

Opening positions and cash allow users without complete history to start tracking. Reports must disclose the analysis start date and limitations. Complete performance attribution is available only to accounts that meet the required completeness level. Current value, concentration, and risk diagnostics can include incomplete accounts with explicit quality labels.

### 7.7 Instrument identity

Instruments use stable internal identifiers derived from asset type, exchange, symbol, and currency. Broker-native symbols, normalized symbols, historical aliases, symbol changes, listing status, and delisting status are stored separately so a ticker change does not break history.

### 7.8 Supported asset scope

The first release supports long-only spot positions in A-shares, Hong Kong equities, US equities, ETFs, and public funds. Unsupported asset types are rejected during import with an actionable issue message.

## 8. Import and Reconciliation

Import is a two-phase workflow:

```text
Upload
-> Store file hash and parser version
-> Detect format
-> Map to standard events
-> Validate symbol, currency, time, amount, and balance
-> Preview source and normalized values
-> Flag duplicate candidates and issues
-> User correction
-> Reconciliation
-> Atomic commit
```

Duplicate detection priority:

1. Broker transaction or execution ID.
2. Stable record ID within the file.
3. Batch, source row, and business-field combination.

When a reliable ID is absent, matching records are duplicate candidates and require review. They are not automatically dropped.

The product owns its import adapters. It may use Vibe-Trading's existing parser behavior as research input, but it does not import Vibe-Trading parser modules because those records use floats and do not define the required account, currency, settlement, tax, and reconciliation semantics.

Successful commit is atomic. A batch with unresolved blocking issues cannot partially post. Raw uploaded files are deleted after successful commit by default, subject to a configurable retention policy.

## 9. Valuation and Snapshots

Price and FX observations are immutable source records with provider, observation time, currency, freshness, and quality.

- Equities and ETFs use the most recent valid market price.
- Public funds use the latest published NAV and display its NAV date.
- Suspended or old observations are marked stale.
- Observations beyond the configured freshness threshold do not participate in target-weight optimization.
- Unvalued assets are listed separately and are not assigned zero or fabricated prices.

`PortfolioSnapshot` contains:

- Ledger version and canonical input hash.
- Account completeness and reconciliation states.
- Cash by currency.
- Positions, lots, costs, values, and weights.
- Realized and unrealized results.
- Dividend, fee, tax, and FX contributions.
- Price and FX provenance.
- Data coverage and freshness.
- Unvalued assets.
- Accounting and metric-definition versions.

A snapshot is immutable and reproducible from its referenced inputs.

## 10. Risk Profile

`RiskProfileVersion` records:

- Investment horizon.
- Willingness to accept risk.
- Financial capacity to absorb loss.
- Maximum acceptable drawdown.
- Liquidity needs.
- Single-position and sector limits.
- Market and currency exposure limits.
- Excluded assets.
- Benchmark.
- Minimum cash requirement.
- User context notes.
- Effective and review dates.

Contradictory answers block recommendation generation until the user confirms or revises the profile. Updating a profile creates a new version; existing reports retain the version used at generation time.

## 11. Deterministic Analytics

Portfolio code calculates all financial facts:

- Asset, account, market, sector, and currency allocation.
- Cash weight.
- Realized and unrealized results.
- Return, dividend, expense, tax, and FX contribution.
- Position and sector concentration.
- Volatility, maximum drawdown, and downside risk.
- Correlation and duplicate exposure.
- Risk contribution.
- Risk-profile constraint violations.
- Coverage, freshness, and unvalued assets.

Each metric includes an ID, value, unit, currency, window, formula version, `as_of`, source observation IDs, and quality status. LLM output cannot override these values.

## 12. Analysis Context

An analysis context binds:

- `snapshot_id`
- `risk_profile_version`
- `metric_definition_version`
- `context_schema_version`
- generation time
- canonical content hash

The core Session message is limited to approximately 4,000 characters to remain below Vibe-Trading's message limit and leave room for instructions. It includes the objective, constraints, portfolio summary, quality status, violations, research targets, required questions, and prohibition on trade execution.

Context selection includes:

- Highest-weight positions.
- Highest risk contributors.
- Material profit and loss outliers.
- All constraint violations.
- Missing or stale data.
- Material event candidates.
- Aggregated tail holdings.

Detailed context is available through paginated read-only MCP tools:

- `portfolio_get_snapshot`
- `portfolio_list_positions`
- `portfolio_get_position`
- `portfolio_get_performance`
- `portfolio_get_risk_profile`
- `portfolio_get_evidence_inputs`

Transaction details are not exposed by default and require an explicit account and time-bounded review request.

## 13. Analysis Orchestration

Responsibility is split into three layers:

1. Deterministic Portfolio analytics owns facts, metrics, constraints, and later candidate weights.
2. Vibe-Trading owns external research, evidence discovery, counterarguments, and backtest validation through public interfaces.
3. Recommendation validation owns schema checking, fact checking, and publication gates.

Analysis flow:

```text
Preflight
-> Create AnalysisRun
-> Create a dedicated Vibe Session
-> Create a research-only goal
-> Send bounded context
-> Agent calls Portfolio MCP and Vibe research tools as needed
-> Collect SSE events or messages
-> Parse structured output
-> Validate
-> Publish, mark partial, fail, or cancel
```

Preflight checks Vibe compatibility, MCP availability, risk-profile validity, account completeness, price and FX coverage, freshness, and the estimated time/model budget.

States are:

- `queued`
- `running`
- `waiting_external_data`
- `validating`
- `partial`
- `completed`
- `failed`
- `cancelled`

SSE reconnection recovers the original run. It must not create a duplicate Session or model charge. Polling the original Session is the fallback.

## 14. Recommendation Contract

Agent output must parse into a versioned `RecommendationSet` containing:

- Summary.
- Portfolio health.
- Risk findings.
- Position findings.
- Action candidates.
- Evidence.
- Unknowns.
- Next checks.

Action values are:

- `consider_increase`
- `consider_reduce`
- `hold_and_monitor`
- `review_exit`
- `insufficient_evidence`

Each action includes current weight, optional target range, deterministic proposal ID, trigger, evidence IDs, counterevidence, risks, invalidation conditions, data time, confidence label, and missing information.

An LLM cannot supply a target range without a deterministic proposal ID. It may explain and challenge an independently calculated proposal but cannot invent weights.

The validator verifies metric references, snapshot values, profile constraints, evidence provenance and freshness, internal consistency, missing-data disclosure, schema completeness, and absence of orders, execution instructions, or return promises. One format-repair attempt is allowed. A second failure produces a `partial` report rather than a formal recommendation.

## 15. Product Scope and Milestones

### Milestone 0: compatibility spike

- Vibe version and capability discovery.
- Session, message, SSE, cancellation, and polling.
- Operator-approved Portfolio MCP.
- Read-only tool invocation.
- Correct offline and unsupported-version degradation.

### MVP

- Connection diagnostics.
- Versioned risk profile.
- Multi-account management.
- Manual entry.
- Standard, Tonghuashun, Eastmoney, and Futu CSV import.
- Preview, correction, reconciliation, and atomic posting.
- Multi-currency ledger.
- Snapshots and deterministic overview.
- Risk diagnostics.
- One on-demand deep analysis flow.
- Structured report and export.
- Backup and restore.

### v1.1

- Per-position research.
- Daily and weekly briefs.
- Report history and snapshot comparison.
- Investment-thesis and invalidation tracking.
- Analysis cost and latency reporting.

Portfolio owns the scheduler. A scheduled brief is complete only when its Vibe Session finishes and its report validates; Vibe-Trading's scheduled-research enqueue status is not treated as report completion.

### v1.2

- Deterministic constrained optimization.
- Turnover and transaction-cost constraints.
- Current-versus-candidate backtest comparison.
- Trade Journal and Shadow Account adapters.
- Swarm investment committee.
- Read-only broker synchronization.

Only public interfaces are stable reuse points. Internal Vibe-Trading modules remain experimental and version-pinned if ever evaluated.

## 16. Information Architecture and First-Run Flow

Top-level areas:

1. Overview.
2. Accounts.
3. Ledger.
4. Research.
5. Reports.
6. Data Quality.
7. Settings.

The default UI language is Simplified Chinese. Standard symbol, provider, and protocol names retain English. The architecture remains internationalization-ready, but the first release does not maintain multiple UI languages.

First-run flow:

```text
Welcome
-> Check Vibe compatibility
-> Complete risk profile
-> Create account
-> Select manual or CSV import
-> Preview normalization
-> Resolve issues
-> Reconcile cash and positions
-> Generate first snapshot
-> Review deterministic diagnostics
-> Start deep analysis
-> Review validated report
```

Every important screen defines empty, loading, partial-data, stale-data, upstream-unavailable, authentication-failure, success, recoverable-error, and incompatible-version states. Errors name the recovery action.

Before deep analysis, the UI displays expected duration, model budget, missing data, and freshness. Insufficient data changes the primary action to data remediation instead of analysis. Recommendation screens have no order or broker-execution action.

## 17. API

The Sidecar API uses `/api/v1` and publishes OpenAPI as the frontend contract.

Primary resources:

- `/system/status`
- `/system/compatibility`
- `/accounts`
- `/accounts/{id}/reconciliation`
- `/risk-profiles`
- `/imports`
- `/imports/{id}/preview`
- `/imports/{id}/issues`
- `/imports/{id}/commit`
- `/ledger/events`
- `/ledger/events/{id}/reverse`
- `/snapshots`
- `/snapshots/{id}/metrics`
- `/analyses`
- `/analyses/{id}/events`
- `/analyses/{id}/cancel`
- `/reports`
- `/reports/{id}/export`
- `/backups`
- `/backups/{id}/restore`

Write operations accept idempotency keys and optimistic concurrency versions. Lists are paginated. Decimal values travel as strings. Dates, time zones, currencies, and units are explicit.

Stable error codes include:

- `DATA_INCOMPLETE`
- `ACCOUNT_UNRECONCILED`
- `PRICE_STALE`
- `FX_MISSING`
- `VIBE_UNAVAILABLE`
- `VIBE_UNSUPPORTED`
- `MCP_NOT_CONFIGURED`
- `ANALYSIS_BUDGET_EXCEEDED`
- `RECOMMENDATION_INVALID`
- `IMPORT_REVIEW_REQUIRED`

## 18. Security and Privacy

Default deployment:

- Binds to `127.0.0.1`.
- Serves frontend and API from the same origin.
- Generates a local administration secret.
- Uses `HttpOnly`, `SameSite` browser sessions and CSRF protection.
- Keeps Vibe API credentials on the server.
- Uses a distinct Portfolio MCP token.
- Exposes no write-capable MCP tools.

Non-loopback deployment requires explicit opt-in, TLS termination, and replacement of default secrets.

SQLite, configuration, backup, and runtime paths use owner-only permissions. Optional local database encryption is supported; encryption keys are stored separately. Logs redact credentials, account identifiers, absolute paths, holdings, and transaction detail. The user can export and permanently delete all Portfolio data.

CSV and external text are untrusted. The implementation protects against formula injection, path traversal, executable content, prompt injection, and unrestricted file access. Original names and notes are transported as data fields, never as system instructions.

## 19. Database Migration and Recovery

Migration sequence:

```text
Inspect schema
-> Integrity check
-> Timestamped backup
-> Migration transaction
-> Validate ledger invariants
-> Update schema version
```

A failed migration leaves the original database and backup intact. Startup refuses a half-migrated database.

Backups include database, schema version, application version, accounting version, checksum, and creation time. Restore imports to a temporary path, validates it, and only then replaces the active database.

## 20. Fault Isolation

- Vibe unavailable: ledger, snapshots, and deterministic metrics continue.
- MCP unavailable: bounded-context analysis only, visibly degraded.
- Partial price failure: available assets remain usable; missing assets are excluded from optimization.
- Agent failure: retain Snapshot, Session, and event trail for retry.
- SSE failure: recover or poll the original Session.
- Validation failure: save as partial, not formal advice.
- Database read-only or full: stop writes and request backup/recovery.
- Parser upgrade: old imports stay bound to the parser version used at commit.
- Unknown Vibe version: disable deep analysis, preserve local access.

## 21. Observability

The trace chain is:

```text
snapshot_id
-> analysis_run_id
-> vibe_session_id
-> vibe_attempt_id
-> report_id
```

Telemetry covers stage latency, upstream requests, MCP scope, model budget, coverage, validation failures, and adapter version. Default logs exclude report content and personal financial data.

## 22. Testing

- Domain unit tests for every supported event and currency path.
- Property tests for balance invariants, reversals, idempotency, and deterministic snapshots.
- Independently checked golden accounting samples.
- Import fixtures for supported brokers, corrupt encodings, missing columns, duplicates, and malicious input.
- Snapshot and context canonicalization tests.
- Recommendation validator tests for fabricated facts, stale evidence, constraint violations, contradictions, and execution language.
- API and MCP schema, authentication, pagination, concurrency, and cancellation tests.
- Vibe contract tests against minimum, stable, and latest versions.
- Fault injection for timeouts, SSE loss, MCP failure, database locking, disk exhaustion, and migration failure.
- Security tests for traversal, formula injection, prompt injection, credential exposure, CORS, CSRF, and authorization.
- End-to-end tests from account creation through report publication.
- Accessibility verification for keyboard flow, focus, labels, zoom, contrast, and chart alternatives.

## 23. Release Gates

MVP release requires:

- All ledger invariant tests pass.
- Golden samples match independently reviewed results.
- Backup and restore rehearsal passes.
- Current stable Vibe contract passes.
- Offline Portfolio behavior passes with Vibe stopped.
- MCP read-only boundary passes.
- No Recommendation Validator bypass exists.
- Import-to-report end-to-end flow passes.
- Personal research positioning and risk disclosures are complete.
- Commercial, multi-user, broker-write, and automatic-trading modes remain disabled.

## 24. Success Metrics

- First-import completion rate.
- Reconciliation success rate.
- Time to first trusted snapshot.
- Import-issue resolution rate.
- Percentage of published recommendations with a complete evidence chain.
- Correct abstention rate when evidence is insufficient.
- Deep-analysis completion rate and latency.
- Vibe compatibility test pass rate.
- User comprehension of why a recommendation was produced.

## 25. Compliance Boundary

The default product is a local, self-hosted personal research assistant. It does not execute trades or promise returns. Commercial distribution, paid personalized advice, multi-user service, or client-facing automated rebalancing is outside the approved scope and requires jurisdiction-specific legal and compliance review before it can be designed or enabled.

## 26. Design Acceptance

This specification incorporates the product, architecture, accounting, integration, security, and regulatory audit completed on 2026-07-18. Its five design sections were approved sequentially by the user:

1. Independent sidecar architecture and operator-controlled MCP.
2. Multi-currency event ledger, reconciliation, and valuation.
3. Deterministic analytics, bounded context, Agent orchestration, and validated recommendations.
4. Phased product scope and user experience.
5. Versioned API, security, fault isolation, testing, and release gates.
