import { useEffect, useState } from "react";
import { get, getToken, setToken, setUnauthenticatedHandler, type User } from "./api";
import { Login } from "./components/Login";
import { Loading } from "./components/ui";
import { AdminView } from "./views/AdminView";
import { AuditorView } from "./views/AuditorView";
import { HolderView } from "./views/HolderView";

const ROLE_LABEL = { ADMIN: "Admin", AUDITOR: "Auditor (read-only)", ACCOUNT_HOLDER: "Account holder" } as const;

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(() => getToken() !== null);

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      setToken(null);
      setUser(null);
    });
    if (!getToken()) return;
    get<User>("/v1/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setChecking(false));
  }, []);

  function signOut() {
    setToken(null);
    setUser(null);
  }

  if (checking) return <Loading />;
  if (!user) return <Login onLogin={setUser} />;

  return (
    <div className="min-h-screen">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-3">
          <span className="text-lg font-semibold text-green">LedgerLine</span>
          <div className="flex items-center gap-4 text-sm">
            <span>
              <span className="font-medium">{user.full_name}</span>
              <span className="ml-2 text-ink-soft">{ROLE_LABEL[user.role]}</span>
            </span>
            <button className="btn-quiet" onClick={signOut}>Sign out</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        {user.role === "ACCOUNT_HOLDER" && <HolderView />}
        {user.role === "ADMIN" && <AdminView />}
        {user.role === "AUDITOR" && <AuditorView />}
      </main>
    </div>
  );
}
