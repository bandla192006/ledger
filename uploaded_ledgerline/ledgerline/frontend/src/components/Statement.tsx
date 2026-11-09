import { useEffect, useState } from "react";
import { ApiError, get, type Statement as StatementData, type StatementEntry } from "../api";
import { formatDateTime } from "../money";
import { Empty, ErrorNote, Hash, Loading, Money } from "./ui";

const KIND: Record<StatementEntry["transaction_type"], string> = {
  DEPOSIT: "Deposit",
  WITHDRAWAL: "Withdrawal",
  TRANSFER: "Transfer",
};

function describe(e: StatementEntry): string {
  if (e.transaction_type === "TRANSFER") {
    const who = e.counterparty_owner_name ?? "another account";
    const where = e.counterparty_account_name ? ` (${e.counterparty_account_name})` : "";
    return `${e.entry_type === "DEBIT" ? "To" : "From"} ${who}${where}`;
  }
  return e.external_ref ? `Ref ${e.external_ref}` : e.transaction_type === "DEPOSIT" ? "Money in" : "Money out";
}

/**
 * A ledger page: debit and credit in separate columns, the running balance,
 * and each entry's hash stub in the margin where a folio number would go.
 */
export function Statement({ accountId, refreshKey = 0 }: { accountId: string; refreshKey?: number }) {
  const [pages, setPages] = useState<StatementData[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    get<StatementData>(`/v1/accounts/${accountId}/statement?limit=25`)
      .then((d) => !cancelled && (setPages([d]), setError(null)))
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [accountId, refreshKey]);

  const last = pages[pages.length - 1];
  const entries = pages.flatMap((p) => p.entries);

  async function loadOlder() {
    if (!last?.next_before_seq) return;
    try {
      const more = await get<StatementData>(
        `/v1/accounts/${accountId}/statement?limit=25&before_seq=${last.next_before_seq}`,
      );
      setPages((p) => [...p, more]);
    } catch (e) {
      setError(e as ApiError);
    }
  }

  if (loading && pages.length === 0) return <Loading />;
  if (error && pages.length === 0) return <ErrorNote error={error} />;
  if (!last) return null;
  const currency = last.account.currency;

  return (
    <div>
      <dl className="mb-4 flex flex-wrap gap-x-8 gap-y-1 text-sm">
        <div>
          <dt className="text-ink-soft">Money in</dt>
          <dd><Money value={last.summary.total_credits} currency={currency} tone="credit" /></dd>
        </div>
        <div>
          <dt className="text-ink-soft">Money out</dt>
          <dd><Money value={last.summary.total_debits} currency={currency} tone="debit" /></dd>
        </div>
        <div>
          <dt className="text-ink-soft">Entries</dt>
          <dd className="num">{last.summary.entry_count}</dd>
        </div>
      </dl>

      {entries.length === 0 ? (
        <Empty>No entries yet. Make a deposit to open this ledger.</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="ledger-head text-left text-ink-soft">
                <th className="w-10 py-2 pl-5 pr-2 font-medium" title="Entry number in this account">No.</th>
                <th className="py-2 pr-3 font-medium">Date</th>
                <th className="py-2 pr-3 font-medium">Particulars</th>
                <th className="py-2 pr-3 text-right font-medium">Debit</th>
                <th className="py-2 pr-3 text-right font-medium">Credit</th>
                <th className="border-l border-rule py-2 pl-3 pr-3 text-right font-medium">Balance</th>
                <th className="py-2 pr-5 text-right font-medium" title="SHA-256 hash, chained to the previous entry">
                  Seal
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b border-rule-soft align-top">
                  <td className="num py-2.5 pl-5 pr-2 text-ink-soft">{e.account_seq}</td>
                  <td className="num whitespace-nowrap py-2.5 pr-3 text-ink-soft">{formatDateTime(e.created_at)}</td>
                  <td className="py-2.5 pr-3">
                    <span className="font-medium">{KIND[e.transaction_type]}</span>{" "}
                    <span className="text-ink-soft">{describe(e)}</span>
                    {e.description && <span className="block text-ink-soft">{e.description}</span>}
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    {e.entry_type === "DEBIT" && <Money value={e.amount} currency={currency} tone="debit" />}
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    {e.entry_type === "CREDIT" && <Money value={e.amount} currency={currency} />}
                  </td>
                  <td className="border-l border-rule py-2.5 pl-3 pr-3 text-right font-medium">
                    <Money value={e.balance_after} currency={currency} />
                  </td>
                  <td className="py-2.5 pr-5 text-right"><Hash value={e.entry_hash} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {last.next_before_seq && (
        <div className="mt-4 text-center">
          <button className="btn-quiet text-sm" onClick={loadOlder}>Show older entries</button>
        </div>
      )}
      {pages.length > 0 && <ErrorNote error={error} />}
    </div>
  );
}
