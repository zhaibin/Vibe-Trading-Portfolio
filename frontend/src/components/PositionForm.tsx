import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type MutableRefObject, useRef, useState } from "react";

import { ApiError, api, newIdempotencyKey } from "../api/client";
import {
  holdingsKeys,
  type Account,
  type Instrument,
  type InstrumentCandidate,
  type Position,
} from "../api/queries";
import type { Notice } from "./StatusMessage";

const QUANTITY = /^\d+(?:\.\d{1,8})?$/;
const MONEY = /^\d+(?:\.\d{1,6})?$/;

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

export function PositionEditForm({
  account,
  onCancel,
  onNotice,
  position,
}: {
  account: Account;
  onCancel: () => void;
  onNotice: (notice: Notice) => void;
  position: Position;
}) {
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState(position.quantity);
  const [averageCost, setAverageCost] = useState(position.average_cost);
  const [note, setNote] = useState(position.note ?? "");
  const [quantityError, setQuantityError] = useState<string>();
  const [costError, setCostError] = useState<string>();
  const [noteError, setNoteError] = useState<string>();
  const pending = useRef<PendingSubmission | undefined>(undefined);
  const mutation = useMutation({
    mutationFn: ({
      payload,
      key,
    }: {
      payload: {
        version: number;
        quantity: string;
        average_cost: string;
        note: string | null;
      };
      key: string;
    }) =>
      api.patch("/api/v1/positions/{position_id}", payload, key, {
        params: { path: { position_id: position.id } },
      }),
    onSuccess: async () => {
      pending.current = undefined;
      onNotice({ kind: "success", title: "持仓已更新" });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.positions(false),
        }),
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.summary(account.currency),
        }),
      ]);
      onCancel();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
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
                .invalidateQueries({ queryKey: holdingsKeys.positions(false) })
                .then(() => {
                  onCancel();
                  onNotice({ kind: "success", title: "已重新载入最新记录" });
                });
            },
          },
        });
        return;
      }
      if (error instanceof ApiError && error.fields?.note !== undefined) {
        setNoteError("备注包含不支持的字符或长度过长");
      }
      if (error instanceof ApiError && error.fields?.quantity !== undefined) {
        setQuantityError("持仓数量必须大于 0 且最多保留 8 位小数");
      }
      if (
        error instanceof ApiError &&
        error.fields?.average_cost !== undefined
      ) {
        setCostError("平均成本必须是不小于 0 且最多保留 6 位小数的数值");
      }
      onNotice({ kind: "error", title: "暂时无法保存持仓，请重试" });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuantity = quantity.trim();
    const normalizedCost = averageCost.trim();
    const nextQuantityError =
      !QUANTITY.test(normalizedQuantity) ||
      /^0+(?:\.0+)?$/.test(normalizedQuantity)
        ? /^\d+\.\d{9,}$/.test(normalizedQuantity)
          ? "持仓数量最多保留 8 位小数"
          : "持仓数量必须大于 0"
        : undefined;
    const nextCostError = !MONEY.test(normalizedCost)
      ? /^\d+\.\d{7,}$/.test(normalizedCost)
        ? "平均成本最多保留 6 位小数"
        : "平均成本必须是不小于 0 的数值"
      : undefined;
    setQuantityError(nextQuantityError);
    setCostError(nextCostError);
    setNoteError(undefined);
    if (nextQuantityError !== undefined || nextCostError !== undefined) return;
    const payload = {
      version: position.version,
      quantity: normalizedQuantity,
      average_cost: normalizedCost,
      note: note.trim() === "" ? null : note,
    };
    mutation.mutate({ payload, key: retainedKey(pending, payload) });
  }

  const suffix = `-${position.id}`;
  return (
    <form
      className="stack-form"
      aria-label={`编辑持仓 ${position.instrument.name} ${position.instrument.canonical_symbol} ${account.name}`}
      onSubmit={submit}
      noValidate
    >
      <h3>编辑持仓</h3>
      <label htmlFor={`edit-position-quantity${suffix}`}>持仓数量</label>
      <input
        id={`edit-position-quantity${suffix}`}
        value={quantity}
        inputMode="decimal"
        aria-describedby={
          quantityError === undefined
            ? undefined
            : `edit-position-quantity-error${suffix}`
        }
        onChange={(event) => setQuantity(event.currentTarget.value)}
      />
      {quantityError === undefined ? null : (
        <p className="field-error" id={`edit-position-quantity-error${suffix}`}>
          {quantityError}
        </p>
      )}
      <label htmlFor={`edit-position-cost${suffix}`}>平均成本</label>
      <input
        id={`edit-position-cost${suffix}`}
        value={averageCost}
        inputMode="decimal"
        aria-describedby={
          costError === undefined
            ? undefined
            : `edit-position-cost-error${suffix}`
        }
        onChange={(event) => setAverageCost(event.currentTarget.value)}
      />
      {costError === undefined ? null : (
        <p className="field-error" id={`edit-position-cost-error${suffix}`}>
          {costError}
        </p>
      )}
      <label htmlFor={`edit-position-note${suffix}`}>备注（可选）</label>
      <textarea
        id={`edit-position-note${suffix}`}
        value={note}
        aria-describedby={
          noteError === undefined
            ? undefined
            : `edit-position-note-error${suffix}`
        }
        onChange={(event) => setNote(event.currentTarget.value)}
      />
      {noteError === undefined ? null : (
        <p className="field-error" id={`edit-position-note-error${suffix}`}>
          {noteError}
        </p>
      )}
      <button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? "正在保存…" : "保存持仓"}
      </button>
      <button type="button" onClick={onCancel}>
        取消编辑
      </button>
    </form>
  );
}

export function PositionForm({
  accounts,
  onNotice,
}: {
  accounts: Account[];
  onNotice: (notice: Notice | undefined) => void;
}) {
  const queryClient = useQueryClient();
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [searchText, setSearchText] = useState("");
  const [candidates, setCandidates] = useState<InstrumentCandidate[]>([]);
  const [confirmed, setConfirmed] = useState<Instrument>();
  const [quantity, setQuantity] = useState("");
  const [averageCost, setAverageCost] = useState("");
  const [note, setNote] = useState("");
  const [quantityError, setQuantityError] = useState<string>();
  const [costError, setCostError] = useState<string>();
  const [noteError, setNoteError] = useState<string>();
  const confirmPending = useRef<PendingSubmission | undefined>(undefined);
  const positionPending = useRef<PendingSubmission | undefined>(undefined);
  const searchController = useRef<AbortController | undefined>(undefined);
  const searchRequestId = useRef(0);

  const search = useMutation({
    mutationFn: ({
      q,
      signal,
    }: {
      q: string;
      requestId: number;
      signal: AbortSignal;
    }) => {
      return api.get("/api/v1/instruments/search", {
        params: { query: { q, limit: 10 } },
        signal,
      });
    },
    onSuccess: (results, request) => {
      if (request.requestId !== searchRequestId.current) return;
      setCandidates(results);
      setConfirmed(undefined);
      if (results.length === 0) {
        onNotice({ kind: "success", title: "没有找到匹配的证券" });
      } else {
        onNotice(undefined);
      }
    },
    onError: (error, request) => {
      if (
        request.requestId !== searchRequestId.current ||
        (error instanceof Error && error.name === "AbortError")
      ) {
        return;
      }
      onNotice({ kind: "error", title: "证券搜索暂时不可用，请重试" });
    },
  });

  const confirmation = useMutation({
    mutationFn: ({
      candidateId,
      key,
    }: {
      candidateId: string;
      key: string;
      requestId: number;
    }) =>
      api.post(
        "/api/v1/instruments/confirm",
        { candidate_id: candidateId },
        key,
      ),
    onSuccess: (instrument, request) => {
      if (request.requestId !== searchRequestId.current) return;
      confirmPending.current = undefined;
      setConfirmed(instrument);
    },
    onError: (_error, request) => {
      if (request.requestId !== searchRequestId.current) return;
      onNotice({ kind: "error", title: "无法确认证券，请重新搜索" });
    },
  });

  const creation = useMutation({
    mutationFn: ({
      payload,
      key,
    }: {
      payload: {
        account_id: string;
        instrument_id: string;
        quantity: string;
        average_cost: string;
        note: string | null;
      };
      key: string;
    }) => api.post("/api/v1/positions", payload, key),
    onSuccess: async (_position, { payload }) => {
      const account = accounts.find((item) => item.id === payload.account_id);
      positionPending.current = undefined;
      setConfirmed(undefined);
      setCandidates([]);
      setSearchText("");
      setQuantity("");
      setAverageCost("");
      setNote("");
      setNoteError(undefined);
      onNotice({ kind: "success", title: "持仓已创建" });
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: holdingsKeys.positions(false),
        }),
        ...(account === undefined
          ? []
          : [
              queryClient.invalidateQueries({
                queryKey: holdingsKeys.summary(account.currency),
              }),
            ]),
      ]);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields?.note !== undefined) {
        setNoteError("备注包含不支持的字符或长度过长");
      }
      if (error instanceof ApiError && error.fields?.quantity !== undefined) {
        setQuantityError("持仓数量必须大于 0 且最多保留 8 位小数");
      }
      if (
        error instanceof ApiError &&
        error.fields?.average_cost !== undefined
      ) {
        setCostError("平均成本必须是不小于 0 且最多保留 6 位小数的数值");
      }
      onNotice({ kind: "error", title: "暂时无法保存持仓，请重试" });
    },
  });

  const selectedAccountId = accounts.some((item) => item.id === accountId)
    ? accountId
    : (accounts[0]?.id ?? "");
  const account = accounts.find((item) => item.id === selectedAccountId);
  const currencyMismatch =
    account !== undefined &&
    confirmed !== undefined &&
    account.currency !== confirmed.currency;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const q = searchText.trim();
    if (q !== "") {
      searchController.current?.abort();
      const controller = new AbortController();
      const requestId = searchRequestId.current + 1;
      searchController.current = controller;
      searchRequestId.current = requestId;
      confirmPending.current = undefined;
      setCandidates([]);
      setConfirmed(undefined);
      search.mutate({ q, requestId, signal: controller.signal });
    }
  }

  function submitPosition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (confirmed === undefined || account === undefined || currencyMismatch) {
      return;
    }
    const normalizedQuantity = quantity.trim();
    const normalizedCost = averageCost.trim();
    let invalid = false;
    if (
      !QUANTITY.test(normalizedQuantity) ||
      /^0+(?:\.0+)?$/.test(normalizedQuantity)
    ) {
      setQuantityError(
        /^\d+\.\d{9,}$/.test(normalizedQuantity)
          ? "持仓数量最多保留 8 位小数"
          : "持仓数量必须大于 0",
      );
      invalid = true;
    } else {
      setQuantityError(undefined);
    }
    if (!MONEY.test(normalizedCost)) {
      setCostError(
        /^\d+\.\d{7,}$/.test(normalizedCost)
          ? "平均成本最多保留 6 位小数"
          : "平均成本必须是不小于 0 的数值",
      );
      invalid = true;
    } else {
      setCostError(undefined);
    }
    if (invalid) {
      return;
    }
    setNoteError(undefined);
    const payload = {
      account_id: account.id,
      instrument_id: confirmed.id,
      quantity: normalizedQuantity,
      average_cost: normalizedCost,
      note: note.trim() === "" ? null : note,
    };
    creation.mutate({ payload, key: retainedKey(positionPending, payload) });
  }

  return (
    <section aria-labelledby="new-position-heading">
      <h2 id="new-position-heading">添加持仓</h2>
      <label htmlFor="position-account">所属账户</label>
      <select
        id="position-account"
        value={selectedAccountId}
        onChange={(event) => {
          setAccountId(event.currentTarget.value);
          setConfirmed(undefined);
        }}
      >
        {accounts.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}（{item.currency}）
          </option>
        ))}
      </select>
      <form className="inline-form" onSubmit={submitSearch}>
        <label htmlFor="instrument-search">证券代码或名称</label>
        <input
          id="instrument-search"
          value={searchText}
          onChange={(event) => setSearchText(event.currentTarget.value)}
        />
        <button disabled={searchText.trim() === ""} type="submit">
          {search.isPending ? "正在搜索…" : "搜索"}
        </button>
      </form>
      {candidates.length === 0 ? null : (
        <div className="candidate-list" aria-label="证券搜索结果">
          {candidates.map((candidate) => (
            <article
              className="candidate-card"
              key={candidate.candidate_id}
              aria-label={`${candidate.name} ${candidate.canonical_symbol}`}
            >
              <h3>
                {candidate.name} {candidate.canonical_symbol}
              </h3>
              <p>
                {candidate.market} · {candidate.asset_type} ·{" "}
                {candidate.currency}
              </p>
              <p>来源：{candidate.sources.join("、")}</p>
              <button
                type="button"
                disabled={confirmation.isPending}
                onClick={() => {
                  const payload = { candidate_id: candidate.candidate_id };
                  confirmation.mutate({
                    candidateId: candidate.candidate_id,
                    key: retainedKey(confirmPending, payload),
                    requestId: searchRequestId.current,
                  });
                }}
              >
                确认 {candidate.name} {candidate.canonical_symbol}
              </button>
            </article>
          ))}
        </div>
      )}
      {confirmed === undefined ? null : (
        <form className="stack-form" onSubmit={submitPosition} noValidate>
          <p>
            已确认：{confirmed.name} {confirmed.canonical_symbol}（
            {confirmed.currency}）
          </p>
          {currencyMismatch ? (
            <p className="field-error" id="position-currency-error">
              证券币种 {confirmed.currency} 与账户币种 {account?.currency}{" "}
              不一致
            </p>
          ) : null}
          <label htmlFor="position-quantity">持仓数量</label>
          <input
            id="position-quantity"
            inputMode="decimal"
            value={quantity}
            aria-describedby={
              quantityError === undefined
                ? undefined
                : "position-quantity-error"
            }
            onChange={(event) => setQuantity(event.currentTarget.value)}
          />
          {quantityError === undefined ? null : (
            <p className="field-error" id="position-quantity-error">
              {quantityError}
            </p>
          )}
          <label htmlFor="position-average-cost">平均成本</label>
          <input
            id="position-average-cost"
            inputMode="decimal"
            value={averageCost}
            aria-describedby={
              costError === undefined ? undefined : "position-cost-error"
            }
            onChange={(event) => setAverageCost(event.currentTarget.value)}
          />
          {costError === undefined ? null : (
            <p className="field-error" id="position-cost-error">
              {costError}
            </p>
          )}
          <label htmlFor="position-note">备注（可选）</label>
          <textarea
            id="position-note"
            value={note}
            aria-describedby={
              noteError === undefined ? undefined : "position-note-error"
            }
            onChange={(event) => setNote(event.currentTarget.value)}
          />
          {noteError === undefined ? null : (
            <p className="field-error" id="position-note-error">
              {noteError}
            </p>
          )}
          <button
            type="submit"
            disabled={creation.isPending || currencyMismatch}
          >
            {creation.isPending ? "正在添加…" : "添加持仓"}
          </button>
        </form>
      )}
    </section>
  );
}
