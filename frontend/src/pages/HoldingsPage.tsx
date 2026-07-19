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
          : `现金余额 ${account.cash_balance}`}
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
          detail: "当前 API 尚不提供归档账户列表与恢复入口。",
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

  return (
    <li className="record-card">
      <strong>数量 {position.quantity}</strong>
      <span>
        平均成本 {position.average_cost} {account.currency}
      </span>
      {position.note === null ? null : <span>{position.note}</span>}
      {archived ? (
        <button
          type="button"
          disabled={updateArchive.isPending}
          onClick={() =>
            updateArchive.mutate(retainedKey(archivePending, archivePayload))
          }
          aria-label={`恢复持仓 ${position.quantity}`}
        >
          恢复
        </button>
      ) : (
        <div className="record-actions">
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-label={`编辑持仓 ${position.quantity}`}
          >
            编辑
          </button>
          <button
            ref={archiveTrigger}
            type="button"
            onClick={() => setConfirming(true)}
            aria-label={`归档持仓 ${position.quantity}`}
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
  const accounts = useQuery(accountsQuery());
  const positions = useQuery(positionsQuery(false));
  const archivedPositions = useQuery({
    ...positionsQuery(true),
    enabled: showArchived,
  });
  const accountItems = accounts.data?.items ?? [];

  return (
    <section aria-labelledby="holdings-heading">
      <h1 id="holdings-heading">持仓管理</h1>
      <p>管理本地保存的账户与当前持仓。</p>
      {notice === undefined ? null : <StatusMessage notice={notice} />}
      {accounts.isPending ? <p role="status">正在加载账户…</p> : null}
      {accounts.isError ? (
        <StatusMessage
          notice={{ kind: "error", title: "暂时无法加载账户，请重试" }}
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
      {accountItems.length === 0 && accounts.data !== undefined ? (
        <section
          className="empty-state"
          aria-labelledby="empty-accounts-heading"
        >
          <h2 id="empty-accounts-heading">还没有账户</h2>
          <p>先创建一个固定币种账户，再添加当前持仓。</p>
        </section>
      ) : null}
      {accountItems.length === 0 ? null : (
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
      {accountItems.length === 0 ? null : (
        <PositionForm accounts={accountItems} onNotice={setNotice} />
      )}
      {positions.data === undefined ||
      positions.data.items.length === 0 ? null : (
        <section aria-labelledby="positions-heading">
          <h2 id="positions-heading">当前持仓</h2>
          <ul className="record-list">
            {positions.data.items.map((position) => {
              const account = accountItems.find(
                (item) => item.id === position.account_id,
              );
              return account === undefined ? null : (
                <PositionRecord
                  key={position.id}
                  position={position}
                  account={account}
                  archived={false}
                  onNotice={setNotice}
                />
              );
            })}
          </ul>
        </section>
      )}
      <button type="button" onClick={() => setShowArchived((value) => !value)}>
        {showArchived ? "隐藏已归档项目" : "查看已归档项目"}
      </button>
      {showArchived ? (
        <section aria-labelledby="archived-positions-heading">
          <h2 id="archived-positions-heading">已归档持仓</h2>
          {archivedPositions.isError ? (
            <StatusMessage
              notice={{
                kind: "error",
                title: "暂时无法加载已归档持仓，请重试",
                action: {
                  label: "重新载入已归档持仓",
                  onClick: () => {
                    void archivedPositions.refetch();
                  },
                },
              }}
            />
          ) : null}
          {archivedPositions.data?.items.length === 0 ? (
            <p>没有已归档持仓。</p>
          ) : null}
          <ul className="record-list">
            {archivedPositions.data?.items.map((position) => {
              const account = accountItems.find(
                (item) => item.id === position.account_id,
              );
              return account === undefined ? null : (
                <PositionRecord
                  key={position.id}
                  position={position}
                  account={account}
                  archived
                  onNotice={setNotice}
                />
              );
            })}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
