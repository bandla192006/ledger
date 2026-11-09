import { useState } from "react";
import { ApiError, get, post, type Anomaly } from "../api";
import { AccountsDirectory } from "../components/AccountsDirectory";
import { GlobalLedger, RefusalLog } from "../components/AuditTables";
import { Integrity } from "../components/Integrity";
import { Empty, ErrorNote, Field, Panel, Tabs } from "../components/ui";
import { useLoad } from "../hooks";
import { formatDateTime, shortId } from "../money";

type Tab = "accounts" | "people" | "ledger" | "refusals" | "anomalies" | "integrity";

interface UserRow {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  account_count: number;
}

const ROLE_LABEL: Record<string, string> = { ADMIN: "Admin", AUDITOR: "Auditor", ACCOUNT_HOLDER: "Account holder" };

function People({ onAccountOpened }: { onAccountOpened: () => void }) {
  const [tick, setTick] = useState(0);
  const { data, error } = useLoad(() => get<{ users: UserRow[] }>("/v1/users"), [tick]);
  const [form, setForm] = useState({ email: "", full_name: "", role: "ACCOUNT_HOLDER", password: "" });
  const [acct, setAcct] = useState({ owner_id: "", name: "" });
  const [err, setErr] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const holders = data?.users.filter((u) => u.role === "ACCOUNT_HOLDER") ?? [];

  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await post("/v1/users", form);
      setNotice(`Added ${form.full_name}.`);
      setForm({ email: "", full_name: "", role: "ACCOUNT_HOLDER", password: "" });
      setTick((t) => t + 1);
    } catch (e2) {
      setErr(e2 as ApiError);
    }
  }

  async function openAccount(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await post("/v1/accounts", acct);
      setNotice(`Opened ${acct.name}.`);
      setAcct({ owner_id: "", name: "" });
      setTick((t) => t + 1);
      onAccountOpened();
    } catch (e2) {
      setErr(e2 as ApiError);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <Panel title="People">
        <ErrorNote error={error} />
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="ledger-head text-left text-ink-soft">
                <th className="py-2 pl-5 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Email</th>
                <th className="py-2 pr-3 font-medium">Role</th>
                <th className="py-2 pr-5 text-right font-medium">Accounts</th>
              </tr>
            </thead>
            <tbody>
              {data?.users.map((u) => (
                <tr key={u.id} className="border-b border-rule-soft">
                  <td className="py-2 pl-5 pr-3 font-medium">{u.full_name}</td>
                  <td className="py-2 pr-3">{u.email}</td>
                  <td className="py-2 pr-3">{ROLE_LABEL[u.role] ?? u.role}</td>
                  <td className="num py-2 pr-5 text-right">{u.account_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="space-y-6">
        {notice && <p role="status" className="rounded-sm border-l-2 border-green bg-green-wash px-3 py-2 text-sm">{notice}</p>}
        <ErrorNote error={err} />
        <Panel title="Open an account">
          <form onSubmit={openAccount} className="space-y-3">
            <Field label="Owner">
              <select className="field" required value={acct.owner_id}
                      onChange={(e) => setAcct({ ...acct, owner_id: e.target.value })}>
                <option value="">Choose an account holder</option>
                {holders.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </select>
            </Field>
            <Field label="Account name">
              <input className="field" required maxLength={80} value={acct.name}
                     onChange={(e) => setAcct({ ...acct, name: e.target.value })} placeholder="e.g. Priya — Main" />
            </Field>
            <button className="btn-primary">Open account</button>
          </form>
        </Panel>
        <Panel title="Add a person">
          <form onSubmit={addUser} className="space-y-3">
            <Field label="Full name">
              <input className="field" required value={form.full_name}
                     onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </Field>
            <Field label="Email">
              <input className="field" type="email" required value={form.email}
                     onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
            <Field label="Role">
              <select className="field" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="ACCOUNT_HOLDER">Account holder</option>
                <option value="AUDITOR">Auditor</option>
                <option value="ADMIN">Admin</option>
              </select>
            </Field>
            <Field label="Temporary password" hint="At least 8 characters">
              <input className="field" type="password" minLength={8} required value={form.password}
                     onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </Field>
            <button className="btn-primary">Add person</button>
          </form>
        </Panel>
      </div>
    </div>
  );
}

export function Anomalies({ canFlag }: { canFlag: boolean }) {
  const [tick, setTick] = useState(0);
  const { data, error } = useLoad(() => get<{ anomalies: Anomaly[] }>("/v1/anomalies"), [tick]);
  const [form, setForm] = useState({ target_type: "TRANSACTION", target_id: "", severity: "MEDIUM", reason: "" });
  const [err, setErr] = useState<ApiError | null>(null);

  async function flag(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await post("/v1/anomalies", form);
      setForm({ ...form, target_id: "", reason: "" });
      setTick((t) => t + 1);
    } catch (e2) {
      setErr(e2 as ApiError);
    }
  }

  return (
    <div className={`grid gap-6 ${canFlag ? "lg:grid-cols-[1fr_22rem]" : ""}`}>
      <Panel title="Flagged anomalies">
        <ErrorNote error={error} />
        {data?.anomalies.length === 0 && <Empty>Nothing flagged.</Empty>}
        <ul className="divide-y divide-rule-soft">
          {data?.anomalies.map((a) => (
            <li key={a.id} className="py-3">
              <p className="flex flex-wrap items-baseline gap-x-3">
                <span className={`font-medium ${a.severity === "HIGH" ? "text-debit" : ""}`}>
                  {a.severity === "HIGH" ? "High" : a.severity === "MEDIUM" ? "Medium" : "Low"} severity
                </span>
                <span className="text-sm text-ink-soft">
                  {a.target_type.toLowerCase().replace("_", " ")} <span className="num">{shortId(a.target_id)}</span>
                </span>
              </p>
              <p className="mt-1">{a.reason}</p>
              <p className="num mt-1 text-xs text-ink-soft">{a.flagged_by_email}, {formatDateTime(a.created_at)}</p>
            </li>
          ))}
        </ul>
      </Panel>
      {canFlag && (
        <Panel title="Flag something">
          <form onSubmit={flag} className="space-y-3">
            <Field label="What">
              <select className="field" value={form.target_type}
                      onChange={(e) => setForm({ ...form, target_type: e.target.value })}>
                <option value="TRANSACTION">Transaction</option>
                <option value="ACCOUNT">Account</option>
                <option value="LEDGER_ENTRY">Ledger entry</option>
                <option value="USER">Person</option>
              </select>
            </Field>
            <Field label="Its ID" hint="Copy the full ID from the ledger or accounts list">
              <input className="field num" required value={form.target_id}
                     pattern="[0-9a-fA-F-]{36}" onChange={(e) => setForm({ ...form, target_id: e.target.value.trim() })} />
            </Field>
            <Field label="Severity">
              <select className="field" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                <option value="LOW">Low</option>
                <option value="MEDIUM">Medium</option>
                <option value="HIGH">High</option>
              </select>
            </Field>
            <Field label="Why">
              <textarea className="field" required maxLength={500} rows={3} value={form.reason}
                        onChange={(e) => setForm({ ...form, reason: e.target.value })} />
            </Field>
            <ErrorNote error={err} />
            <button className="btn-primary">Flag</button>
          </form>
        </Panel>
      )}
    </div>
  );
}

export function AdminView() {
  const [tab, setTab] = useState<Tab>("accounts");
  const [accountsTick, setAccountsTick] = useState(0);
  return (
    <>
      <div className="mb-6 border-b border-rule">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "accounts", label: "Accounts" },
            { id: "people", label: "People" },
            { id: "ledger", label: "Ledger" },
            { id: "refusals", label: "Refusals" },
            { id: "anomalies", label: "Anomalies" },
            { id: "integrity", label: "Integrity" },
          ]}
        />
      </div>
      {tab === "accounts" && <AccountsDirectory canManage refreshKey={accountsTick} />}
      {tab === "people" && <People onAccountOpened={() => setAccountsTick((t) => t + 1)} />}
      {tab === "ledger" && <GlobalLedger />}
      {tab === "refusals" && <RefusalLog />}
      {tab === "anomalies" && <Anomalies canFlag />}
      {tab === "integrity" && <Integrity />}
    </>
  );
}
