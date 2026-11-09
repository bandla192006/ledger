// Money is always integer minor units (paise). We never do float arithmetic on it.

const SYMBOLS: Record<string, string> = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };
const groupInr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export interface MoneyParts {
  sign: "" | "−";
  symbol: string;
  major: string;
  minor: string;
}

export function moneyParts(minorUnits: number, currency = "INR"): MoneyParts {
  const negative = minorUnits < 0;
  const abs = Math.abs(minorUnits);
  const major = Math.trunc(abs / 100);
  const minor = abs % 100;
  return {
    sign: negative ? "−" : "",
    symbol: SYMBOLS[currency.trim()] ?? `${currency.trim()} `,
    major: groupInr.format(major),
    minor: String(minor).padStart(2, "0"),
  };
}

export function formatMoney(minorUnits: number, currency = "INR"): string {
  const p = moneyParts(minorUnits, currency);
  return `${p.sign}${p.symbol}${p.major}.${p.minor}`;
}

/**
 * Parse what a person typed ("1,250.5") into paise (125050) using string
 * arithmetic only. Returns null when the input isn't a positive amount with at
 * most two decimal places.
 */
export function parseToMinor(input: string): number | null {
  const cleaned = input.replace(/[,\s₹]/g, "");
  const match = /^(\d{1,10})(?:\.(\d{0,2}))?$/.exec(cleaned);
  if (!match) return null;
  const major = Number(match[1]);
  const minor = Number((match[2] ?? "").padEnd(2, "0") || "0");
  const total = major * 100 + minor;
  return total > 0 ? total : null;
}

export function shortId(id: string | null | undefined, n = 8): string {
  return id ? id.slice(0, n) : "—";
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
