import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, get, newIdempotencyKey, request, type Account, type DirectoryAccount } from "../api";
import { formatMoney, parseToMinor } from "../money";
import { Field } from "./ui";

type Mode = "deposit" | "withdraw" | "transfer";

const MODES: { id: Mode; label: string; verb: string; done: string }[] = [
  { id: "deposit", label: "Deposit", verb: "Deposit", done: "Deposited" },
  { id: "withdraw", label: "Withdraw", verb: "Withdraw", done: "Withdrew" },
  { id: "transfer", label: "Transfer", verb: "Send", done: "Sent" },
];

const FRIENDLY: Record<string, string> = {
  INSUFFICIENT_FUNDS: "Not enough money in this account for that amount.",
  ACCOUNT_FROZEN: "This account is frozen, so money can't move in or out right now.",
  CONCURRENT_CONFLICT: "This request is still being processed. Retrying is safe: it won't be applied twice.",
  IDEMPOTENCY_KEY_REUSED: "This request changed after it was first sent. Edit the form and send it again.",
  SAME_ACCOUNT: "Choose a different account to send to.",
  ACCOUNT_NOT_FOUND: "That destination account doesn't exist.",
  NETWORK_ERROR: "Couldn't reach the server. Retrying is safe: it won't be applied twice.",
};

interface Result {
  tone: "ok" | "error" | "retry";
  text: string;
}

/**
 * The idempotency key is created once per *intended* operation and kept until
 * it succeeds or the person changes the form. So a double-click, a flaky
 * network or a "Retry" press all reuse the same key and can never post twice.
 */
export function MoveMoney({ account, onPosted }: { account: Account; onPosted: () => void }) {
  const [mode, setMode] = useState<Mode>("deposit");
  const [amount, setAmount] = useState("");
  const [destination, setDestination] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [payees, setPayees] = useState<DirectoryAccount[]>([]);
  const keyRef = useRef<string>(newIdempotencyKey());

  const minor = parseToMinor(amount);
  const fingerprint = `${mode}|${account.id}|${minor}|${destination}|${note}`;
  const lastFingerprint = useRef(fingerprint);
  if (lastFingerprint.current !== fingerprint) {
    // The intended operation changed: it gets a new key.
    lastFingerprint.current = fingerprint;
    keyRef.current = newIdempotencyKey();
  }

  useEffect(() => {
    get<{ accounts: DirectoryAccount[] }>("/v1/accounts/directory")
      .then((d) => setPayees(d.accounts.filter((a) => a.id !== account.id)))
      .catch(() => setPayees([]));
  }, [account.id]);

  useEffect(() => {
    setResult(null);
  }, [mode, account.id]);

  const frozen = account.status === "FROZEN";
  const meta = MODES.find((m) => m.id === mode)!;
  const canSubmit = !busy && !frozen && minor !== null && (mode !== "transfer" || destination !== "");
  const preview = useMemo(() => (minor ? formatMoney(minor, account.currency) : null), [minor, account.currency]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || minor === null) return;
    setBusy(true);
    setResult(null);
    const description = note.trim() || undefined;
    const path = mode === "deposit" ? "/v1/deposits" : mode === "withdraw" ? "/v1/withdrawals" : "/v1/transfers";
    const body =
      mode === "transfer"
        ? { source_account_id: account.id, destination_account_id: destination, amount: minor,
            currency: account.currency.trim(), description }
        : { account_id: account.id, amount: minor, currency: account.currency.trim(), description };
    try {
      const { replayed } = await request(path, { method: "POST", body, idempotencyKey: keyRef.current });
      setResult({
        tone: "ok",
        text: `${meta.done} ${formatMoney(minor, account.currency)}.${replayed ? " (Already recorded earlier; nothing was posted twice.)" : ""}`,
      });
      setAmount("");
      setNote("");
      keyRef.current = newIdempotencyKey();
      onPosted();
    } catch (err) {
      const apiErr = err as ApiError;
      const retryable = apiErr.status === 0 || apiErr.status === 409 || apiErr.status === 503;
      let text = FRIENDLY[apiErr.code] ?? apiErr.message;
      const available = apiErr.details.available;
      if (apiErr.code === "INSUFFICIENT_FUNDS" && typeof available === "number") {
        text = `Not enough money: ${formatMoney(available, account.currency)} available.`;
      }
      setResult({ tone: retryable ? "retry" : "error", text });
      if (!retryable) onPosted(); // a refusal is final for this key; refresh in case balances moved
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-describedby="move-money-help">
      <div className="inline-flex rounded-md border border-rule p-0.5" role="radiogroup" aria-label="Operation">
        {MODES.map((m) => (
          <button
            type="button"
            key={m.id}
            role="radio"
            aria-checked={mode === m.id}
            onClick={() => setMode(m.id)}
            className={`rounded px-3 py-1.5 text-sm font-medium ${
              mode === m.id ? "bg-green text-white" : "text-ink-soft hover:text-ink"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {frozen && (
        <p className="text-sm text-debit">This account is frozen by an administrator. Money can't move in or out.</p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount" hint={preview ? `${preview} will be ${mode === "deposit" ? "added" : "taken"}` : "Up to two decimal places"}>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-soft">₹</span>
            <input
              className="field num pl-7"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              aria-invalid={amount !== "" && minor === null}
            />
          </div>
        </Field>
        {mode === "transfer" ? (
          <Field label="To">
            <select className="field" value={destination} onChange={(e) => setDestination(e.target.value)}>
              <option value="">Choose an account</option>
              {payees.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.owner_name} — {p.name}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field label="Note" hint="Optional, shown on your statement">
            <input className="field" maxLength={280} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        )}
      </div>
      {mode === "transfer" && (
        <Field label="Note" hint="Optional, shown on both statements">
          <input className="field" maxLength={280} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={!canSubmit}>
          {busy ? "Sending…" : result?.tone === "retry" ? "Retry" : `${meta.verb}${preview ? ` ${preview}` : ""}`}
        </button>
        <span id="move-money-help" className="text-xs text-ink-soft">
          Request <span className="num">{keyRef.current.slice(0, 8)}</span>. Retrying never charges twice.
        </span>
      </div>

      {result && (
        <p
          role="status"
          className={`rounded-sm border-l-2 px-3 py-2 text-sm ${
            result.tone === "ok"
              ? "border-green bg-green-wash text-green-deep"
              : result.tone === "retry"
                ? "border-seal bg-seal-wash text-seal"
                : "border-debit bg-debit-wash text-debit"
          }`}
        >
          {result.text}
        </p>
      )}
    </form>
  );
}
