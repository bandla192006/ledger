import { useState } from "react";
import { get, type IdempotencyRecord, type LedgerEntry, type Refusal } from "../api";
import { useLoad } from "../hooks";
import { formatDateTime, shortId } from "../money";
import { Empty, ErrorNote, Hash, Loading, Money, Panel } from "./ui";

const PAGE = 50;

function Pager({ offset, count, onChange }: { offset: number; count: number; onChange: (o: number) => void }) {
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-ink-soft">
      <span className="num">
        {count === 0 ? "No rows" : `Rows ${offset + 1}–${offset + count}`}
      </span>
      <div className="flex gap-2">
        <button className="btn-quiet" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}>
          Newer
        </button>
        <button className="btn-quiet" disabled={count < PAGE} onClick={() => onChange(offset + PAGE)}>
          Older
        </button>
      </div>
    </div>
  );
}

export function GlobalLedger() {
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad(
    () => get<{ entries: LedgerEntry[] }>(`/v1/ledger/entries?limit=${PAGE}&offset=${offset}`),
    [offset],
  );
  return (
    <Panel title="Global ledger" aside={<button className="btn-quiet text-sm" onClick={reload}>Refresh</button>}>
      <p className="mb-4 max-w-prose text-sm text-ink-soft">
        Every posting across every account, newest first. Rows can't be edited or deleted; each seal is the
        SHA-256 hash chaining the entry to the one before it in the same account.
      </p>
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="ledger-head text-left text-ink-soft">
                <th className="py-2 pl-5 pr-3 font-medium">Date</th>
                <th className="py-2 pr-3 font-medium">Transaction</th>
                <th className="py-2 pr-3 font-medium">Account</th>
                <th className="py-2 pr-3 text-right font-medium">Debit</th>
                <th className="py-2 pr-3 text-right font-medium">Credit</th>
                <th className="border-l border-rule py-2 pl-3 pr-3 text-right font-medium">Balance after</th>
                <th className="py-2 pr-5 text-right font-medium">Seal</th>
              </tr>
            </thead>
            <tbody>
              {data?.entries.map((e) => (
                <tr key={e.id} className="border-b border-rule-soft">
                  <td className="num whitespace-nowrap py-2 pl-5 pr-3 text-ink-soft">{formatDateTime(e.created_at)}</td>
                  <td className="py-2 pr-3">
                    <span className="font-medium capitalize">{e.transaction_type.toLowerCase()}</span>{" "}
                    <span className="num text-xs text-ink-soft">{shortId(e.transaction_id)}</span>
                  </td>
                  <td className="py-2 pr-3">
                    {e.account_name}
                    <span className="num ml-1 text-xs text-ink-soft">#{e.account_seq}</span>
                  </td>
                  <td className="py-2 pr-3 text-right">
                    {e.entry_type === "DEBIT" && <Money value={e.amount} currency={e.currency} tone="debit" />}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    {e.entry_type === "CREDIT" && <Money value={e.amount} currency={e.currency} />}
                  </td>
                  <td className="border-l border-rule py-2 pl-3 pr-3 text-right"><Money value={e.balance_after} currency={e.currency} /></td>
                  <td className="py-2 pr-5 text-right"><Hash value={e.entry_hash} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && data.entries.length === 0 && <Empty>The ledger is empty.</Empty>}
        </div>
      )}
      <Pager offset={offset} count={data?.entries.length ?? 0} onChange={setOffset} />
    </Panel>
  );
}

const REASON_LABEL: Record<string, string> = {
  INSUFFICIENT_FUNDS: "Insufficient funds",
  UNAUTHORIZED: "Not the account owner",
  FORBIDDEN_ROLE: "Role not allowed",
  UNAUTHENTICATED: "Not signed in",
  CONCURRENT_CONFLICT: "Concurrent duplicate",
  IDEMPOTENCY_KEY_REUSED: "Key reused for a different request",
  MISSING_IDEMPOTENCY_KEY: "Missing request key",
  INVALID_IDEMPOTENCY_KEY: "Malformed request key",
  ACCOUNT_NOT_FOUND: "Unknown account",
  ACCOUNT_FROZEN: "Account frozen",
  SAME_ACCOUNT: "Same source and destination",
  CURRENCY_MISMATCH: "Currency mismatch",
  VALIDATION_ERROR: "Malformed request",
  LOGIN_FAILED: "Failed sign-in",
  INVARIANT_GUARD_TRIPPED: "Database guard tripped",
};

export function RefusalLog() {
  const [offset, setOffset] = useState(0);
  const [reason, setReason] = useState("");
  const { data, error, loading, reload } = useLoad(
    () =>
      get<{ refusals: Refusal[]; counts: { reason_code: string; n: number }[] }>(
        `/v1/audit/refusals?limit=${PAGE}&offset=${offset}${reason ? `&reason_code=${reason}` : ""}`,
      ),
    [offset, reason],
  );
  return (
    <Panel title="Refused attempts" aside={<button className="btn-quiet text-sm" onClick={reload}>Refresh</button>}>
      {data && data.counts.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          <button
            onClick={() => { setReason(""); setOffset(0); }}
            className={`rounded-sm border px-2 py-1 text-sm ${reason === "" ? "border-green bg-green-wash text-green-deep" : "border-rule"}`}
          >
            All
          </button>
          {data.counts.map((c) => (
            <button
              key={c.reason_code}
              onClick={() => { setReason(c.reason_code); setOffset(0); }}
              className={`rounded-sm border px-2 py-1 text-sm ${reason === c.reason_code ? "border-green bg-green-wash text-green-deep" : "border-rule"}`}
            >
              {REASON_LABEL[c.reason_code] ?? c.reason_code} <span className="num text-ink-soft">{c.n}</span>
            </button>
          ))}
        </div>
      )}
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="ledger-head text-left text-ink-soft">
                <th className="py-2 pl-5 pr-3 font-medium">When</th>
                <th className="py-2 pr-3 font-medium">Who</th>
                <th className="py-2 pr-3 font-medium">Attempt</th>
                <th className="py-2 pr-3 font-medium">Reason</th>
                <th className="py-2 pr-3 text-right font-medium">Requested</th>
                <th className="py-2 pr-5 text-right font-medium">Balance then</th>
              </tr>
            </thead>
            <tbody>
              {data?.refusals.map((r) => (
                <tr key={r.id} className="border-b border-rule-soft align-top">
                  <td className="num whitespace-nowrap py-2 pl-5 pr-3 text-ink-soft">{formatDateTime(r.created_at)}</td>
                  <td className="py-2 pr-3">{r.attempted_by_email ?? <span className="text-ink-soft">Anonymous</span>}</td>
                  <td className="py-2 pr-3">
                    <span className="capitalize">{r.action.replace(/^(DEPOSIT|WITHDRAWAL|TRANSFER|LOGIN)$/, (m) => m.toLowerCase())}</span>
                    {r.source_account_id && (
                      <span className="num block text-xs text-ink-soft">from {shortId(r.source_account_id)}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <span className="text-debit">{REASON_LABEL[r.reason_code] ?? r.reason_code}</span>
                    <span className="num ml-1 text-xs text-ink-soft">{r.http_status}</span>
                  </td>
                  <td className="py-2 pr-3 text-right">
                    {r.requested_amount !== null ? <Money value={r.requested_amount} /> : "—"}
                  </td>
                  <td className="py-2 pr-5 text-right">
                    {r.account_balance_snapshot !== null ? <Money value={r.account_balance_snapshot} /> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && data.refusals.length === 0 && <Empty>No refused attempts recorded.</Empty>}
        </div>
      )}
      <Pager offset={offset} count={data?.refusals.length ?? 0} onChange={setOffset} />
    </Panel>
  );
}

export function IdempotencyLog() {
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad(
    () => get<{ idempotency_keys: IdempotencyRecord[] }>(`/v1/audit/idempotency-keys?limit=${PAGE}&offset=${offset}`),
    [offset],
  );
  return (
    <Panel title="Request keys" aside={<button className="btn-quiet text-sm" onClick={reload}>Refresh</button>}>
      <p className="mb-4 max-w-prose text-sm text-ink-soft">
        Each money request carries a key. A repeat of the same key gets the stored response back instead of
        running again.
      </p>
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="ledger-head text-left text-ink-soft">
                <th className="py-2 pl-5 pr-3 font-medium">Created</th>
                <th className="py-2 pr-3 font-medium">User</th>
                <th className="py-2 pr-3 font-medium">Key</th>
                <th className="py-2 pr-3 font-medium">Request</th>
                <th className="py-2 pr-3 font-medium">Stored response</th>
                <th className="py-2 pr-5 font-medium">Transaction</th>
              </tr>
            </thead>
            <tbody>
              {data?.idempotency_keys.map((k) => (
                <tr key={`${k.user_email}-${k.idempotency_key}`} className="border-b border-rule-soft">
                  <td className="num whitespace-nowrap py-2 pl-5 pr-3 text-ink-soft">{formatDateTime(k.created_at)}</td>
                  <td className="py-2 pr-3">{k.user_email}</td>
                  <td className="num max-w-[14rem] truncate py-2 pr-3" title={k.idempotency_key}>{k.idempotency_key}</td>
                  <td className="py-2 pr-3">{k.request_path}</td>
                  <td className={`num py-2 pr-3 ${k.response_code && k.response_code >= 400 ? "text-debit" : ""}`}>
                    {k.status === "COMPLETED" ? k.response_code : "In flight"}
                  </td>
                  <td className="num py-2 pr-5 text-ink-soft">{shortId(k.transaction_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && data.idempotency_keys.length === 0 && <Empty>No requests yet.</Empty>}
        </div>
      )}
      <Pager offset={offset} count={data?.idempotency_keys.length ?? 0} onChange={setOffset} />
    </Panel>
  );
}
