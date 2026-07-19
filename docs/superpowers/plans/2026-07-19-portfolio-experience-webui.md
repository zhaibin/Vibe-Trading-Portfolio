# Portfolio Experience WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local Simplified Chinese portfolio WebUI with exact current-position storage, currency-separated valuation, and explicit Sidecar-owned market-data refresh that remains usable without Vibe-Trading.

**Architecture:** Extend the existing FastAPI composition with an injectable portfolio subsystem backed by SQLAlchemy Async, SQLite, and Alembic, while preserving the existing Vibe compatibility boundary. Implement fixed-host Eastmoney, Yahoo, and Tencent adapters behind a Sidecar-owned market-data protocol, then serve a React/TypeScript SPA and `/api/v1` from the same loopback origin. Every behavior change follows RED → minimal GREEN → focused regression → commit.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2 Async, aiosqlite, Alembic, httpx; React 19.2.7, React Router 7.18.1, TanStack Query 5.101.2, TypeScript 7.0.2, Vite 8.1.5, Vitest 4.1.10, React Testing Library, Playwright 1.61.1, Node 24 in CI.

**Design:** [`docs/superpowers/specs/2026-07-19-portfolio-experience-webui-design.md`](../specs/2026-07-19-portfolio-experience-webui-design.md)

## Global Constraints

- `/Users/zhaibin/Dev/AInvest` is read-only reference material. Never import from it, modify it, share its storage, or copy Sidecar code into it.
- The portfolio must work when Vibe is stopped. Market search and quote refresh must never call `VibeGateway`.
- Preserve the three existing compatibility operations and their fail-closed behavior; never add Session `mcpServers` or `ALLOW_SESSION_MCP_SERVERS=1`.
- Bind production to `127.0.0.1:8765`, serve SPA and API from one origin, emit no permissive CORS headers, and block non-loopback configuration.
- Support only long A-share, Hong Kong, and US equity/ETF current-position snapshots in CNY, HKD, and USD fixed-currency accounts.
- Never calculate or display a cross-currency total, FX conversion, realized return, transaction history, or trading action.
- Quantity accepts at most 8 fractional digits and must be positive. Average cost and cash accept at most 6 fractional digits and must be non-negative. Accepted quote price has at most 6 fractional digits and must be positive.
- Decimal values cross the API as canonical strings, persist as canonical SQLite text, and calculate with Python `Decimal`; binary float is prohibited for financial values.
- Dashboard loads, startup, health, and compatibility checks perform no market-data network request. Refresh happens only after explicit POST.
- Provider schemes, hosts, paths, response sizes, timeouts, and redirects are code-defined. No custom provider URL is configurable.
- Default tests and E2E perform no internet access. Real market-provider, Vibe runtime, and MCP gates remain separate opt-in checks; skipped means not run.
- Preserve the existing Python coverage gate at 85% and require at least 80% frontend line coverage.
- Preserve user-owned `.codegraph/`, `.cursor/`, and unrelated worktree changes.

---

## Planned File Structure

### Backend and migrations

- Modify `pyproject.toml` and `uv.lock` — lock SQLAlchemy, aiosqlite, and Alembic.
- Modify `src/vibe_portfolio/config.py` — loopback API, database, and bounded provider settings.
- Create `src/vibe_portfolio/portfolio/domain.py` — enums, canonical symbols, exact decimal validation, and domain records.
- Create `src/vibe_portfolio/portfolio/persistence_types.py` — exact decimal and UTC ISO SQLAlchemy types.
- Create `src/vibe_portfolio/portfolio/tables.py` — SQLAlchemy table mappings only.
- Create `src/vibe_portfolio/portfolio/database.py` — safe path checks, migration/backup startup, async engine/session lifecycle.
- Create `src/vibe_portfolio/portfolio/repository.py` — account, instrument, position, quote, refresh, candidate, and idempotency persistence.
- Create `src/vibe_portfolio/portfolio/service.py` — application rules, archive/concurrency/idempotency, and summary calculation.
- Create `src/vibe_portfolio/portfolio/schemas.py` — request/response models and stable errors.
- Create `src/vibe_portfolio/portfolio/router.py` — portfolio HTTP endpoints and dependency extraction.
- Create `src/vibe_portfolio/market_data/models.py` — provider DTOs and error codes.
- Create `src/vibe_portfolio/market_data/protocol.py` — provider interfaces and routing contract.
- Create `src/vibe_portfolio/market_data/http.py` — fixed-host bounded HTTP transport.
- Create `src/vibe_portfolio/market_data/eastmoney.py` — Eastmoney search and CN/HK quote adapter.
- Create `src/vibe_portfolio/market_data/yahoo.py` — Yahoo search and HK/US quote adapter.
- Create `src/vibe_portfolio/market_data/tencent.py` — Tencent CN quote fallback.
- Create `src/vibe_portfolio/market_data/service.py` — merge, confirmation, fallback, refresh locking, validation, and persistence orchestration.
- Create `src/vibe_portfolio/market_data/router.py` — search, refresh, run-status, and provider/cache-status endpoints.
- Create `src/vibe_portfolio/api/security.py` — Host, Origin, Fetch Metadata, body, and response-header policy.
- Create `src/vibe_portfolio/api/static.py` — safe SPA/static routing.
- Modify `src/vibe_portfolio/api/app.py` and `src/vibe_portfolio/api/main.py` — compose lifecycle, routers, OpenAPI, security, and packaged frontend.
- Create `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, and `migrations/versions/20260719_0001_portfolio_snapshot.py` — initial schema.
- Create `scripts/export_openapi.py` — deterministic OpenAPI snapshot generation.

### Backend tests

- Create `tests/portfolio/conftest.py` and focused files under `tests/portfolio/` for domain, migration, repository, service, summary, security, and API behavior.
- Create `tests/market_data/fixtures/` with synthetic provider payloads and focused adapter/orchestration tests.
- Modify `tests/api/test_system_api.py` — scope route invariants to system operations while retaining exact behavior assertions.
- Create `tests/api/test_static_app.py` — OpenAPI, security, assets, and SPA fallback.
- Create `tests/contract/test_live_market_data.py` — explicit opt-in synthetic-symbol provider smoke gate.

### Frontend and E2E

- Create `frontend/package.json`, `frontend/package-lock.json`, TypeScript/Vite/Vitest/ESLint configs, and `frontend/index.html`.
- Create `frontend/src/api/` — generated schema plus a checked fetch client.
- Create `frontend/src/app/` — QueryClient, router, shell, error boundary, and global styles.
- Create `frontend/src/components/` — accessible status, currency, summary, table, form, and allocation components.
- Create `frontend/src/pages/OverviewPage.tsx`, `HoldingsPage.tsx`, and `SettingsPage.tsx` with colocated tests.
- Create `src/vibe_portfolio/web/__init__.py`; Vite builds ignored output to `src/vibe_portfolio/web/dist/` for FastAPI and wheel packaging.
- Create `frontend/playwright.config.ts`, `frontend/e2e/portfolio.spec.ts`, `scripts/e2e_fakes.py`, `scripts/run_e2e_server.py`, and `scripts/run_e2e.py` — two-phase production-build E2E with injected fake providers and a real process restart.
- Modify `.gitignore`, `.github/workflows/ci.yml`, `README.md`, and `docs/handoff/CURRENT.md`.

---

### Task 1: Lock Runtime Configuration and Persistence Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/vibe_portfolio/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.api_origin() -> str`, `Settings.api_origins() -> frozenset[str]`, `Settings.database_path: Path`, and fixed bounded market-data settings used by all later tasks.
- Preserves: `Settings.vibe_*` and `Settings.mcp_*` fields and methods unchanged.

- [ ] **Step 1: Write failing configuration tests**

Add tests that assert the default loopback origin, ignored runtime paths, fixed provider limits, and rejection of non-loopback API hosts:

```python
def test_portfolio_runtime_defaults_are_local_and_bounded() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8765
    assert settings.api_origin() == "http://127.0.0.1:8765"
    assert settings.api_origins() == frozenset({"http://127.0.0.1:8765", "http://localhost:8765"})
    assert settings.database_path == Path("var/data/portfolio.db")
    assert settings.api_max_request_bytes == 64_000
    assert settings.database_busy_timeout_ms == 500
    assert settings.market_connect_timeout_seconds == 3.0
    assert settings.market_read_timeout_seconds == 8.0
    assert settings.market_operation_timeout_seconds == 15.0
    assert settings.market_max_concurrency == 4
    assert settings.market_max_batch_instruments == 500
    assert settings.market_max_response_bytes == 1_000_000


def test_api_host_cannot_be_widened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTFOLIO_API_HOST", "0.0.0.0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_config.py -q`

Expected: FAIL because the new settings and `api_origin()` do not exist.

- [ ] **Step 3: Add dependencies and the minimal settings implementation**

Add runtime constraints:

```toml
"aiosqlite>=0.21,<1",
"alembic>=1.16,<2",
"sqlalchemy>=2.0.41,<3",
```

Add these fields without configurable provider URLs:

```python
api_host: Literal["127.0.0.1"] = "127.0.0.1"
api_port: int = Field(default=8765, ge=1024, le=65535)
database_path: Path = Path("var/data/portfolio.db")
api_max_request_bytes: int = Field(default=64_000, ge=1024, le=1_000_000)
database_busy_timeout_ms: int = Field(default=500, ge=100, le=5_000)
market_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
market_read_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
market_operation_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
market_max_concurrency: int = Field(default=4, ge=1, le=8)
market_max_batch_instruments: int = Field(default=500, ge=1, le=1_000)
market_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)

def api_origin(self) -> str:
    return f"http://{self.api_host}:{self.api_port}"

def api_origins(self) -> frozenset[str]:
    return frozenset({self.api_origin(), f"http://localhost:{self.api_port}"})
```

Run `uv lock` to update the lockfile.

- [ ] **Step 4: Verify GREEN and dependency integrity**

Run: `uv lock --check && uv run pytest tests/test_config.py -q && uv run mypy src/vibe_portfolio/config.py`

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/vibe_portfolio/config.py tests/test_config.py
git commit -m "build: add portfolio persistence foundation"
```

### Task 2: Define Exact Domain Values and Canonical Instrument Identity

**Files:**
- Create: `src/vibe_portfolio/portfolio/__init__.py`
- Create: `src/vibe_portfolio/portfolio/domain.py`
- Create: `tests/portfolio/test_domain.py`

**Interfaces:**
- Produces: `Currency`, `Market`, `AssetType`, `QuoteState`, `CanonicalInstrument`, `parse_quantity`, `parse_money`, `parse_price`, `canonical_symbol`, and `quote_state`.
- Consumes: no database or provider code.

- [ ] **Step 1: Write failing exact-value and symbol tests**

Cover canonical formatting, malformed symbols, positive quantity, non-negative money, the exact `1_000_000_000_000` magnitude ceiling, precision rejection, future timestamps, and derived freshness:

```python
@pytest.mark.parametrize(
    ("code", "market", "expected"),
    [
        ("600519", Market.CN_SH, "600519.SH"),
        ("920001", Market.CN_BJ, "920001.BJ"),
        ("700", Market.HK, "00700.HK"),
        ("aapl", Market.US, "AAPL.US"),
    ],
)
def test_canonical_symbol(code: str, market: Market, expected: str) -> None:
    assert canonical_symbol(code, market) == expected


def test_decimal_precision_is_rejected_not_rounded() -> None:
    with pytest.raises(DomainValidationError, match="quantity_precision"):
        parse_quantity("1.000000001")
    with pytest.raises(DomainValidationError, match="money_precision"):
        parse_money("10.0000001")


def test_quote_state_is_derived_from_latest_attempt() -> None:
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
    assert quote_state(now - timedelta(hours=1), latest_attempt_succeeded=True, now=now) is QuoteState.FRESH
    assert quote_state(now - timedelta(hours=1), latest_attempt_succeeded=False, now=now) is QuoteState.STALE
    assert quote_state(None, latest_attempt_succeeded=False, now=now) is QuoteState.UNAVAILABLE
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/portfolio/test_domain.py -q`

Expected: collection FAIL because `vibe_portfolio.portfolio.domain` does not exist.

- [ ] **Step 3: Implement the minimal domain module**

Use string enums and one exact parser:

```python
class DomainValidationError(ValueError):
    pass


class Currency(StrEnum):
    CNY = "CNY"
    HKD = "HKD"
    USD = "USD"


class Market(StrEnum):
    CN_SH = "CN_SH"
    CN_SZ = "CN_SZ"
    CN_BJ = "CN_BJ"
    HK = "HK"
    US = "US"


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"


class QuoteState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CanonicalInstrument:
    symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType


def _parse_exact(
    value: str | Decimal,
    *,
    scale: int,
    positive: bool,
    maximum: Decimal,
    code: str,
) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise DomainValidationError(f"{code}_invalid") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0) or parsed > maximum:
        raise DomainValidationError(f"{code}_range")
    if -parsed.as_tuple().exponent > scale:
        raise DomainValidationError(f"{code}_precision")
    return parsed


def canonical_symbol(code: str, market: Market) -> str:
    cleaned = code.strip().upper()
    suffix = {Market.CN_SH: "SH", Market.CN_SZ: "SZ", Market.CN_BJ: "BJ", Market.HK: "HK", Market.US: "US"}[market]
    base = cleaned.removesuffix(f".{suffix}")
    if market is Market.HK:
        if not base.isdigit() or not 1 <= len(base) <= 5:
            raise DomainValidationError("symbol_invalid")
        return f"{base.zfill(5)}.HK"
    if market in {Market.CN_SH, Market.CN_SZ, Market.CN_BJ} and not re.fullmatch(r"\d{6}", base):
        raise DomainValidationError("symbol_invalid")
    if market is Market.US and not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", base):
        raise DomainValidationError("symbol_invalid")
    return f"{base}.{suffix}"


def parse_quantity(value: str | Decimal) -> Decimal:
    return _parse_exact(value, scale=8, positive=True, maximum=Decimal("1000000000000"), code="quantity")


def parse_money(value: str | Decimal) -> Decimal:
    return _parse_exact(value, scale=6, positive=False, maximum=Decimal("1000000000000"), code="money")


def parse_price(value: str | Decimal) -> Decimal:
    return _parse_exact(value, scale=6, positive=True, maximum=Decimal("1000000000000"), code="price")


def quote_state(
    as_of: datetime | None,
    *,
    latest_attempt_succeeded: bool,
    now: datetime,
) -> QuoteState:
    if as_of is None:
        return QuoteState.UNAVAILABLE
    if not latest_attempt_succeeded or now - as_of > timedelta(hours=72):
        return QuoteState.STALE
    return QuoteState.FRESH
```

Keep `quote_state()` pure with the approved 72-hour threshold. Task 8 rejects a provider timestamp more than five minutes in the future before it can reach this function.

- [ ] **Step 4: Verify GREEN and strict typing**

Run: `uv run pytest tests/portfolio/test_domain.py -q && uv run ruff check src/vibe_portfolio/portfolio tests/portfolio && uv run mypy src/vibe_portfolio/portfolio`

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/vibe_portfolio/portfolio tests/portfolio/test_domain.py
git commit -m "feat: define exact portfolio domain values"
```

### Task 3: Create the Initial SQLite Schema and Exact Persistence Types

**Files:**
- Create: `src/vibe_portfolio/portfolio/persistence_types.py`
- Create: `src/vibe_portfolio/portfolio/tables.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/20260719_0001_portfolio_snapshot.py`
- Create: `tests/portfolio/conftest.py`
- Create: `tests/portfolio/test_migrations.py`
- Create: `tests/portfolio/test_persistence_types.py`

**Interfaces:**
- Produces: `ExactDecimal`, `UtcIsoDateTime`, SQLAlchemy `Base`, and tables `AccountRow`, `InstrumentRow`, `InstrumentProviderSymbolRow`, `PositionRow`, `LatestQuoteRow`, `QuoteRefreshRunRow`, `QuoteRefreshItemRow`, `InstrumentCandidateRow`, and `IdempotencyRow`.
- Consumes: domain enums and exact decimal parsers from Task 2.

- [ ] **Step 1: Write failing type round-trip and migration tests**

The tests must prove SQLite stores decimal text, timezone-aware UTC values return aware, foreign keys are enabled, and the partial unique index permits archived duplicates but rejects two active positions:

```python
async def test_exact_decimal_is_stored_as_text(async_engine: AsyncEngine) -> None:
    async with async_engine.begin() as connection:
        table = Table("exact_values", MetaData(), Column("value", ExactDecimal(), nullable=False))
        await connection.run_sync(table.create)
        await connection.execute(table.insert().values(value=Decimal("1.230000")))
        stored = (await connection.execute(text("select typeof(value), value from exact_values"))).one()
    assert stored == ("text", "1.230000")


def test_initial_migration_creates_all_snapshot_tables(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {"accounts", "instruments", "positions", "latest_quotes", "quote_refresh_runs", "instrument_candidates", "idempotency_records"} <= tables
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/portfolio/test_persistence_types.py tests/portfolio/test_migrations.py -q`

Expected: collection FAIL because persistence modules and migration entry point do not exist.

- [ ] **Step 3: Implement exact SQLAlchemy types and mapped tables**

The decimal type must bind canonical text and never call `float()`:

```python
class ExactDecimal(TypeDecorator[Decimal]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        del dialect
        return None if value is None else format(value, "f")

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        del dialect
        return None if value is None else Decimal(value)
```

Define explicit `CheckConstraint` values for currency, market, asset type, refresh status, and archive timestamps. Define a SQLite partial unique index:

```python
Index(
    "uq_positions_active_account_instrument",
    PositionRow.account_id,
    PositionRow.instrument_id,
    unique=True,
    sqlite_where=PositionRow.archived_at.is_(None),
)
```

Map these exact columns:

- `accounts`: `id`, `name`, `normalized_name`, `currency`, nullable `cash_balance`, `version`, `created_at`, `updated_at`, nullable `archived_at`; active `normalized_name` is unique.
- `instruments`: `id`, unique `canonical_symbol`, `name`, `market`, `currency`, `asset_type`, `created_at`, `updated_at`.
- `instrument_provider_symbols`: `instrument_id`, `provider`, `provider_symbol`; both `(instrument_id, provider)` and `(provider, provider_symbol)` are unique.
- `positions`: `id`, `account_id`, `instrument_id`, `quantity`, `average_cost`, nullable `note`, `version`, `created_at`, `updated_at`, nullable `archived_at`.
- `latest_quotes`: primary-key `instrument_id`, `price`, `currency`, `provider`, `provider_symbol`, `as_of`, `fetched_at`, `refresh_run_id`.
- `quote_refresh_runs`: `id`, canonical scope hash, `status`, `started_at`, nullable `finished_at`, `updated_count`, `stale_count`, `unavailable_count`.
- `quote_refresh_items`: `run_id`, `instrument_id`, `outcome`, nullable `provider`, nullable stable `error_code`, `created_at`; `(run_id, instrument_id)` is unique.
- `instrument_candidates`: `id`, canonical validated fields, serialized provider-symbol mappings, `created_at`, `expires_at`, nullable `consumed_at`.
- `idempotency_records`: `scope`, SHA-256 `key_hash`, canonical `request_hash`, `state`, nullable `resource_id`, nullable `response_status`, `created_at`, `expires_at`; `(scope, key_hash)` is unique.

Store UUIDs as canonical 36-character strings and UTC timestamps using `UtcIsoDateTime(Text)`. Candidate rows expire after 15 minutes, idempotency rows after 24 hours, refresh-item detail after 90 days, and refresh-run summaries after 365 days; schema columns carry the timestamps needed for bounded pruning.

- [ ] **Step 4: Write and execute the reviewed initial migration**

The migration must create every mapped table, foreign key, check, partial unique index, schema-version marker, and bounded indexes used by account/position/refresh queries. Do not use `alembic revision --autogenerate` as the final artifact without reviewing its exact SQL.

Run: `uv run alembic upgrade head && uv run alembic current`

Expected: current revision is `20260719_0001` against an ignored local `var/data/portfolio.db`.

- [ ] **Step 5: Verify GREEN from a clean temporary database**

Run: `uv run pytest tests/portfolio/test_persistence_types.py tests/portfolio/test_migrations.py -q && uv run ruff check migrations src/vibe_portfolio/portfolio tests/portfolio && uv run mypy src/vibe_portfolio/portfolio`

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini migrations src/vibe_portfolio/portfolio/persistence_types.py src/vibe_portfolio/portfolio/tables.py tests/portfolio
git commit -m "feat: add portfolio snapshot schema"
```

### Task 4: Add Safe Database Startup, Integrity Checks, and Migration Backup

**Files:**
- Create: `src/vibe_portfolio/portfolio/database.py`
- Create: `tests/portfolio/test_database.py`
- Modify: `tests/portfolio/conftest.py`

**Interfaces:**
- Produces: `Database(path: Path, busy_timeout_ms: int)`, `Database.start()`, `Database.close()`, `Database.session()`, `upgrade_database(path)`, and stable `DatabaseStartupError.code`/`DatabaseBusyError.code`.
- Consumes: Alembic revision and `Base` from Task 3.

- [ ] **Step 1: Write failing startup-safety tests**

Test new database creation, parent mode, symlink rejection, corrupt integrity failure, future revision rejection, pre-migration SQLite backup, failed migration preservation, and timezone/foreign-key PRAGMAs. Use only `tmp_path` targets:

```python
async def test_database_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    real.touch()
    link = tmp_path / "linked.db"
    link.symlink_to(real)
    with pytest.raises(DatabaseStartupError, match="DATABASE_PATH_UNSAFE"):
        await Database(link).start()


def test_upgrade_creates_verified_backup_before_schema_change(tmp_path: Path) -> None:
    path = legacy_database_fixture(tmp_path)
    result = upgrade_database(path)
    assert result.backup_path is not None
    assert sqlite_integrity(result.backup_path) == "ok"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/portfolio/test_database.py -q`

Expected: collection FAIL because `Database` is undefined.

- [ ] **Step 3: Implement the startup sequence**

Use this exact order:

```python
async def start(self) -> None:
    validate_database_path(self.path)
    await asyncio.to_thread(upgrade_database, self.path)
    self.engine = create_async_engine(sqlite_async_url(self.path), pool_pre_ping=True)
    event.listen(self.engine.sync_engine, "connect", enable_sqlite_pragmas)
    async with self.engine.connect() as connection:
        await connection.execute(text("select 1"))
```

`upgrade_database()` must inspect the schema, run `PRAGMA integrity_check`, use `sqlite3.Connection.backup()` into a timestamped sibling file before any needed migration, run Alembic, verify the resulting revision/integrity, and leave the original plus backup intact on failure. Create the runtime directory with mode `0o700` and database/backup files with mode `0o600` where supported. Every SQLite connection sets `foreign_keys=ON`, `journal_mode=WAL`, and the configured 500 ms `busy_timeout`; after that bound, translate lock exhaustion to `DatabaseBusyError("DATABASE_BUSY")`.

- [ ] **Step 4: Verify GREEN and lock failure semantics**

Run: `uv run pytest tests/portfolio/test_database.py -q`

Expected: all database startup tests pass, including bounded `DATABASE_BUSY` mapping after a deliberately held SQLite write lock.

- [ ] **Step 5: Commit**

```bash
git add src/vibe_portfolio/portfolio/database.py tests/portfolio/conftest.py tests/portfolio/test_database.py
git commit -m "feat: fail closed on unsafe portfolio storage"
```

### Task 5: Implement Accounts, Idempotency, and Optimistic Concurrency APIs

**Files:**
- Create: `src/vibe_portfolio/portfolio/repository.py`
- Create: `src/vibe_portfolio/portfolio/schemas.py`
- Create: `src/vibe_portfolio/portfolio/service.py`
- Create: `src/vibe_portfolio/portfolio/router.py`
- Create: `tests/portfolio/test_accounts_api.py`
- Create: `tests/portfolio/test_idempotency.py`

**Interfaces:**
- Produces: `PortfolioRepository`, `PortfolioService.create_account`, `PortfolioService.update_account`, `build_portfolio_router(service)`, cursor `GET /api/v1/accounts`, POST/PATCH account routes, and `api_error(code, status, fields)`.
- Consumes: `Database.session()`, `AccountRow`, `IdempotencyRow`, exact money parser.

- [ ] **Step 1: Write account API tests before registering routes**

Use an app fixture with a temporary migrated database and fake Vibe services. Assert decimal strings, normalized duplicate names, unknown cash, idempotent replay across a service restart, key/body conflict, stale version, archive blocked by active positions, pagination, and sanitized database failures:

```python
async def test_create_account_replays_same_idempotency_key(client: AsyncClient) -> None:
    headers = {"Idempotency-Key": "account-create-1", "Origin": "http://127.0.0.1:8765"}
    body = {"name": "港股账户", "currency": "HKD", "cash_balance": None}
    first = await client.post("/api/v1/accounts", json=body, headers=headers)
    second = await client.post("/api/v1/accounts", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


async def test_patch_rejects_stale_version(client: AsyncClient, account: dict[str, object]) -> None:
    response = await client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"version": 0, "name": "已过期修改"},
        headers={"Idempotency-Key": "stale-edit", "Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
```

- [ ] **Step 2: Run account tests and verify RED**

Run: `uv run pytest tests/portfolio/test_accounts_api.py tests/portfolio/test_idempotency.py -q`

Expected: FAIL because account routes are absent.

- [ ] **Step 3: Implement schemas, repository operations, and service rules**

Define `AccountCreate`, `AccountPatch`, `AccountView`, `CursorPage[AccountView]`, and a stable error envelope. Normalize account names with Unicode NFKC plus collapsed whitespace, require 1..80 characters, and make the normalized active value unique. API financial inputs must be JSON strings; use separate annotated types whose `BeforeValidator` rejects non-string JSON, delegates to `parse_money`/`parse_quantity`/`parse_price`, and whose `PlainSerializer(lambda value: format(value, "f"), return_type=str)` plus JSON Schema declares `type: string`. Validate `Idempotency-Key` as 8..128 visible ASCII characters before hashing. The service sequence for every POST/PATCH is:

```python
async with self.database.session() as session, session.begin():
    replay = await self.repository.claim_idempotency(session, scope, key, canonical_request_hash(payload))
    if replay.completed:
        return replay.response
    account = await self.repository.insert_or_update_account(session, command)
    await self.repository.complete_idempotency(session, replay, account)
return account
```

Hash the key and canonical request; never persist the raw key or request body. Compare `UPDATE ... WHERE id=:id AND version=:version`, increment `version`, and check affected row count to produce 409 without lost updates.

- [ ] **Step 4: Register the account router and verify GREEN**

Build a router with `build_portfolio_router(service: PortfolioService) -> APIRouter`; close over the injected service rather than reading a global singleton. Register this router in the focused test app. Run:

`uv run pytest tests/portfolio/test_accounts_api.py tests/portfolio/test_idempotency.py -q`

Expected: all account and retry tests pass.

- [ ] **Step 5: Run focused quality gates**

Run: `uv run ruff check src/vibe_portfolio/portfolio tests/portfolio && uv run mypy src/vibe_portfolio/portfolio`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/vibe_portfolio/portfolio tests/portfolio/test_accounts_api.py tests/portfolio/test_idempotency.py
git commit -m "feat: add exact account management api"
```

### Task 6: Implement Instrument Confirmation and Position Snapshot APIs

**Files:**
- Modify: `src/vibe_portfolio/portfolio/repository.py`
- Modify: `src/vibe_portfolio/portfolio/schemas.py`
- Modify: `src/vibe_portfolio/portfolio/service.py`
- Modify: `src/vibe_portfolio/portfolio/router.py`
- Create: `tests/portfolio/test_instruments_api.py`
- Create: `tests/portfolio/test_positions_api.py`

**Interfaces:**
- Produces: `cache_candidates`, `confirm_instrument`, position list/create/update/archive, and `POST /api/v1/instruments/confirm`.
- Temporarily consumes: test-created `InstrumentCandidate` records; Task 9 connects live search providers to the same method.

- [ ] **Step 1: Write failing confirmation and position tests**

Cover expired candidate, tampered candidate ID, allowed equity/ETF types, account/instrument currency mismatch, duplicate active position, archive/recreate, exact precision, account currency immutability, and optimistic concurrency:

```python
async def test_confirmed_candidate_is_required(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/positions",
        json={"account_id": ACCOUNT_ID, "instrument_id": UNKNOWN_ID, "quantity": "10", "average_cost": "12.34"},
        headers=write_headers("position-1"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_CONFIRMED"


async def test_position_currency_must_match_account(client: AsyncClient, cny_account: dict[str, object], usd_instrument: dict[str, object]) -> None:
    response = await create_position(client, cny_account, usd_instrument)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CURRENCY_MISMATCH"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/portfolio/test_instruments_api.py tests/portfolio/test_positions_api.py -q`

Expected: FAIL because instrument confirmation and position routes are absent.

- [ ] **Step 3: Implement server-side candidate confirmation**

`confirm_instrument(candidate_id, key)` must lock and read one unexpired candidate, revalidate its canonical symbol/market/currency/type, upsert instrument plus provider mapping, mark the candidate consumed, and complete idempotency in one transaction. The request accepts only `candidate_id`; it never accepts browser-supplied name, market, currency, or provider symbol.

- [ ] **Step 4: Implement current-position rules and endpoints**

Use `PositionCreate(account_id, instrument_id, quantity, average_cost, note)` and `PositionPatch(version, quantity?, average_cost?, note?, archived?)`. Normalize notes to plain Unicode text, reject control characters, and cap them at 500 characters. Reject shorts, currency mismatch, unsupported types, archived parents, and two active rows for the same pair. Cursor-paginate active and archived lists. Allow restore only when it would not violate the active pair constraint. Map SQLite uniqueness errors to `DUPLICATE_POSITION`, not a 500.

- [ ] **Step 5: Verify GREEN and persistence after restart**

Run: `uv run pytest tests/portfolio/test_instruments_api.py tests/portfolio/test_positions_api.py -q`

Expected: all tests pass, including close/reopen of `Database` with the same temporary path.

- [ ] **Step 6: Commit**

```bash
git add src/vibe_portfolio/portfolio tests/portfolio/test_instruments_api.py tests/portfolio/test_positions_api.py
git commit -m "feat: add confirmed position snapshots"
```

### Task 7: Implement Currency-Separated Summary and Freshness Semantics

**Files:**
- Modify: `src/vibe_portfolio/portfolio/repository.py`
- Modify: `src/vibe_portfolio/portfolio/schemas.py`
- Modify: `src/vibe_portfolio/portfolio/service.py`
- Modify: `src/vibe_portfolio/portfolio/router.py`
- Create: `tests/portfolio/test_summary.py`
- Create: `tests/portfolio/test_summary_api.py`

**Interfaces:**
- Produces: pure `calculate_summary(...) -> PortfolioSummary`, `PortfolioService.summary(currency, now) -> PortfolioSummary`, and `GET /api/v1/portfolio/summary?currency=...`.
- Consumes: active accounts/positions and latest quote plus latest refresh outcome records.

- [ ] **Step 1: Write deterministic summary tests**

Use a fixed aware clock. Cover exact value/cost/P&L, zero cost percentage omission, cash unknown versus zero, stale value included with estimated label, unavailable value excluded, allocation denominator, archived exclusion, and three independent currencies:

```python
def test_summary_excludes_unavailable_value_but_exposes_cost() -> None:
    summary = calculate_summary(
        currency=Currency.CNY,
        accounts=[account(cash=None)],
        positions=[position(quantity="10", average_cost="8")],
        quotes={},
        latest_attempts={},
        now=FIXED_NOW,
    )
    assert summary.market_value == Decimal("0")
    assert summary.position_cost == Decimal("80")
    assert summary.unvalued_cost == Decimal("80")
    assert summary.unvalued_count == 1
    assert summary.estimated is True
    assert summary.known_cash == Decimal("0")
    assert summary.unknown_cash_account_count == 1
```

- [ ] **Step 2: Run summary tests and verify RED**

Run: `uv run pytest tests/portfolio/test_summary.py tests/portfolio/test_summary_api.py -q`

Expected: FAIL because summary functions and route are absent.

- [ ] **Step 3: Implement pure Decimal calculation before HTTP wiring**

Calculate in Python from repository records. `position_cost` includes every active position, while `valued_position_cost` includes only positions with a stale/fresh quote; unrealized P&L and its percentage use `valued_position_cost`, never unavailable cost. Sum every entered cash value into `known_cash`, count omitted balances in `unknown_cash_account_count`, include known cash in the total, and force `estimated=True` when the unknown count is non-zero. Quantize only response-display fields using named helpers; do not run SQL `SUM()` over decimal text. Derive quote state at read time using `as_of`, the most recent per-instrument refresh outcome, and the injected clock.

- [ ] **Step 4: Add the SQLite-only summary endpoint**

The handler must call only `PortfolioService.summary`; assert in tests that the fake market provider has zero calls on dashboard reads. Serialize every decimal as a canonical string and every timestamp with timezone.

- [ ] **Step 5: Verify GREEN**

Run: `uv run pytest tests/portfolio/test_summary.py tests/portfolio/test_summary_api.py -q`

Expected: all summary tests pass with no network fixture installed.

- [ ] **Step 6: Commit**

```bash
git add src/vibe_portfolio/portfolio tests/portfolio/test_summary.py tests/portfolio/test_summary_api.py
git commit -m "feat: calculate currency separated portfolio summaries"
```

### Task 8: Build the Fixed-Host Market-Data Contract and Safe HTTP Transport

**Files:**
- Create: `src/vibe_portfolio/market_data/__init__.py`
- Create: `src/vibe_portfolio/market_data/models.py`
- Create: `src/vibe_portfolio/market_data/protocol.py`
- Create: `src/vibe_portfolio/market_data/http.py`
- Create: `tests/market_data/test_models.py`
- Create: `tests/market_data/test_http.py`

**Interfaces:**
- Produces: `ProviderSymbol`, `InstrumentCandidate`, `ProviderInstrument`, `ProviderQuote`, `ProviderErrorCode`, `ProviderFailure`, `RefreshScope`, `RefreshResult`, `validate_quote`, `MarketDataProvider.search`, `MarketDataProvider.fetch_quotes`, and `BoundedProviderHttp.get_json`/`get_text`.
- Consumes: domain currency, market, asset type, canonical symbol, and decimal validators.

- [ ] **Step 1: Write failing provider validation and transport tests**

Use `httpx.MockTransport` plus an injected monotonic clock to test exact-host allowlisting, HTTPS-only URLs, redirect rejection, status mapping, timeout mapping, per-host minimum spacing, content-length and streamed-size limits, JSON/charset errors, response sanitization, and a five-minute future timestamp rejection:

```python
async def test_transport_rejects_unknown_host() -> None:
    transport = BoundedProviderHttp(allowed_hosts={"query1.finance.yahoo.com"}, client=fake_client())
    with pytest.raises(ProviderFailure, match="PROVIDER_DESTINATION_BLOCKED"):
        await transport.get_json("https://attacker.example/quote")


def test_provider_quote_rejects_currency_mismatch() -> None:
    with pytest.raises(ProviderFailure, match="QUOTE_CURRENCY_MISMATCH"):
        validate_quote(provider_quote(currency=Currency.USD), instrument(currency=Currency.HKD), now=FIXED_NOW)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/market_data/test_models.py tests/market_data/test_http.py -q`

Expected: collection FAIL because market-data modules do not exist.

- [ ] **Step 3: Define immutable provider DTOs and protocols**

Use frozen dataclasses and exact signatures:

```python
class ProviderErrorCode(StrEnum):
    DESTINATION_BLOCKED = "PROVIDER_DESTINATION_BLOCKED"
    TIMEOUT = "PROVIDER_TIMEOUT"
    RESPONSE_TOO_LARGE = "PROVIDER_RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "QUOTE_RESPONSE_INVALID"
    CURRENCY_MISMATCH = "QUOTE_CURRENCY_MISMATCH"


class ProviderFailure(RuntimeError):
    def __init__(self, code: ProviderErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderSymbol:
    provider: str
    symbol: str


@dataclass(frozen=True, slots=True)
class InstrumentCandidate:
    canonical_symbol: str
    name: str
    market: Market
    currency: Currency
    asset_type: AssetType
    provider_symbols: tuple[ProviderSymbol, ...]
    candidate_id: UUID | None = None

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(mapping.provider for mapping in self.provider_symbols)


@dataclass(frozen=True, slots=True)
class ProviderInstrument:
    canonical_symbol: str
    provider_symbol: str
    market: Market
    currency: Currency
    asset_type: AssetType


@dataclass(frozen=True, slots=True)
class ProviderQuote:
    canonical_symbol: str
    provider_symbol: str
    price: Decimal
    currency: Currency
    as_of: datetime
    provider: str


@dataclass(frozen=True, slots=True)
class RefreshScope:
    instrument_ids: tuple[UUID, ...] | None

    @classmethod
    def all(cls) -> "RefreshScope":
        return cls(instrument_ids=None)


@dataclass(frozen=True, slots=True)
class RefreshResult:
    run_id: UUID
    status: Literal["succeeded", "partial", "failed"]
    updated: int
    stale: int
    unavailable: int


class MarketDataProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int) -> list[InstrumentCandidate]: ...

    async def fetch_quotes(self, instruments: Sequence[ProviderInstrument]) -> list[ProviderQuote]: ...
```

`validate_quote(quote, instrument, now)` returns the quote only after exact identity/currency/type/range/precision/timestamp checks or raises `ProviderFailure` with a stable code. Validation rejects unknown extras at parser boundaries before DTO construction.

- [ ] **Step 4: Implement bounded HTTP streaming**

Construct `httpx.AsyncClient` with explicit connect/read/write/pool timeouts, `follow_redirects=False`, `trust_env=False`, TLS verification enabled, one fixed User-Agent, and no cookies. Validate `urlsplit(url).scheme == "https"`, exact lowercase hostname, and a path prefix from that host's immutable `HostPolicy` before the request. Associate each allowed host with a code-defined minimum interval (Eastmoney 1.0 seconds, Yahoo 0.6 seconds, Tencent 0.5 seconds); serialize that host's starts with an `asyncio.Lock` and injected monotonic clock so concurrent adapters cannot burst it. Stream decoded bytes and abort once `market_max_response_bytes` is exceeded. Decode JSON from bounded UTF-8 text with `json.loads(text, parse_float=Decimal, parse_int=int)` rather than `response.json()` so a provider number never becomes a binary float; decode Tencent text only with the adapter's fixed GB18030 setting. Never include URL query, body, or exception text in a persisted error.

- [ ] **Step 5: Verify GREEN and no Vibe dependency**

Run: `uv run pytest tests/market_data/test_models.py tests/market_data/test_http.py -q && rg -n 'vibe_portfolio\.vibe|VibeGateway' src/vibe_portfolio/market_data`

Expected: tests pass and `rg` returns no matches.

- [ ] **Step 6: Commit**

```bash
git add src/vibe_portfolio/market_data tests/market_data
git commit -m "feat: define bounded market data adapters"
```

### Task 9: Implement Eastmoney and Yahoo Search with Trusted Confirmation Cache

**Files:**
- Create: `src/vibe_portfolio/market_data/eastmoney.py`
- Create: `src/vibe_portfolio/market_data/yahoo.py`
- Create: `src/vibe_portfolio/market_data/service.py`
- Create: `src/vibe_portfolio/market_data/router.py`
- Create: `tests/market_data/fixtures/eastmoney_search.json`
- Create: `tests/market_data/fixtures/yahoo_search.json`
- Create: `tests/market_data/test_eastmoney.py`
- Create: `tests/market_data/test_yahoo.py`
- Create: `tests/market_data/test_search_service.py`
- Create: `tests/portfolio/test_search_api.py`

**Interfaces:**
- Produces: `MarketDataService.search(query, limit)`, `build_market_data_router(service)`, `GET /api/v1/instruments/search`, and durable short-lived candidate IDs consumed by Task 6 confirmation.
- Consumes: fixed-host transport, `PortfolioRepository.cache_candidates`, and canonical identity.

- [ ] **Step 1: Add synthetic fixture tests before adapters**

Fixtures must contain only invented names/symbols and the minimum provider shape. Test Eastmoney `QuotationCodeTable.Data` parsing for market IDs `1`, `0`, `116`, `105`, `106`, `107`; Yahoo `quotes` parsing for bare US, four-digit HK, ETF/equity filters; duplicate merge provenance; provider partial failure; NFKC query normalization; accepted Unicode letters/numbers plus space, dot, hyphen, ampersand, and slash; rejection of control/URL metacharacters; length 1..80; limit 1..25; and candidate expiry.

```python
async def test_search_merges_same_symbol_and_records_provenance(search_service: MarketDataService) -> None:
    results = await search_service.search("DEMO", limit=10)
    assert [item.canonical_symbol for item in results] == ["DEMO.US"]
    assert results[0].sources == ("eastmoney", "yahoo")
    assert results[0].candidate_id
```

- [ ] **Step 2: Run search tests and verify RED**

Run: `uv run pytest tests/market_data/test_eastmoney.py tests/market_data/test_yahoo.py tests/market_data/test_search_service.py tests/portfolio/test_search_api.py -q`

Expected: FAIL because provider parsers and search service are absent.

- [ ] **Step 3: Implement Eastmoney search independently**

Use fixed `https://searchapi.eastmoney.com/api/suggest/get` with params `input`, `type=14`, and bounded `count`. Map `QuoteID`/`MktNum` only through the reviewed market table; distinguish Eastmoney market `0` Beijing codes (`4*`, `8*`, and `92*`) from Shenzhen instead of collapsing both to `.SZ`; pad HK to five digits; allow only equity/ETF candidates with a known CNY/HKD/USD mapping. Do not copy AInvest code or error strings.

- [ ] **Step 4: Implement Yahoo search independently**

Use fixed `https://query2.finance.yahoo.com/v1/finance/search` with bounded `q` and quote count. Normalize bare US equity/ETF symbols to `.US` and Yahoo `0700.HK` to `00700.HK`. Drop crypto, index, FX, option, fund types outside ETF, and unknown currencies/markets.

- [ ] **Step 5: Implement bounded parallel merge and candidate cache**

Run the two provider searches under `asyncio.TaskGroup` with per-provider exception capture, normalize by canonical symbol, keep deterministic Eastmoney-then-Yahoo rank, union provenance, cap the final result, and persist only validated fields with a 15-minute expiration. Return `MARKET_SEARCH_UNAVAILABLE` only when both providers fail; an empty successful search is a 200 with zero results.

- [ ] **Step 6: Verify GREEN**

Run the focused search command from Step 2 again.

Expected: all tests pass and no real network request occurs.

- [ ] **Step 7: Commit**

```bash
git add src/vibe_portfolio/market_data tests/market_data tests/portfolio/test_search_api.py
git commit -m "feat: add confirmed instrument search"
```

### Task 10: Implement Quote Adapters, Fallback Routing, and Explicit Refresh

**Files:**
- Modify: `src/vibe_portfolio/market_data/eastmoney.py`
- Modify: `src/vibe_portfolio/market_data/yahoo.py`
- Create: `src/vibe_portfolio/market_data/tencent.py`
- Modify: `src/vibe_portfolio/market_data/service.py`
- Modify: `src/vibe_portfolio/market_data/router.py`
- Modify: `src/vibe_portfolio/portfolio/repository.py`
- Modify: `src/vibe_portfolio/portfolio/schemas.py`
- Create: `tests/market_data/fixtures/eastmoney_quote.json`
- Create: `tests/market_data/fixtures/yahoo_chart.json`
- Create: `tests/market_data/fixtures/tencent_quote.txt`
- Create: `tests/market_data/test_refresh_service.py`
- Create: `tests/portfolio/test_refresh_api.py`

**Interfaces:**
- Produces: `ProviderRegistry`, `build_live_provider_registry(settings)`, `MarketDataService.refresh(scope, idempotency_key)`, POST refresh route, GET refresh-run route, `LatestQuoteRow`, and per-instrument run outcomes.
- Consumes: active canonical instruments and provider mappings from earlier tasks.

- [ ] **Step 1: Write adapter and orchestration tests before quote code**

Cover exact parsing and routing:

- Eastmoney fixed `https://push2.eastmoney.com/api/qt/stock/get`, `secid`, fields `f43,f57,f58,f59,f86`, and `price = Decimal(f43) / (Decimal(10) ** int(f59))`.
- Yahoo fixed `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`, `interval=1m`, `range=1d`, with `meta.regularMarketPrice`, `meta.regularMarketTime`, `meta.currency`, and `meta.instrumentType`.
- Tencent fixed `https://qt.gtimg.cn/q={symbol}` parsing GB18030 text, field 3 price, and field 30 aware market-local timestamp.
- Shanghai/Shenzhen route Eastmoney → Tencent, Beijing route Eastmoney only, HK route Yahoo → Eastmoney, US route Yahoo only.
- Valid primary prevents fallback; missing/invalid primary triggers fallback only for that instrument.
- A partial success commits valid quotes and preserves failed instruments' last valid quotes as stale.
- All failure creates a failed run; concurrent refresh returns `QUOTE_REFRESH_IN_PROGRESS`; dashboard GET never refreshes.

```python
async def test_partial_refresh_preserves_last_valid_quote(refresh_service: MarketDataService) -> None:
    result = await refresh_service.refresh(scope=RefreshScope.all(), idempotency_key="refresh-2")
    assert result.updated == 1
    assert result.stale == 1
    assert await quote_price("STALE.US") == Decimal("42.10")
    assert await latest_outcome("STALE.US") == "stale"
```

- [ ] **Step 2: Run quote/refresh tests and verify RED**

Run: `uv run pytest tests/market_data/test_eastmoney.py tests/market_data/test_yahoo.py tests/market_data/test_refresh_service.py tests/portfolio/test_refresh_api.py -q`

Expected: FAIL because quote parsers and refresh endpoints do not exist.

- [ ] **Step 3: Implement exact quote parsers**

Parse provider JSON/text into `ProviderQuote` and immediately validate canonical identity, expected currency/type, positive finite Decimal, six-digit precision, aware timestamp, and future tolerance. Eastmoney/Yahoo epoch seconds become aware UTC; Tencent field 30 is parsed in `Asia/Shanghai` and converted to UTC. Do not use provider floats as financial values: the bounded JSON decoder already returns `Decimal`, and Tencent substrings go directly to `Decimal`.

- [ ] **Step 4: Implement routing and bounded refresh orchestration**

Accept `RefreshRequest(instrument_ids: list[UUID] | None)` where `None` snapshots every active instrument and an explicit list must be unique, active, and no longer than `market_max_batch_instruments`. Group that immutable snapshot by route, limit tasks with `asyncio.Semaphore(settings.market_max_concurrency)`, wrap the entire operation with `asyncio.timeout(settings.market_operation_timeout_seconds)`, and use a single process lock plus a database in-progress row. Fallback receives only missing/failed instruments. Each instrument ends as `updated`, `stale`, or `unavailable`; an empty active scope completes successfully with zero counts and no provider call.

Before accepting a new run, mark any prior `running` row from an earlier process as `failed` with `REFRESH_ABANDONED`; do not alter its last valid quotes. A partial provider failure returns HTTP 200 with run status `partial`. If every requested provider attempt fails, complete the run as `failed` and return `502 QUOTE_UNAVAILABLE` with the sanitized run summary. A replay using the same completed idempotency key returns that original run instead of competing for the refresh lock.

- [ ] **Step 5: Commit run and valid latest quotes atomically**

In one database transaction, upsert only accepted `LatestQuoteRow` values, insert every sanitized refresh item, complete the run counts/timestamps, and complete idempotency. Provider error detail is a stable enum only. At startup and after explicit writes, prune at most 1,000 expired candidate/idempotency/refresh-detail rows per transaction using the 15-minute/24-hour/90-day/365-day policy; do not create a background scheduler. A transaction failure must preserve all prior quotes and leave the run recoverably failed on the next startup audit.

- [ ] **Step 6: Verify GREEN and serialization**

Run: `uv run pytest tests/market_data tests/portfolio/test_refresh_api.py tests/portfolio/test_summary.py -q`

Expected: all tests pass; response prices are strings; default tests issue zero internet requests.

- [ ] **Step 7: Commit**

```bash
git add src/vibe_portfolio/market_data src/vibe_portfolio/portfolio tests/market_data tests/portfolio/test_refresh_api.py
git commit -m "feat: refresh independent portfolio quotes"
```

### Task 11: Compose Security, OpenAPI, and Safe SPA Serving Without Regressing Compatibility

**Files:**
- Create: `src/vibe_portfolio/api/security.py`
- Create: `src/vibe_portfolio/api/static.py`
- Create: `src/vibe_portfolio/web/__init__.py`
- Modify: `src/vibe_portfolio/api/app.py`
- Modify: `src/vibe_portfolio/api/main.py`
- Modify: `tests/api/test_system_api.py`
- Create: `tests/api/test_security.py`
- Create: `tests/api/test_static_app.py`
- Create: `scripts/export_openapi.py`

**Interfaces:**
- Produces: fully composed `create_app`, `/api/v1/openapi.json`, `web_dist_path()`, security middleware, GET-only SPA fallback, and production use of `Settings.api_host/api_port`.
- Preserves: exact system status/compatibility/probe response semantics and gateway close lifecycle.

- [ ] **Step 1: Write failing route and security tests**

Test allowed Host, rejected attacker Host, exact Origin required for POST/PATCH, same-origin `Sec-Fetch-Site`, JSON-only write bodies, request-size limit, CSP/security headers, API `no-store`, hashed-asset immutable cache, unknown API JSON 404, missing asset 404, known SPA route HTML, non-GET fallback 405/404, and traversal rejection.

Update all existing `TestClient` and `httpx.AsyncClient` instances for the composed app to use `http://127.0.0.1:8765` instead of the framework defaults `testserver` or `sidecar`, so the production Host policy is exercised rather than bypassed.

Update the old route test to assert the system subset exactly:

```python
def test_system_operations_remain_exactly_the_approved_three() -> None:
    operations = system_operations(create_full_test_app().openapi())
    assert operations == {
        ("/api/v1/system/status", "get"),
        ("/api/v1/system/compatibility", "get"),
        ("/api/v1/system/compatibility/mcp-probe", "post"),
    }
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/api/test_system_api.py tests/api/test_security.py tests/api/test_static_app.py -q`

Expected: new security/static tests fail while existing system behavior remains green.

- [ ] **Step 3: Implement middleware and response policy**

Use Starlette `TrustedHostMiddleware` plus a custom middleware that checks `Origin in settings.api_origins()` and `Sec-Fetch-Site in {None, "same-origin"}` for state-changing browser requests, streams/limits body size, and adds the exact CSP and headers from the design. Require JSON content type for portfolio POST/PATCH endpoints; preserve the existing zero-body `/api/v1/system/compatibility/mcp-probe` contract while still applying Host and cross-site Origin checks to it. Do not add CORS middleware.

- [ ] **Step 4: Compose portfolio lifespan and routes**

Extend `AppServices` with optional independent fields `database: Database | None`, `portfolio: PortfolioService | None`, `market_data: MarketDataService | None`, and `static_dir: Path | None`. Production `build_services()` constructs them and obtains `static_dir` from `vibe_portfolio.web.web_dist_path()`, which resolves relative to the installed Python package rather than the working directory; diagnostic-only tests retain their current two required fakes and receive `None` defaults. `create_app()` includes `build_portfolio_router(configured.portfolio)` and `build_market_data_router(configured.market_data)` only when present. In the lifespan, start the database before yield, close provider HTTP clients and database on exit, and always close the existing Vibe gateway. Publish OpenAPI at `/api/v1/openapi.json`; keep Swagger and ReDoc disabled. `api.main.main()` must validate `web_dist_path()/index.html` before starting production Uvicorn and must use `Settings.api_host/api_port`.

- [ ] **Step 5: Implement safe static resolution**

Serve `/assets/` from an injected directory. Serve `index.html` for `/`, `/holdings`, `/settings`, and GET-only extensionless client paths only. Return JSON 404 for any `/api/` path before fallback. Reject missing dot-containing asset paths and resolved paths outside the static root.

- [ ] **Step 6: Export and verify the OpenAPI contract**

`scripts/export_openapi.py` must construct a full app with test-safe injected services, sort JSON keys, create the `frontend/` parent when absent, write `frontend/openapi.json`, and make repeated runs byte-identical.

Run: `uv run pytest tests/api/test_system_api.py tests/api/test_security.py tests/api/test_static_app.py -q && uv run python scripts/export_openapi.py`

Expected: tests pass and `frontend/openapi.json` is created deterministically.

- [ ] **Step 7: Commit**

```bash
git add src/vibe_portfolio/api src/vibe_portfolio/web src/vibe_portfolio/api/main.py tests/api scripts/export_openapi.py frontend/openapi.json
git commit -m "feat: serve secure same origin portfolio app"
```

### Task 12: Scaffold the Locked React Application and Typed API Client

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api/schema.d.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/App.test.tsx`
- Create: `frontend/src/app/styles.css`
- Create: `frontend/src/pages/OverviewPage.tsx`
- Create: `frontend/src/pages/HoldingsPage.tsx`
- Create: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/vite-env.d.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `api.get/post/patch`, generated OpenAPI types, `App`, QueryClient, browser router, `npm run check`, and Vite output under `src/vibe_portfolio/web/dist/`.
- Consumes: committed `frontend/openapi.json` and backend routes from Task 11.

- [ ] **Step 1: Create a failing shell test and package scripts**

Pin the versions recorded in the plan header plus `@types/node@24.13.3`, `@types/react@19.2.17`, `@types/react-dom@19.2.3`, `@testing-library/react@16.3.2`, `@testing-library/jest-dom@6.9.1`, `@testing-library/user-event@14.6.1`, `@vitejs/plugin-react@6.0.3`, `@vitest/coverage-v8@4.1.10`, `jsdom@29.1.1`, `openapi-typescript@7.13.0`, `eslint@10.7.0`, `@eslint/js@10.0.1`, `typescript-eslint@8.64.0`, `eslint-plugin-react-hooks@7.1.1`, `eslint-plugin-react-refresh@0.5.3`, `globals@17.7.0`, and `prettier@3.9.5`. Set `"packageManager": "npm@11.17.0"` and engines `node >=24 <27`, `npm >=11 <13`.

Define scripts:

```json
{
  "scripts": {
    "api:types": "openapi-typescript openapi.json -o src/api/schema.d.ts",
    "build": "tsc -b && vite build",
    "format:check": "prettier --check .",
    "lint": "eslint . --max-warnings 0",
    "test": "vitest run",
    "test:coverage": "vitest run --coverage",
    "typecheck": "tsc -b --pretty false",
    "check": "npm run format:check && npm run lint && npm run typecheck && npm run test:coverage && npm run build"
  }
}
```

The first test asserts the Chinese navigation and default overview route.

- [ ] **Step 2: Install from the exact package definition and verify RED**

Run: `npm --cache /tmp/vibe-portfolio-npm-cache --prefix frontend install && npm --prefix frontend test`

Expected: FAIL because `App` and the shell do not exist; `package-lock.json` is created.

- [ ] **Step 3: Generate types and implement the minimal shell/client**

Run `npm --prefix frontend run api:types`. Implement `ApiError`, `request<T>()`, and typed GET/POST/PATCH wrappers that always set `Accept: application/json`, set JSON for writes, send credentials `same-origin`, and parse the stable error envelope. POST/PATCH require the caller to pass an idempotency key generated by `newIdempotencyKey()`; the wrapper never regenerates it during a retry. Never use `dangerouslySetInnerHTML`.

Build `App` with routes `/`, `/holdings`, `/settings`, minimal semantic page headings in the three page files, an accessible skip link, visible Chinese navigation, QueryClient retry disabled for 4xx, and a global error boundary. Tasks 13 and 14 replace each minimal page body with its approved tested flow without changing route ownership.

- [ ] **Step 4: Configure production build and coverage**

Vite must proxy `/api` to `http://127.0.0.1:8765` only in development, overwrite the proxied request `Origin` with `http://127.0.0.1:8765`, and build to `../src/vibe_portfolio/web/dist` with `emptyOutDir: true`. Vitest must use jsdom, load `vitest.setup.ts`, and enforce 80% lines/functions/statements and 75% branches.

Ignore only generated/runtime paths:

```gitignore
frontend/node_modules/
frontend/coverage/
frontend/playwright-report/
frontend/test-results/
src/vibe_portfolio/web/dist/
```

- [ ] **Step 5: Verify GREEN**

Run: `npm --prefix frontend run check`

Expected: formatting, lint, typecheck, coverage, and production build exit 0; `src/vibe_portfolio/web/dist/index.html` exists.

- [ ] **Step 6: Commit**

```bash
git add .gitignore frontend src/vibe_portfolio/web/__init__.py
git commit -m "feat: add typed portfolio web shell"
```

### Task 13: Build the Holdings Management Experience

**Files:**
- Create: `frontend/src/api/queries.ts`
- Create: `frontend/src/components/AccountForm.tsx`
- Create: `frontend/src/components/PositionForm.tsx`
- Create: `frontend/src/components/StatusMessage.tsx`
- Modify: `frontend/src/pages/HoldingsPage.tsx`
- Create: `frontend/src/pages/HoldingsPage.test.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/styles.css`

**Interfaces:**
- Produces: create/edit/archive account and position flows, explicit instrument search/confirmation, conflict recovery, and query invalidation.
- Consumes: typed account, instrument confirmation, and position endpoints.

- [ ] **Step 1: Write failing interaction tests**

Using `userEvent` and mocked fetch responses, cover empty onboarding, account creation with unknown cash, precision field error, explicit search submit, canonical candidate confirmation, position creation, currency mismatch, archived-items view and restore, archive confirmation, 409 reload prompt, keyboard/focus restoration, and network failure retained form values:

```tsx
it('requires candidate confirmation before position input', async () => {
  renderHoldings();
  await user.type(screen.getByLabelText('证券代码或名称'), '茅台');
  await user.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByText('600519.SH')).toBeVisible();
  expect(screen.queryByLabelText('持仓数量')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '确认 贵州茅台 600519.SH' }));
  expect(await screen.findByLabelText('持仓数量')).toBeVisible();
});
```

- [ ] **Step 2: Run the page test and verify RED**

Run: `npm --prefix frontend test -- HoldingsPage.test.tsx`

Expected: FAIL because the page and forms do not exist.

- [ ] **Step 3: Implement account and position mutations**

Use TanStack Query keys `['accounts']`, `['positions']`, and `['summary', currency]`. Generate one UUID idempotency key per user submission and retain it across network retry of that submission. Invalidate affected queries only after success. On 409, display current version and a `重新载入` action; never silently overwrite.

- [ ] **Step 4: Implement trusted search/confirmation flow**

Search only after explicit form submission. Render candidate name, canonical symbol, market, type, currency, and sources. POST only `candidate_id` to confirmation, then show quantity/cost fields. Prevent adding an instrument whose confirmed currency differs from the selected account before POST, while retaining the backend as authority.

- [ ] **Step 5: Implement archive and accessible status behavior**

Use an inline confirmation region, not `window.confirm`. Move focus to the result/status heading after success or failure, use `aria-live="polite"` for non-destructive statuses, and keep errors adjacent to their fields. Account archive remains disabled with an explanation when active positions exist.

- [ ] **Step 6: Verify GREEN and coverage**

Run: `npm --prefix frontend test -- HoldingsPage.test.tsx && npm --prefix frontend run typecheck && npm --prefix frontend run lint`

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/queries.ts frontend/src/components frontend/src/pages/HoldingsPage.tsx frontend/src/pages/HoldingsPage.test.tsx frontend/src/app
git commit -m "feat: add holdings management experience"
```

### Task 14: Build the Overview, Refresh, and Settings Experience

**Files:**
- Create: `frontend/src/components/CurrencyTabs.tsx`
- Create: `frontend/src/components/SummaryCards.tsx`
- Create: `frontend/src/components/PositionTable.tsx`
- Create: `frontend/src/components/AllocationBars.tsx`
- Modify: `frontend/src/pages/OverviewPage.tsx`
- Create: `frontend/src/pages/OverviewPage.test.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/styles.css`
- Modify: `src/vibe_portfolio/market_data/models.py`
- Modify: `src/vibe_portfolio/market_data/service.py`
- Modify: `src/vibe_portfolio/market_data/router.py`
- Create: `tests/portfolio/test_settings_api.py`

**Interfaces:**
- Produces: overview-first dashboard, explicit refresh status, separate currencies, accessible allocations, and redacted settings status.
- Consumes: summary, refresh, refresh-run, accounts, positions, and settings-status APIs.

- [ ] **Step 1: Write failing overview/settings tests**

Cover no holdings CTA, independent currency tabs, unknown cash, stale badge/timestamp/provider, unavailable cost/count, estimated label, zero-cost P&L omission, allocation excluding unavailable values, explicit refresh only, partial refresh summary, refresh-in-progress state, provider disabled status, relative database path, and no raw URLs/secrets.

```tsx
it('does not refresh on load and labels partial valuation', async () => {
  renderOverview(withUnavailablePosition());
  expect(await screen.findByText('估算总资产')).toBeVisible();
  expect(screen.getByText('1 项未估值')).toBeVisible();
  expect(fetchCalls('/api/v1/market-data/refresh')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: '刷新行情' }));
  expect(fetchCalls('/api/v1/market-data/refresh')).toHaveLength(1);
});
```

- [ ] **Step 2: Run focused frontend/backend tests and verify RED**

Run: `npm --prefix frontend test -- OverviewPage.test.tsx SettingsPage.test.tsx && uv run pytest tests/portfolio/test_settings_api.py -q`

Expected: FAIL because pages and settings endpoint do not exist.

- [ ] **Step 3: Add redacted settings status API**

Return schema revision, migration health, relative database display path, relative backup directory plus latest backup timestamp, adapter enabled flags, last successful refresh timestamp, and cache counts from `MarketDataService.settings_status()`. Never return an absolute path, provider URL, query, token, or holdings.

- [ ] **Step 4: Implement overview and explicit refresh**

Load available currencies from accounts, then query one summary per selected currency. The refresh mutation uses a retained idempotency key, disables while active, renders updated/stale/unavailable counts, and invalidates summaries plus settings only after completion. A stale value remains in market value with badge and estimated label; unavailable value is excluded and its cost is shown separately.

- [ ] **Step 5: Implement accessible allocation and responsive styling**

Render allocation as CSS bars plus a semantic table/list containing symbol, exact percentage text, and value. Do not use a canvas-only chart. Support keyboard tabs, 200% zoom, `prefers-reduced-motion`, visible focus, mobile stacking, and no horizontal loss of financial labels.

- [ ] **Step 6: Verify GREEN and full frontend gate**

Run: `uv run pytest tests/portfolio/test_settings_api.py -q && npm --prefix frontend run check`

Expected: backend test and every frontend format/lint/type/coverage/build gate exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/vibe_portfolio/market_data tests/portfolio/test_settings_api.py frontend/src
git commit -m "feat: add portfolio overview and quote status"
```

### Task 15: Add Production-Build Playwright E2E with Fake Providers

**Files:**
- Create: `scripts/run_e2e_server.py`
- Create: `scripts/e2e_fakes.py`
- Create: `scripts/run_e2e.py`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/portfolio.spec.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `npm run e2e`, a two-phase real FastAPI/SQLite/production-SPA restart harness, and deterministic fake search/quote behavior.
- Consumes: `create_app` dependency injection and built assets.

- [ ] **Step 1: Write the E2E scenario before the runner**

Install exact dev dependencies `@playwright/test@1.61.1` and `@axe-core/playwright@4.12.1`, update the lockfile, add `"e2e": "uv run python ../scripts/run_e2e.py"`, and create two serially tagged scenario phases that:

1. creates CNY, HKD, and USD accounts;
2. searches/confirms `600000.SH`, `00700.HK`, and `DEMO.US` synthetic fixtures;
3. creates positions and sets cash;
4. verifies no refresh request occurred on initial overview;
5. clicks refresh and verifies three separated exact summaries;
6. triggers a fake partial failure and verifies stale/unavailable labels;
7. edits then archives a position;
8. reloads, stops the first server process, starts a second process with the same temporary DB, and verifies persisted data in phase two;
9. requests `/api/v1/does-not-exist` and asserts JSON 404 rather than SPA HTML;
10. creates a two-tab version conflict and verifies reload recovery.

- [ ] **Step 2: Run Playwright and verify RED**

Run: `npm --prefix frontend run build && npm --prefix frontend run e2e`

Expected: FAIL because `scripts/run_e2e.py` and the injected server/fakes do not exist.

- [ ] **Step 3: Implement the injected fake-provider server**

`scripts/run_e2e_server.py` must use only an explicitly supplied temporary test database, inject deterministic providers from `scripts/e2e_fakes.py`, bind only `127.0.0.1:8875`, serve the production build, and install signal cleanup. It must reject execution unless `PORTFOLIO_E2E=1` and must never select fake providers through production `Settings`.

- [ ] **Step 4: Configure Playwright with bounded diagnostics**

Use Chromium, one worker, base URL `http://127.0.0.1:8875`, trace/screenshot on first failure, and no `webServer` block or external base URLs. Run Axe on overview, holdings, and settings and fail on serious/critical violations; exercise keyboard-only navigation and 200% viewport/zoom overflow assertions. `scripts/run_e2e.py` creates one `tempfile.TemporaryDirectory`, starts `run_e2e_server.py` with that explicit DB, runs the `@phase1` Playwright tests, stops the server, starts a new server process with the same DB, runs `@phase2`, and always terminates the child in `finally`. Install Chromium with `npm exec --prefix frontend -- playwright install --with-deps chromium`.

- [ ] **Step 5: Verify GREEN**

Run: `npm --prefix frontend run build && npm --prefix frontend run e2e`

Expected: all E2E scenarios pass; server logs contain no account names, balances, holdings, provider payloads, or absolute database path.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_e2e_server.py scripts/run_e2e.py scripts/e2e_fakes.py frontend/playwright.config.ts frontend/e2e frontend/package.json frontend/package-lock.json
git commit -m "test: cover portfolio experience end to end"
```

### Task 16: Add Explicit Live-Provider Gate, CI, Packaging, and User Documentation

**Files:**
- Create: `tests/contract/test_live_market_data.py`
- Modify: `pyproject.toml`
- Modify: `AGENTS.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/runbooks/portfolio-data.md`
- Modify: `docs/handoff/CURRENT.md`

**Interfaces:**
- Produces: opt-in `market_contract` marker, locked CI, packaged asset verification, local-user runbook, and final handoff evidence.
- Consumes: all previous backend/frontend/E2E gates.

- [ ] **Step 1: Write the opt-in provider smoke test with honest skip semantics**

Use only fixed public test instruments specified in the test (`510300.SH`, `00700.HK`, and `AAPL.US`), never personal holdings:

```python
@pytest.mark.market_contract
async def test_enabled_live_market_providers_return_valid_public_quotes() -> None:
    if os.environ.get("PORTFOLIO_RUN_MARKET_CONTRACT") != "1":
        pytest.skip("PORTFOLIO_RUN_MARKET_CONTRACT=1 is not set; market contract not run")
    registry = build_live_provider_registry(Settings(_env_file=None))
    try:
        result = await registry.probe_public_fixtures(("510300.SH", "00700.HK", "AAPL.US"))
        assert result.passed, result.model_dump_json(indent=2)
    finally:
        await registry.close()
```

The probe validates shape, currency, timestamp, and positive exact price for one reviewed instrument per enabled route. It is rate-bounded and reports each provider independently.

- [ ] **Step 2: Register markers and prove default skip semantics**

Add `market_contract` to pytest markers. Update the stable hermetic command in `AGENTS.md` from `-m "not contract"` to `-m "not contract and not market_contract"` so future agents cannot accidentally run live quote providers. Run:

`uv run pytest tests/contract/test_live_market_data.py -q`

Expected: one skipped with text `market contract not run`, never passed.

- [ ] **Step 3: Extend CI with locked frontend and E2E gates**

Keep the Python job unchanged except for migrations in coverage. Add a frontend job using `actions/setup-node@v4` with Node 24, `npm ci --prefix frontend`, deterministic OpenAPI regeneration followed by `git diff --exit-code`, `npm --prefix frontend run check`, Playwright Chromium install, and `npm --prefix frontend run e2e`. Add a Hatch wheel `force-include` mapping from `src/vibe_portfolio/web/dist` to `vibe_portfolio/web/dist`, build the wheel only after the SPA, and assert the wheel contains `vibe_portfolio/web/dist/index.html` and hashed assets.

- [ ] **Step 4: Document the user experience and data boundary**

Update README so the primary run path is:

```bash
uv sync --frozen --extra dev
npm ci --prefix frontend
npm --prefix frontend run build
uv run portfolio-api
```

Document `http://127.0.0.1:8765`, database/backup locations, no login loopback risk, explicit refresh, stale/unavailable meanings, no cross-currency total, no transaction history, no trading, provider usage risk, and recovery steps. Keep Vibe compatibility/MCP instructions separate and unchanged in meaning.

- [ ] **Step 5: Run the complete hermetic release gate**

Run exactly:

```bash
uv sync --frozen --extra dev
uv lock --check
uv run ruff check src tests migrations scripts
uv run mypy src
uv run pytest -m "not contract and not market_contract" --cov=vibe_portfolio --cov-report=term-missing --cov-fail-under=85
uv run python scripts/export_openapi.py
npm ci --prefix frontend
npm --prefix frontend run api:types
git diff --exit-code -- frontend/openapi.json frontend/src/api/schema.d.ts
npm --prefix frontend run check
npm --prefix frontend run e2e
uv build
unzip -l dist/vibe_trading_portfolio-*.whl | rg "vibe_portfolio/web/dist/(index.html|assets/)"
```

Expected: every command exits 0; backend coverage is at least 85%; frontend coverage is at least 80% lines; E2E passes; wheel contains the built SPA. Do not run or report the live provider/Vibe/MCP gates unless their explicit flags are set.

- [ ] **Step 6: Perform privacy and boundary scans**

Run:

```bash
git status --short
git diff --check
rg -n "ALLOW_SESSION_MCP_SERVERS|mcpServers" src tests frontend scripts
rg -n "VibeGateway|vibe_portfolio\.vibe" src/vibe_portfolio/market_data
git ls-files var src/vibe_portfolio/web/dist frontend/node_modules frontend/coverage
```

Expected: only the pre-existing governed MCP installation references appear; market-data search returns no Vibe dependency; generated/runtime/personal-data paths return no tracked files; no tokens, account identifiers, holdings, or absolute personal paths appear in the diff.

- [ ] **Step 7: Run opt-in gates only when explicitly configured**

Follow `docs/runbooks/vibe-compatibility.md` for route, runtime, and MCP checks. Run the new market check only with `PORTFOLIO_RUN_MARKET_CONTRACT=1`. Record each as `passed`, `failed`, or `not run`; never collapse skipped layers into a single green statement.

- [ ] **Step 8: Update handoff with actual evidence and commit**

Record branch, exact commits, remote state, implemented scope, actual coverage, each hermetic command result, opt-in gate status, remaining formal-MVP scope, and the next approval-gated action. Do not record secrets or personal holdings.

```bash
git add AGENTS.md pyproject.toml .github/workflows/ci.yml tests/contract/test_live_market_data.py README.md docs/runbooks/portfolio-data.md docs/handoff/CURRENT.md
git commit -m "docs: ship portfolio experience milestone"
```

---

## Specification Coverage Map

| Design area | Implemented by |
|---|---|
| Staged snapshot scope and no-trading/Vibe independence | Global Constraints; Tasks 8–10 and 16 boundary scans |
| Overview-first Chinese UX, holdings, settings, empty/error/stale states | Tasks 13–15 |
| Exact accounts, instruments, positions, archive, concurrency, idempotency | Tasks 2–7 |
| SQLite, migrations, integrity, permissions, backup, recovery, retention | Tasks 3–4 and 10 |
| Currency-local valuation, unknown cash, stale/unavailable rules | Task 7 and Task 14 |
| Sidecar-owned search, confirmation, quote validation, routing, fallback | Tasks 8–10 |
| Versioned API, pagination, OpenAPI, stable sanitized errors | Tasks 5–11 |
| Loopback no-auth compensating controls, CSP, SSRF and static routing | Tasks 8 and 11 |
| Hermetic backend/frontend/E2E and opt-in external gates | Tasks 1–16, finalized in Tasks 15–16 |
| Packaging, user operation, provider risk, and truthful handoff | Task 16 |

Self-review found no unassigned design requirement. Formal ledger, CSV import, FX consolidation, research, restore UI, permanent deletion, and remote authentication remain deliberately outside Experience Milestone 1A and must not be pulled into these tasks.

---

## Plan Completion Criteria

The plan is complete only when all 16 task commits have been reviewed, the complete hermetic gate in Task 16 has fresh successful output, the production WebUI is exercised through Playwright, the market-data package has no Vibe dependency, and the handoff records actual results. Experience Milestone 1A still must not be described as the umbrella design's immutable-ledger MVP.
