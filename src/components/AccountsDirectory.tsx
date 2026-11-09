import { useState } from "react";
import { ApiError, get, post, type Account } from "../api";
import { useLoad } from "../hooks";
import { Statement } from "./Statement";
import { ErrorNote, Loading, Money, Panel, StatusMark } from "./ui";

/** All accounts, with a statement drill-down. Admins can also freeze and unfreeze. */
export function AccountsDirectory({ canManage, refreshKey = 0 }: { canManage: boolean; refreshKey?: number }) {
  const [tick, setTick] = useState(0);
  const { data, error, loading } = useLoad(() => get<{ accounts: Account[] }>("/v1/accounts"), [tick, refreshKey]);
  const [open, setOpen] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  async function setStatus(a: Account, status: "ACTIVE" | "FROZEN") {
    setActionError(null);
    const reason = status === "FROZEN" ? window.prompt(`Reason for freezing ${a.name}?`) ?? undefined : undefined;
    try {
      await post(`/v1/accounts/${a.id}/status`, { status, reason: reason || undefined });
      setTick((t) => t + 1);
    } catch (e) {
      setActionError(e as ApiError);
    }
  }

  const accounts = data?.accounts ?? [];
  const opened = accounts.find((a) => a.id === open);

  return (
    <div className="space-y-6">
      <Panel title="Accounts">
        <ErrorNote error={error ?? actionError} />
        {loading && !data ? <Loading /> : (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="ledger-head text-left text-ink-soft">
                  <th className="py-2 pl-5 pr-3 font-medium">Account</th>
                  <th className="py-2 pr-3 font-medium">Owner</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 text-right font-medium">Balance</th>
                  <th className="py-2 pr-5 text-right font-medium"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.id} className={`border-b border-rule-soft ${a.id === open ? "bg-green-wash" : ""}`}>
                    <td className="py-2 pl-5 pr-3 font-medium">
                      {a.name}
                      {a.account_type === "SYSTEM" && (
                        <span className="block text-xs font-normal text-ink-soft">
                          Mirrors money that entered or left the system
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">{a.owner_name ?? <span className="text-ink-soft">System</span>}</td>
                    <td className="py-2 pr-3"><StatusMark status={a.status} /></td>
                    <td className="py-2 pr-3 text-right">
                      <Money value={a.balance} currency={a.currency} tone={a.balance < 0 ? "debit" : "ink"} />
                    </td>
                    <td className="whitespace-nowrap py-2 pr-5 text-right">
                      <button className="btn-quiet mr-2 text-sm" onClick={() => setOpen(a.id === open ? null : a.id)}>
                        {a.id === open ? "Close statement" : "Statement"}
                      </button>
                      {canManage && a.account_type === "USER" && (
                        <button
                          className="btn-quiet text-sm"
                          onClick={() => setStatus(a, a.status === "ACTIVE" ? "FROZEN" : "ACTIVE")}
                        >
                          {a.status === "ACTIVE" ? "Freeze" : "Unfreeze"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {opened && (
        <Panel title={`Statement for ${opened.name}`}>
          <Statement accountId={opened.id} refreshKey={tick} />
        </Panel>
      )}
    </div>
  );
}
