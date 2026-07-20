import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type MutableRefObject, useRef, useState } from "react";

import { ApiError, api, newIdempotencyKey } from "../api/client";
import {
  accountsQuery,
  holdingsKeys,
  positionsQuery,
  type Account,
  type Position,
} from "../api/queries";
import { AccountForm } from "../components/AccountForm";
import { PositionEditForm, PositionForm } from "../components/PositionForm";
import { type Notice, StatusMessage } from "../components/StatusMessage";
import { formatMoney, formatQuantity } from "../lib/decimal";

interface PendingSubmission {
  signature: string;
  key: string;
}

function retainedKey(
  pending: MutableRefObject<PendingSubmission | undefined>,
  payload: object,
): string {
  const signature = JSON.stringify(payload);
  if (pending.current?.signature !== signature) {
    pending.current = { signature, key: newIdempotencyKey() };
  }
  return pending.current.key;
}

function AccountRecord({
  account,
  hasPositions,
  onNotice,
  positionsError,
  positionsReady,
}: {
  account: Account;
  hasPositions: boolean;
  onNotice: (notice: Notice) => void;
  positionsError: boolean;
  positionsReady: boolean;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const archiveTrigger = useRef<HTMLButtonElement>(null);
  const archivePending = useRef<PendingSubmission | undefined>(undefined);
  const archivePayload = { version: account.version, archived: true as const };
  const archive = useMutation({
    mutationFn: (key: string) =>
      api.patch("/api/v1/accounts/{account_id}", archivePayload, key, {
        params: { path: { account_id: account.id } },
      }),
    onSuccess: async () => {
      archivePending.current = undefined;
      setConfirming(false);
      onNotice({ kind: "success", title: "账户已归档" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: holdingsKeys.accounts }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.summary(account.currency),
        }),
      ]);
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.code === "ACCOUNT_HAS_ACTIVE_POSITIONS"
      ) {
        onNotice({
          kind: "error",
          title: "账户已有当前持仓，无法归档",
          detail: "请重新载入持仓，并先归档该账户的全部当前持仓。",
          action: {
            label: "重新载入持仓",
            onClick: () => {
              void queryClient
                .invalidateQueries({ queryKey: holdingsKeys.positions(false) })
                .then(() => {
                  setConfirming(false);
                  onNotice({ kind: "success", title: "已重新载入最新持仓" });
                });
            },
          },
        });
        return;
      }
      if (
        error instanceof ApiError &&
        error.code === "CONCURRENT_MODIFICATION"
      ) {
        const version = error.fields?.version;
        onNotice({
          kind: "error",
          title: "记录已在其他位置更新",
          detail:
            typeof version === "number" || typeof version === "string"
              ? `服务器当前版本 ${String(version)}`
              : "请重新载入服务器中的最新记录。",
          action: {
            label: "重新载入",
            onClick: () => {
              void queryClient
                .invalidateQueries({ queryKey: holdingsKeys.accounts })
                .then(() =>
                  onNotice({ kind: "success", title: "已重新载入最新记录" }),
                );
            },
          },
        });
        return;
      }
      onNotice({ kind: "error", title: "暂时无法归档账户，请重试" });
    },
  });

  return (
    <li className="record-card">
      <strong>{account.name}</strong>
      <span>{account.currency}</span>
      <span>
        {account.cash_balance === null
          ? "现金余额未知"
          : `现金余额 ${formatMoney(account.cash_balance, account.currency)}`}
      </span>
      <div className="record-actions">
        <button
          type="button"
          onClick={() => setEditing((value) => !value)}
          aria-label={`编辑账户 ${account.name}`}
        >
          编辑
        </button>
        <button
          ref={archiveTrigger}
          type="button"
          disabled={!positionsReady || hasPositions}
          onClick={() => setConfirming(true)}
          aria-label={`归档账户 ${account.name}`}
        >
          归档
        </button>
      </div>
      {!positionsReady ? (
        <p>
          {positionsError
            ? "暂时无法确认该账户是否有当前持仓。"
            : "正在确认该账户是否有当前持仓。"}
        </p>
      ) : hasPositions ? (
        <p>请先归档该账户的全部持仓</p>
      ) : null}
      {editing ? (
        <AccountForm
          account={account}
          onCancel={() => setEditing(false)}
          onNotice={onNotice}
        />
      ) : null}
      {confirming ? (
        <section
          className="inline-confirmation"
          role="region"
          aria-label="确认归档账户"
        >
          <p>归档后，该账户不会出现在当前账户列表中。</p>
          <button
            type="button"
            disabled={archive.isPending}
            onClick={() =>
              archive.mutate(retainedKey(archivePending, archivePayload))
            }
          >
            确认归档
          </button>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              archiveTrigger.current?.focus();
            }}
          >
            取消
          </button>
        </section>
      ) : null}
    </li>
  );
}

function ArchivedAccountRecord({
  account,
  onNotice,
}: {
  account: Account;
  onNotice: (notice: Notice) => void;
}) {
  const queryClient = useQueryClient();
  const restorePending = useRef<PendingSubmission | undefined>(undefined);
  const restorePayload = { version: account.version, archived: false as const };
  const restore = useMutation({
    mutationFn: (key: string) =>
      api.patch("/api/v1/accounts/{account_id}", restorePayload, key, {
        params: { path: { account_id: account.id } },
      }),
    onSuccess: async () => {
      restorePending.current = undefined;
      onNotice({ kind: "success", title: "账户已恢复" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: holdingsKeys.accounts }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.summary(account.currency),
        }),
      ]);
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.code === "CONCURRENT_MODIFICATION"
      ) {
        const version = error.fields?.version;
        onNotice({
          kind: "error",
          title: "记录已在其他位置更新",
          detail:
            typeof version === "number" || typeof version === "string"
              ? `服务器当前版本 ${String(version)}`
              : "请重新载入服务器中的最新记录。",
          action: {
            label: "重新载入",
            onClick: () => {
              void queryClient.invalidateQueries({
                queryKey: holdingsKeys.accounts,
              });
            },
          },
        });
        return;
      }
      onNotice({ kind: "error", title: "暂时无法恢复账户，请重试" });
    },
  });

  return (
    <li className="record-card">
      <strong>{account.name}</strong>
      <span>{account.currency}</span>
      <button
        type="button"
        disabled={restore.isPending}
        aria-label={`恢复账户 ${account.name}`}
        onClick={() =>
          restore.mutate(retainedKey(restorePending, restorePayload))
        }
      >
        恢复
      </button>
    </li>
  );
}

function PositionRecord({
  account,
  archived,
  onNotice,
  position,
}: {
  account: Account;
  archived: boolean;
  onNotice: (notice: Notice) => void;
  position: Position;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const archiveTrigger = useRef<HTMLButtonElement>(null);
  const archivePending = useRef<PendingSubmission | undefined>(undefined);
  const archivePayload = { version: position.version, archived: !archived };
  const updateArchive = useMutation({
    mutationFn: (key: string) =>
      api.patch("/api/v1/positions/{position_id}", archivePayload, key, {
        params: { path: { position_id: position.id } },
      }),
    onSuccess: async () => {
      archivePending.current = undefined;
      setConfirming(false);
      onNotice({
        kind: "success",
        title: archived ? "持仓已恢复" : "持仓已归档",
      });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.positions(false),
        }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.positions(true),
        }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.summary(account.currency),
        }),
      ]);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "POSITION_ARCHIVED") {
        onNotice({
          kind: "error",
          title: "持仓已在其他位置归档",
          detail: "请重新载入最新持仓状态。",
          action: {
            label: "重新载入持仓",
            onClick: () => {
              void Promise.all([
                queryClient.invalidateQueries({
                  queryKey: holdingsKeys.positions(false),
                }),
                queryClient.invalidateQueries({
                  queryKey: holdingsKeys.positions(true),
                }),
              ]).then(() => {
                setConfirming(false);
                onNotice({ kind: "success", title: "已重新载入最新持仓" });
              });
            },
          },
        });
        return;
      }
      if (error instanceof ApiError && error.code === "DUPLICATE_POSITION") {
        onNotice({
          kind: "error",
          title: "该账户已有同一证券的当前持仓",
          detail: "请重新载入当前持仓后再处理。",
          action: {
            label: "重新载入持仓",
            onClick: () => {
              void Promise.all([
                queryClient.invalidateQueries({
                  queryKey: holdingsKeys.positions(false),
                }),
                queryClient.invalidateQueries({
                  queryKey: holdingsKeys.positions(true),
                }),
              ]).then(() => {
                setConfirming(false);
                onNotice({ kind: "success", title: "已重新载入最新持仓" });
              });
            },
          },
        });
        return;
      }
      if (error instanceof ApiError && error.code === "ACCOUNT_ARCHIVED") {
        onNotice({
          kind: "error",
          title: "所属账户已归档，无法恢复持仓",
          detail: "请先在已归档账户列表中恢复所属账户。",
        });
        return;
      }
      if (
        error instanceof ApiError &&
        error.code === "CONCURRENT_MODIFICATION"
      ) {
        const version = error.fields?.version;
        onNotice({
          kind: "error",
          title: "记录已在其他位置更新",
          detail:
            typeof version === "number" || typeof version === "string"
              ? `服务器当前版本 ${String(version)}`
              : "请重新载入服务器中的最新记录。",
          action: {
            label: "重新载入",
            onClick: () => {
              void queryClient
                .invalidateQueries({
                  queryKey: holdingsKeys.positions(archived),
                })
                .then(() => {
                  setConfirming(false);
                  onNotice({ kind: "success", title: "已重新载入最新记录" });
                });
            },
          },
        });
        return;
      }
      onNotice({
        kind: "error",
        title: archived
          ? "暂时无法恢复持仓，请重试"
          : "暂时无法归档持仓，请重试",
      });
    },
  });

  const identity = `${position.instrument.name} ${position.instrument.canonical_symbol} ${account.name}`;

  return (
    <li className="record-card">
      <h3>
        {position.instrument.name} {position.instrument.canonical_symbol}
      </h3>
      <strong>数量 {formatQuantity(position.quantity)}</strong>
      <span>
        平均成本 {formatMoney(position.average_cost, account.currency)}
      </span>
      {position.note === null ? null : <span>{position.note}</span>}
      {archived ? (
        <button
          type="button"
          disabled={updateArchive.isPending}
          onClick={() =>
            updateArchive.mutate(retainedKey(archivePending, archivePayload))
          }
          aria-label={`恢复持仓 ${identity}`}
        >
          恢复
        </button>
      ) : (
        <div className="record-actions">
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-label={`编辑持仓 ${identity}`}
          >
            编辑
          </button>
          <button
            ref={archiveTrigger}
            type="button"
            onClick={() => setConfirming(true)}
            aria-label={`归档持仓 ${identity}`}
          >
            归档
          </button>
        </div>
      )}
      {editing ? (
        <PositionEditForm
          position={position}
          account={account}
          onCancel={() => setEditing(false)}
          onNotice={onNotice}
        />
      ) : null}
      {confirming ? (
        <section
          className="inline-confirmation"
          role="region"
          aria-label="确认归档持仓"
        >
          <p>归档后可从已归档项目中恢复。</p>
          <button
            type="button"
            disabled={updateArchive.isPending}
            onClick={() =>
              updateArchive.mutate(retainedKey(archivePending, archivePayload))
            }
          >
            确认归档
          </button>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              archiveTrigger.current?.focus();
            }}
          >
            取消
          </button>
        </section>
      ) : null}
    </li>
  );
}

export function HoldingsPage() {
  const [notice, setNotice] = useState<Notice>();
  const [showArchived, setShowArchived] = useState(false);
  const accounts = useQuery(accountsQuery(false));
  const positions = useQuery(positionsQuery(false));
  const archivedAccounts = useQuery({
    ...accountsQuery(true),
    enabled: showArchived,
  });
  const archivedPositions = useQuery({
    ...positionsQuery(true),
    enabled: showArchived,
  });
  const accountItems = (
    accounts.isError ? [] : (accounts.data?.items ?? [])
  ).filter((account) => account.archived_at === null);
  const activeAccountIds = new Set(accountItems.map((account) => account.id));
  const archivedAccountItems = (
    archivedAccounts.isError ? [] : (archivedAccounts.data?.items ?? [])
  ).filter(
    (account) =>
      account.archived_at !== null && !activeAccountIds.has(account.id),
  );
  const allAccountItems = [...accountItems, ...archivedAccountItems];
  const accountQueriesFetching =
    accounts.isFetching || (showArchived && archivedAccounts.isFetching);
  const archivedDataFetching =
    accountQueriesFetching || archivedPositions.isFetching;
  const currentPositionRecords = (positions.data?.items ?? []).flatMap(
    (position) => {
      const account = accountItems.find(
        (item) => item.id === position.account_id,
      );
      return account === undefined ? [] : [{ position, account }];
    },
  );
  const archivedPositionRecords = (archivedPositions.data?.items ?? []).flatMap(
    (position) => {
      const account = allAccountItems.find(
        (item) => item.id === position.account_id,
      );
      return account === undefined ? [] : [{ position, account }];
    },
  );

  return (
    <section aria-labelledby="holdings-heading">
      <h1 id="holdings-heading">持仓管理</h1>
      <p>管理本地保存的账户与当前持仓。</p>
      {notice === undefined ? null : <StatusMessage notice={notice} />}
      {accounts.isPending ? <p role="status">正在加载账户…</p> : null}
      {accounts.isError ? (
        <StatusMessage
          notice={{
            kind: "error",
            title: "暂时无法加载账户，请重试",
            action: {
              label: "重新载入账户",
              onClick: () => {
                void accounts.refetch();
              },
            },
          }}
        />
      ) : null}
      {positions.isError ? (
        <StatusMessage
          notice={{
            kind: "error",
            title: "暂时无法加载持仓，请重试",
            action: {
              label: "重新载入持仓",
              onClick: () => {
                void positions.refetch();
              },
            },
          }}
        />
      ) : null}
      {positions.isPending ? (
        <p role="status" aria-live="polite">
          正在加载当前持仓…
        </p>
      ) : null}
      {!accountQueriesFetching &&
      !accounts.isError &&
      accountItems.length === 0 &&
      accounts.data !== undefined ? (
        <section
          className="empty-state"
          aria-labelledby="empty-accounts-heading"
        >
          <h2 id="empty-accounts-heading">还没有账户</h2>
          <p>先创建一个固定币种账户，再添加当前持仓。</p>
        </section>
      ) : null}
      {accountQueriesFetching || accountItems.length === 0 ? null : (
        <section aria-labelledby="accounts-heading">
          <h2 id="accounts-heading">账户</h2>
          <ul className="record-list">
            {accountItems.map((account) => (
              <AccountRecord
                key={account.id}
                account={account}
                hasPositions={
                  positions.data?.items.some(
                    (position) => position.account_id === account.id,
                  ) ?? false
                }
                onNotice={setNotice}
                positionsError={positions.isError}
                positionsReady={positions.isSuccess}
              />
            ))}
          </ul>
        </section>
      )}
      <AccountForm onNotice={setNotice} />
      {accountQueriesFetching || accountItems.length === 0 ? null : (
        <PositionForm accounts={accountItems} onNotice={setNotice} />
      )}
      {accountQueriesFetching ||
      !accounts.isSuccess ||
      !positions.isSuccess ||
      currentPositionRecords.length === 0 ? null : (
        <section aria-labelledby="positions-heading">
          <h2 id="positions-heading">当前持仓</h2>
          <ul className="record-list" aria-label="当前持仓">
            {currentPositionRecords.map(({ position, account }) => (
              <PositionRecord
                key={position.id}
                position={position}
                account={account}
                archived={false}
                onNotice={setNotice}
              />
            ))}
          </ul>
        </section>
      )}
      {positions.isSuccess && positions.data.items.length === 0 ? (
        <p aria-live="polite">暂无当前持仓</p>
      ) : null}
      {!accountQueriesFetching &&
      accounts.isSuccess &&
      positions.isSuccess &&
      currentPositionRecords.length !== positions.data.items.length ? (
        <StatusMessage
          notice={{ kind: "error", title: "持仓所属账户不可用，请重新载入" }}
        />
      ) : null}
      <button type="button" onClick={() => setShowArchived((value) => !value)}>
        {showArchived ? "隐藏已归档项目" : "查看已归档项目"}
      </button>
      {showArchived ? (
        <div>
          {archivedDataFetching ? (
            <p role="status" aria-live="polite">
              正在加载已归档项目…
            </p>
          ) : null}
          {archivedAccounts.isError || archivedPositions.isError ? (
            <StatusMessage
              notice={{
                kind: "error",
                title: "暂时无法加载已归档项目，请重试",
                action: {
                  label: "重新载入已归档项目",
                  onClick: () => {
                    void Promise.all([
                      archivedAccounts.refetch(),
                      archivedPositions.refetch(),
                    ]);
                  },
                },
              }}
            />
          ) : null}
          {!archivedDataFetching &&
          archivedAccounts.isSuccess &&
          archivedPositions.isSuccess &&
          archivedAccountItems.length === 0 &&
          archivedPositions.data.items.length === 0 ? (
            <p aria-live="polite">没有已归档账户或持仓</p>
          ) : null}
          {!archivedDataFetching &&
          accounts.isSuccess &&
          archivedAccounts.isSuccess &&
          archivedPositions.isSuccess &&
          archivedPositionRecords.length !==
            archivedPositions.data.items.length ? (
            <StatusMessage
              notice={{
                kind: "error",
                title: "归档持仓所属账户不可用，请重新载入",
              }}
            />
          ) : null}
          {accountQueriesFetching ||
          archivedAccountItems.length === 0 ? null : (
            <section aria-labelledby="archived-accounts-heading">
              <h2 id="archived-accounts-heading">已归档账户</h2>
              <ul className="record-list">
                {archivedAccountItems.map((account) => (
                  <ArchivedAccountRecord
                    key={account.id}
                    account={account}
                    onNotice={setNotice}
                  />
                ))}
              </ul>
            </section>
          )}
          {archivedDataFetching ||
          !accounts.isSuccess ||
          !archivedAccounts.isSuccess ||
          !archivedPositions.isSuccess ||
          archivedPositionRecords.length === 0 ? null : (
            <section aria-labelledby="archived-positions-heading">
              <h2 id="archived-positions-heading">已归档持仓</h2>
              <ul className="record-list">
                {archivedPositionRecords.map(({ position, account }) => (
                  <PositionRecord
                    key={position.id}
                    position={position}
                    account={account}
                    archived
                    onNotice={setNotice}
                  />
                ))}
              </ul>
            </section>
          )}
        </div>
      ) : null}
    </section>
  );
}
