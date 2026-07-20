# Portfolio Data Runbook

## Scope and safety boundary

The Sidecar stores a local snapshot of accounts and current positions. It does not store transaction history, infer trades, connect to a broker, place orders, or execute trades. Vibe compatibility and MCP are optional separate integrations described in the [compatibility runbook](vibe-compatibility.md); portfolio storage and market data do not import or depend on Vibe.

The service binds only to `127.0.0.1` and has no login. Never publish it through a public bind, reverse proxy, tunnel, shared host, or remote container port. Anyone able to reach the loopback service in the same user context could read or change the local portfolio snapshot.

## Start and stop

From the repository root:

```bash
uv sync --frozen --extra dev
npm ci --prefix frontend
npm --prefix frontend run build
uv run portfolio-api
```

Open <http://127.0.0.1:8765>. Stop the API cleanly with `Ctrl-C` before copying, replacing, or restoring its database.

## Storage and backups

- Default database: `var/data/portfolio.db`
- Automatic migration backups: `var/data/portfolio.db.backup-*.db`
- Database directory mode: owner-only
- Database and backup file mode: owner-only

Keep `var/` out of source control. Never paste database contents, account names, balances, holdings, generated tokens, or API keys into issues, logs, commits, or handoff documents.

Before an upgrade or manual recovery:

1. Stop `portfolio-api`.
2. Copy the current database and the newest backup to a private location outside the repository.
3. Confirm the copies remain owner-readable only.
4. Start the API and inspect Settings for the schema revision and migration-health state.

## Restore after migration or database failure

There is no restore button in Experience Milestone 1A. Restore is an operator filesystem procedure:

1. Stop the API and verify no `portfolio-api` process is using the database.
2. Preserve the failed `portfolio.db` under a new private diagnostic filename; do not delete the only copy.
3. Select the newest known-good `portfolio.db.backup-*.db` created before the failure.
4. Copy that backup to `var/data/portfolio.db`, keeping mode `0600` and directory mode `0700`.
5. Start the API. It will validate the path, run required migrations, and create a new pre-migration backup when applicable.
6. Confirm Settings reports a healthy migration and verify accounts/positions manually before further edits.

If startup remains fail-closed, stop and retain all copies. Do not bypass path, integrity, migration, or permission checks.

## Quote refresh and valuation meanings

Opening Overview never contacts providers. Use the explicit refresh button when you want current quotes.

- **Fresh:** the latest valid quote is within the accepted age and the latest attempt succeeded.
- **Stale:** the Sidecar retained the last valid quote after it aged or a later provider attempt failed. The value is an estimate and shows provider/timestamp evidence.
- **Unavailable:** no valid quote exists for that position. Its cost remains visible, but it is excluded from market value and allocation.
- **Partial refresh:** at least one instrument updated while another remained stale or unavailable.

Cash can be unknown. Unknown cash and unvalued positions make totals estimates. CNY, HKD, and USD are never combined; there is no FX conversion.

## Provider risk and recovery

Market adapters use reviewed public endpoints with bounded destinations, response sizes, timeouts, and concurrency. Those endpoints remain external dependencies whose availability, payloads, rate limits, and usage terms can change.

When refresh is partial or fails:

1. Do not repeatedly hammer the refresh control.
2. Inspect the visible stale/unavailable counts and provider evidence; do not treat an old quote as current.
3. Check local connectivity and provider status outside the Sidecar without exposing portfolio data.
4. Retry later. The last valid quote remains preserved and visibly stale.
5. If validating a release, use the opt-in public-fixture gate only—never personal holdings:

   ```bash
   PORTFOLIO_RUN_MARKET_CONTRACT=1 uv run pytest tests/contract/test_live_market_data.py -q
   ```

Without `PORTFOLIO_RUN_MARKET_CONTRACT=1`, that layer is skipped/not run. The hermetic suite never contacts providers.

## Deliberately unsupported operations

Experience Milestone 1A has no CSV import, immutable ledger, transaction reconstruction, realized performance, FX consolidation, research automation, remote authentication, restore UI, permanent deletion, broker write, order placement, or trade execution. Do not infer those capabilities from snapshot valuation.
