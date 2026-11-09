import { useEffect, useState } from "react";
import { get, getToken, setToken, setUnauthenticatedHandler, type User } from "./api";
import { Ledger3DView } from "./components/Ledger3DView";
import { Login } from "./components/Login";
import { Loading } from "./components/ui";
import { AdminView } from "./views/AdminView";
import { AuditorView } from "./views/AuditorView";
import { HolderView } from "./views/HolderView";

const ROLE_LABEL = { ADMIN: "Admin", AUDITOR: "Auditor (read-only)", ACCOUNT_HOLDER: "Account holder" } as const;

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(() => getToken() !== null);
  const [show3D, setShow3D] = useState(false);

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
          <div className="flex items-center gap-4">
            <span className="text-lg font-semibold text-green">LedgerLine</span>
            <button
              onClick={() => setShow3D(!show3D)}
              className={`rounded-md px-3 py-1 text-xs font-semibold tracking-wide transition border ${
                show3D
                  ? "bg-emerald-800 text-white border-emerald-900 shadow-inner"
                  : "bg-surface hover:bg-paper text-emerald-800 border-rule"
              }`}
            >
              {show3D ? "← Standard Tables" : "🌐 3D Spatial Ledger"}
            </button>
          </div>
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
        {show3D ? (
          <Ledger3DView onClose={() => setShow3D(false)} />
        ) : (
          <>
            {user.role === "ACCOUNT_HOLDER" && <HolderView />}
            {user.role === "ADMIN" && <AdminView />}
            {user.role === "AUDITOR" && <AuditorView />}
          </>
        )}
      </main>
    </div>
  );
}
