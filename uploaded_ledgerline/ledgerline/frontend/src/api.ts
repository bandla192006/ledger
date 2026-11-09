// Thin fetch client for the LedgerLine API.

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
const TOKEN_KEY = "ledgerline.token";

export type Role = "ADMIN" | "ACCOUNT_HOLDER" | "AUDITOR";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
}

export interface Account {
  id: string;
  owner_id: string | null;
  owner_name: string | null;
  owner_email: string | null;
  name: string;
  account_type: "USER" | "SYSTEM";
  system_code: string | null;
  currency: string;
  balance: number;
  status: "ACTIVE" | "FROZEN";
  created_at: string;
}

export interface StatementEntry {
  id: string;
  transaction_id: string;
  account_seq: number;
  entry_type: "DEBIT" | "CREDIT";
  amount: number;
  balance_after: number;
  currency: string;
  entry_hash: string;
  created_at: string;
  transaction_type: "DEPOSIT" | "WITHDRAWAL" | "TRANSFER";
  description: string | null;
  external_ref: string | null;
  counterparty_account_name: string | null;
  counterparty_owner_name: string | null;
}

export interface Statement {
  account: Account;
  summary: { entry_count: number; total_credits: number; total_debits: number };
  entries: StatementEntry[];
  next_before_seq: number | null;
}

export interface LedgerEntry {
  id: string;
  transaction_id: string;
  transaction_type: string;
  account_id: string;
  account_name: string;
  owner_name: string | null;
  account_seq: number;
  entry_type: "DEBIT" | "CREDIT";
  amount: number;
  balance_after: number;
  currency: string;
  prev_hash: string;
  entry_hash: string;
  created_at: string;
}

export interface Refusal {
  id: number;
  attempted_by_email: string | null;
  actor_role: string | null;
  action: string;
  reason_code: string;
  http_status: number;
  idempotency_key: string | null;
  source_account_id: string | null;
  destination_account_id: string | null;
  requested_amount: number | null;
  account_balance_snapshot: number | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface IdempotencyRecord {
  user_email: string;
  idempotency_key: string;
  request_path: string;
  request_hash: string;
  status: "IN_FLIGHT" | "COMPLETED";
  response_code: number | null;
  transaction_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface InvariantCheck {
  name: string;
  description: string;
  passed: boolean;
  details: Record<string, unknown>;
}

export interface InvariantReport {
  run_id: number;
  run_at: string;
  passed: boolean;
  checks: InvariantCheck[];
  totals: Record<string, number>;
}

export interface Anomaly {
  id: number;
  target_type: string;
  target_id: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  reason: string;
  flagged_by_email: string;
  created_at: string;
}

export interface DirectoryAccount {
  id: string;
  name: string;
  currency: string;
  owner_name: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
}

let onUnauthenticated: (() => void) | null = null;
export function setUnauthenticatedHandler(fn: () => void): void {
  onUnauthenticated = fn;
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  idempotencyKey?: string;
}

export interface ApiResult<T> {
  data: T;
  replayed: boolean;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Couldn't reach the server. Check your connection and retry.");
  }

  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = payload?.error ?? {};
    if (res.status === 401 && path !== "/v1/auth/login") onUnauthenticated?.();
    throw new ApiError(res.status, err.code ?? "HTTP_ERROR", err.message ?? res.statusText, err.details ?? {});
  }
  return { data: payload as T, replayed: res.headers.get("Idempotent-Replayed") === "true" };
}

export async function get<T>(path: string): Promise<T> {
  return (await request<T>(path)).data;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return (await request<T>(path, { method: "POST", body })).data;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
