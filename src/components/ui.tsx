import type { ReactNode } from "react";
import { ApiError } from "../api";
import { moneyParts } from "../money";

export function Money({
  value,
  currency = "INR",
  size = "sm",
  tone = "ink",
}: {
  value: number;
  currency?: string;
  size?: "sm" | "lg" | "xl";
  tone?: "ink" | "debit" | "credit";
}) {
  const p = moneyParts(value, currency);
  const color = tone === "debit" ? "text-debit" : tone === "credit" ? "text-green" : "";
  if (size === "sm") {
    return (
      <span className={`num whitespace-nowrap ${color}`}>
        {p.sign}
        {p.symbol}
        {p.major}.{p.minor}
      </span>
    );
  }
  const big = size === "xl" ? "text-5xl sm:text-6xl" : "text-3xl";
  const small = size === "xl" ? "text-2xl" : "text-lg";
  return (
    <span className={`num whitespace-nowrap font-light tracking-tight ${color}`}>
      <span className={`${small} align-top text-ink-soft`}>{p.symbol}</span>
      <span className={big}>
        {p.sign}
        {p.major}
      </span>
      <span className={`${small} text-ink-soft`}>.{p.minor}</span>
    </span>
  );
}

export function Panel({ title, aside, children, className = "" }: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-md border border-rule bg-surface ${className}`}>
      {(title || aside) && (
        <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule-soft px-5 py-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {aside}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <nav className="flex gap-1 overflow-x-auto" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={`whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium ${
            value === t.id ? "border-green text-green" : "border-transparent text-ink-soft hover:text-ink"
          }`}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}

export function StatusMark({ status }: { status: "ACTIVE" | "FROZEN" }) {
  return status === "ACTIVE" ? (
    <span className="text-sm text-ink-soft">Active</span>
  ) : (
    <span className="rounded-sm bg-debit-wash px-1.5 py-0.5 text-sm font-medium text-debit">Frozen</span>
  );
}

export function ErrorNote({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-sm border-l-2 border-debit bg-debit-wash px-3 py-2 text-sm text-debit">
      {error.message}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-ink-soft">{children}</p>;
}

export function Loading() {
  return <p className="py-6 text-center text-sm text-ink-soft">Loading…</p>;
}

export function Hash({ value }: { value: string }) {
  return (
    <abbr title={value} className="num text-xs text-ink-soft no-underline">
      {value.slice(0, 8)}
    </abbr>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-soft">{hint}</span>}
    </label>
  );
}
