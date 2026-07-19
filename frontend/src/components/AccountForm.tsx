import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";

import { ApiError, api, newIdempotencyKey } from "../api/client";
import { holdingsKeys, type Account, type Currency } from "../api/queries";
import type { Notice } from "./StatusMessage";

const MONEY = /^\d+(?:\.\d{1,6})?$/;

interface AccountDraft {
  name: string;
  currency: Currency;
  cash_balance: string | null;
}

interface PendingSubmission {
  signature: string;
  key: string;
}

export function AccountForm({
  account,
  onCancel,
  onNotice,
}: {
  account?: Account;
  onCancel?: () => void;
  onNotice: (notice: Notice) => void;
}) {
  const fieldSuffix = account === undefined ? "" : `-${account.id}`;
  const queryClient = useQueryClient();
  const [name, setName] = useState(account?.name ?? "");
  const [currency, setCurrency] = useState<Currency>(
    account?.currency ?? "CNY",
  );
  const [cash, setCash] = useState(account?.cash_balance ?? "");
  const [nameError, setNameError] = useState<string>();
  const [cashError, setCashError] = useState<string>();
  const pending = useRef<PendingSubmission | undefined>(undefined);
  const mutation = useMutation({
    mutationFn: ({ draft, key }: { draft: AccountDraft; key: string }) =>
      account === undefined
        ? api.post("/api/v1/accounts", draft, key)
        : api.patch(
            "/api/v1/accounts/{account_id}",
            {
              version: account.version,
              name: draft.name,
              cash_balance: draft.cash_balance,
            },
            key,
            { params: { path: { account_id: account.id } } },
          ),
    onSuccess: async (savedAccount) => {
      pending.current = undefined;
      if (account === undefined) {
        setName("");
        setCash("");
        setCurrency("CNY");
      }
      onNotice({
        kind: "success",
        title: account === undefined ? "账户已创建" : "账户已更新",
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: holdingsKeys.accounts }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.summary(savedAccount.currency),
        }),
      ]);
      onCancel?.();
    },
    onError: (error) => {
      if (
        account !== undefined &&
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
                .then(() => {
                  onCancel?.();
                  onNotice({ kind: "success", title: "已重新载入最新记录" });
                });
            },
          },
        });
        return;
      }
      if (error instanceof ApiError) {
        if (
          error.code === "DUPLICATE_ACCOUNT_NAME" ||
          error.fields?.name !== undefined
        ) {
          setNameError(
            error.code === "DUPLICATE_ACCOUNT_NAME"
              ? "账户名称已存在，请换一个名称。"
              : "请输入有效的账户名称",
          );
        }
        if (error.fields?.cash_balance !== undefined) {
          setCashError("请输入不小于 0 且最多 6 位小数的现金余额");
        }
      }
      const detail =
        error instanceof ApiError && error.code === "DUPLICATE_ACCOUNT_NAME"
          ? "账户名称已存在，请换一个名称。"
          : "请检查本地服务后重试。";
      onNotice({
        kind: "error",
        title: "暂时无法保存账户，请重试",
        detail,
      });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNameError(undefined);
    const normalizedCash = cash.trim();
    if (normalizedCash !== "" && !MONEY.test(normalizedCash)) {
      setCashError(
        /^\d+\.\d{7,}$/.test(normalizedCash)
          ? "现金余额最多保留 6 位小数"
          : "请输入不小于 0 的有效现金余额",
      );
      return;
    }
    setCashError(undefined);
    const draft: AccountDraft = {
      name: name.trim(),
      currency,
      cash_balance: normalizedCash === "" ? null : normalizedCash,
    };
    const signature = JSON.stringify(draft);
    if (pending.current?.signature !== signature) {
      pending.current = { signature, key: newIdempotencyKey() };
    }
    mutation.mutate({ draft, key: pending.current.key });
  }

  return (
    <form
      className="stack-form"
      onSubmit={submit}
      noValidate
      aria-label={
        account === undefined ? undefined : `编辑账户 ${account.name}`
      }
    >
      <h2>{account === undefined ? "创建账户" : "编辑账户"}</h2>
      <label htmlFor={`account-name${fieldSuffix}`}>账户名称</label>
      <input
        id={`account-name${fieldSuffix}`}
        required
        maxLength={80}
        value={name}
        aria-invalid={nameError === undefined ? undefined : true}
        aria-describedby={
          nameError === undefined
            ? undefined
            : `account-name-error${fieldSuffix}`
        }
        onChange={(event) => setName(event.currentTarget.value)}
      />
      {nameError === undefined ? null : (
        <p className="field-error" id={`account-name-error${fieldSuffix}`}>
          {nameError}
        </p>
      )}
      <label htmlFor={`account-currency${fieldSuffix}`}>账户币种</label>
      <select
        id={`account-currency${fieldSuffix}`}
        disabled={account !== undefined}
        value={currency}
        onChange={(event) => setCurrency(event.currentTarget.value as Currency)}
      >
        <option value="CNY">CNY</option>
        <option value="HKD">HKD</option>
        <option value="USD">USD</option>
      </select>
      <label htmlFor={`account-cash${fieldSuffix}`}>现金余额（可选）</label>
      <input
        id={`account-cash${fieldSuffix}`}
        inputMode="decimal"
        value={cash}
        aria-invalid={cashError === undefined ? undefined : true}
        aria-describedby={
          cashError === undefined
            ? undefined
            : `account-cash-error${fieldSuffix}`
        }
        onChange={(event) => setCash(event.currentTarget.value)}
      />
      {cashError === undefined ? null : (
        <p className="field-error" id={`account-cash-error${fieldSuffix}`}>
          {cashError}
        </p>
      )}
      <button disabled={mutation.isPending || name.trim() === ""} type="submit">
        {mutation.isPending
          ? account === undefined
            ? "正在创建…"
            : "正在保存…"
          : account === undefined
            ? "创建账户"
            : "保存账户"}
      </button>
      {account === undefined || onCancel === undefined ? null : (
        <button type="button" onClick={onCancel}>
          取消编辑
        </button>
      )}
    </form>
  );
}
