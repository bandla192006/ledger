import { useState } from "react";
import { ApiError, request, setToken, type User } from "../api";
import { ErrorNote, Field } from "./ui";

const DEMO = [
  { email: "alice@ledgerline.dev", who: "Alice", role: "Account holder" },
  { email: "bob@ledgerline.dev", who: "Bob", role: "Account holder" },
  { email: "admin@ledgerline.dev", who: "Asha", role: "Admin" },
  { email: "auditor@ledgerline.dev", who: "Ravi", role: "Auditor" },
];
const DEMO_PASSWORD = "ledgerline-demo";

/** One balanced transaction, set the way it would be written in a bound ledger. */
function JournalExample() {
  return (
    <figure className="max-w-md">
      <table className="w-full text-sm">
        <thead>
          <tr className="ledger-head text-left text-ink-soft">
            <th className="py-2 pr-3 font-medium">Account</th>
            <th className="py-2 pr-3 text-right font-medium">Debit</th>
            <th className="py-2 text-right font-medium">Credit</th>
          </tr>
        </thead>
        <tbody className="num">
          <tr className="border-b border-rule-soft">
            <td className="py-2 pr-3">Alice — Main</td>
            <td className="py-2 pr-3 text-right text-debit">₹1,250.50</td>
            <td />
          </tr>
          <tr className="border-b border-rule-soft">
            <td className="py-2 pr-3 pl-6">Bob — Main</td>
            <td />
            <td className="py-2 text-right">₹1,250.50</td>
          </tr>
          <tr>
            <td className="py-2 pr-3 text-ink-soft">Net</td>
            <td className="border-t-2 border-ink py-2 pr-3 text-right" colSpan={2}>₹0.00</td>
          </tr>
        </tbody>
      </table>
      <figcaption className="mt-3 text-sm text-ink-soft">
        Dinner split: one debit, one equal credit, sealed with a hash chained to everything before it.
      </figcaption>
    </figure>
  );
}

export function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function signIn(e?: React.FormEvent, override?: { email: string; password: string }) {
    e?.preventDefault();
    const creds = override ?? { email, password };
    setBusy(true);
    setError(null);
    try {
      const { data } = await request<{ access_token: string; user: User }>("/v1/auth/login", {
        method: "POST",
        body: creds,
      });
      setToken(data.access_token);
      onLogin(data.user);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto grid min-h-screen max-w-6xl items-center gap-12 px-6 py-12 lg:grid-cols-[1.2fr_1fr]">
      <div>
        <p className="mb-8 text-lg font-semibold text-green">LedgerLine</p>
        <h1 className="mb-5 max-w-xl text-4xl font-light leading-tight tracking-tight sm:text-5xl">
          Every rupee is written twice, and nothing written is ever erased.
        </h1>
        <p className="mb-10 max-w-lg text-ink-soft">
          A double-entry ledger where balances can&apos;t go negative, retries can&apos;t double-charge, and any
          edit to history breaks a chain of hashes that auditors verify on demand.
        </p>
        <JournalExample />
      </div>

      <div className="order-first rounded-md border border-rule bg-surface p-6 sm:p-8 lg:order-none">
        <h2 className="mb-6 text-xl font-semibold">Sign in</h2>
        <form onSubmit={signIn} className="space-y-4">
          <Field label="Email">
            <input className="field" type="email" autoComplete="username" value={email}
                   onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="Password">
            <input className="field" type="password" autoComplete="current-password" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
          </Field>
          <ErrorNote error={error} />
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>

        <div className="mt-8 border-t border-rule-soft pt-6">
          <p className="mb-3 text-sm text-ink-soft">Or sign in as a demo user:</p>
          <div className="grid grid-cols-2 gap-2">
            {DEMO.map((d) => (
              <button
                key={d.email}
                className="btn-quiet text-left"
                disabled={busy}
                onClick={() => signIn(undefined, { email: d.email, password: DEMO_PASSWORD })}
              >
                <span className="block font-medium">{d.who}</span>
                <span className="block text-xs text-ink-soft">{d.role}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}
