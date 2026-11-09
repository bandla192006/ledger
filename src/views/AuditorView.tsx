import { useState } from "react";
import { AccountsDirectory } from "../components/AccountsDirectory";
import { GlobalLedger, IdempotencyLog, RefusalLog } from "../components/AuditTables";
import { Integrity } from "../components/Integrity";
import { Tabs } from "../components/ui";
import { Anomalies } from "./AdminView";

type Tab = "integrity" | "accounts" | "ledger" | "refusals" | "keys" | "anomalies";

/** Read-only: auditors can see everything and verify, but change nothing. */
export function AuditorView() {
  const [tab, setTab] = useState<Tab>("integrity");
  return (
    <>
      <div className="mb-6 border-b border-rule">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "integrity", label: "Integrity" },
            { id: "accounts", label: "Accounts" },
            { id: "ledger", label: "Ledger" },
            { id: "refusals", label: "Refusals" },
            { id: "keys", label: "Request keys" },
            { id: "anomalies", label: "Anomalies" },
          ]}
        />
      </div>
      {tab === "integrity" && <Integrity />}
      {tab === "accounts" && <AccountsDirectory canManage={false} />}
      {tab === "ledger" && <GlobalLedger />}
      {tab === "refusals" && <RefusalLog />}
      {tab === "keys" && <IdempotencyLog />}
      {tab === "anomalies" && <Anomalies canFlag={false} />}
    </>
  );
}
