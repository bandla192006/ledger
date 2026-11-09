import { useState } from "react";
import { ApiError, get, post, type InvariantReport } from "../api";
import { useLoad } from "../hooks";
import { formatDateTime } from "../money";
import { ErrorNote, Panel } from "./ui";

const PLAIN: Record<string, string> = {
  double_entry_balanced: "Every transaction balances",
  global_debits_equal_credits: "Total debits equal total credits",
  zero_sum_balances: "All balances sum to zero",
  external_flow_reconciles: "Deposits minus withdrawals match what customers hold",
  internal_transfers_net_zero: "Transfers between customers net to zero",
  no_negative_user_balances: "No customer account is overdrawn",
  cached_balance_matches_ledger: "Every balance matches its ledger",
  running_balance_continuity: "Running balances are continuous",
  hash_chain_intact: "Hash chain is unbroken",
  idempotency_consistent: "Every transaction has exactly one request key",
};

interface RunSummary {
  id: number;
  passed: boolean;
  created_at: string;
  run_by_email: string;
}

export function Integrity() {
  const [report, setReport] = useState<InvariantReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [running, setRunning] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const history = useLoad(() => get<{ runs: RunSummary[] }>("/v1/invariants/runs?limit=10"), []);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setReport(await post<InvariantReport>("/v1/invariants/verify"));
      history.reload();
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setRunning(false);
    }
  }

  const failed = report?.checks.filter((c) => !c.passed).length ?? 0;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
      <Panel
        title="Ledger integrity"
        aside={
          <button className="btn-primary" onClick={run} disabled={running}>
            {running ? "Verifying…" : "Run verification"}
          </button>
        }
      >
        <p className="mb-5 max-w-prose text-sm text-ink-soft">
          Recomputes ten invariants over one consistent snapshot of the database, including every SHA-256 hash,
          independently of the database&apos;s own triggers. Each run is recorded.
        </p>
        <ErrorNote error={error} />
        {report && (
          <>
            <div
              className={`mb-5 flex items-center gap-4 rounded-md border-2 px-4 py-3 ${
                report.passed ? "border-seal text-seal" : "border-debit text-debit"
              }`}
            >
              <span
                aria-hidden
                className={`grid h-12 w-12 shrink-0 place-items-center rounded-full border-2 text-xl font-semibold ${
                  report.passed ? "border-seal" : "border-debit"
                }`}
              >
                {report.passed ? "✓" : "!"}
              </span>
              <div>
                <p className="font-semibold">
                  {report.passed
                    ? `All ${report.checks.length} checks passed`
                    : `${failed} of ${report.checks.length} checks failed`}
                </p>
                <p className="num text-sm opacity-80">
                  Run {report.run_id} at {formatDateTime(report.run_at)}, covering {report.totals.transactions}{" "}
                  transactions and {report.totals.ledger_entries} entries
                </p>
              </div>
            </div>
            <ul className="divide-y divide-rule-soft">
              {report.checks.map((c) => (
                <li key={c.name}>
                  <button
                    className="flex w-full items-start gap-3 py-2.5 text-left"
                    aria-expanded={open === c.name}
                    onClick={() => setOpen(open === c.name ? null : c.name)}
                  >
                    <span className={`mt-0.5 w-5 shrink-0 font-semibold ${c.passed ? "text-seal" : "text-debit"}`}>
                      {c.passed ? "✓" : "✗"}
                    </span>
                    <span>
                      <span className="block font-medium">{PLAIN[c.name] ?? c.name}</span>
                      <span className="block text-sm text-ink-soft">{c.description}</span>
                    </span>
                  </button>
                  {open === c.name && (
                    <pre className="num mb-3 ml-8 overflow-x-auto rounded-sm bg-paper p-3 text-xs">
                      {JSON.stringify(c.details, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {!report && !error && (
          <p className="text-sm">Run a verification to see each invariant and its evidence.</p>
        )}
      </Panel>

      <Panel title="Recent runs" className="self-start">
        {history.data?.runs.length ? (
          <ul className="space-y-2 text-sm">
            {history.data.runs.map((r) => (
              <li key={r.id} className="flex items-baseline justify-between gap-2">
                <span className={r.passed ? "text-seal" : "text-debit"}>{r.passed ? "Passed" : "Failed"}</span>
                <span className="num text-ink-soft">{formatDateTime(r.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-soft">No runs yet.</p>
        )}
      </Panel>
    </div>
  );
}
