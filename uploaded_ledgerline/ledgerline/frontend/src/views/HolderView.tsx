import { useEffect, useState } from "react";
import { get, type Account } from "../api";
import { MoveMoney } from "../components/MoveMoney";
import { Statement } from "../components/Statement";
import { Empty, ErrorNote, Loading, Money, Panel, StatusMark } from "../components/ui";
import { useLoad } from "../hooks";

export function HolderView() {
  const [refresh, setRefresh] = useState(0);
  const { data, error, loading } = useLoad(() => get<{ accounts: Account[] }>("/v1/accounts"), [refresh]);
  const [selected, setSelected] = useState<string | null>(null);

  const accounts = data?.accounts ?? [];
  useEffect(() => {
    if (!selected && accounts.length) setSelected(accounts[0].id);
  }, [accounts, selected]);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorNote error={error} />;
  if (accounts.length === 0) {
    return <Empty>You don&apos;t have an account yet. An administrator can open one for you.</Empty>;
  }

  const current = accounts.find((a) => a.id === selected) ?? accounts[0];
  const total = accounts.reduce((sum, a) => sum + a.balance, 0);

  return (
    <div className="grid gap-6 lg:grid-cols-[17rem_1fr]">
      <aside>
        <h2 className="mb-3 text-sm font-medium text-ink-soft">Your accounts</h2>
        <ul className="overflow-hidden rounded-md border border-rule bg-surface">
          {accounts.map((a) => {
            const active = a.id === current.id;
            return (
              <li key={a.id} className="border-b border-rule-soft last:border-b-0">
                <button
                  onClick={() => setSelected(a.id)}
                  aria-current={active}
                  className={`flex w-full items-baseline justify-between gap-3 border-l-4 px-4 py-3 text-left ${
                    active ? "border-green bg-green-wash" : "border-transparent hover:bg-paper"
                  }`}
                >
                  <span>
                    <span className="block font-medium">{a.name}</span>
                    {a.status === "FROZEN" && <StatusMark status={a.status} />}
                  </span>
                  <Money value={a.balance} currency={a.currency} />
                </button>
              </li>
            );
          })}
        </ul>
        {accounts.length > 1 && (
          <p className="mt-3 flex justify-between px-1 text-sm">
            <span className="text-ink-soft">Total</span>
            <Money value={total} />
          </p>
        )}
      </aside>

      <div className="space-y-6">
        <section aria-label="Balance">
          <p className="text-sm text-ink-soft">{current.name}</p>
          <p className="mt-1">
            <Money value={current.balance} currency={current.currency} size="xl" />
          </p>
        </section>

        <Panel title="Move money">
          <MoveMoney account={current} onPosted={() => setRefresh((r) => r + 1)} />
        </Panel>

        <Panel title="Statement">
          <Statement accountId={current.id} refreshKey={refresh} />
        </Panel>
      </div>
    </div>
  );
}
