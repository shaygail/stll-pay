/** Browser API client. All calls go to same-origin /api, which Next forwards to the API. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

const CSRF_COOKIE = "stllpay_csrf";

export function readCookie(name: string, cookieString?: string): string | undefined {
  const source = cookieString ?? (typeof document === "undefined" ? "" : document.cookie);
  for (const part of source.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return undefined;
}

export async function toApiError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown } } = {};
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  return new ApiError(
    res.status,
    body.error?.code ?? "unknown_error",
    body.error?.message ?? "Something went wrong. Please try again.",
    body.error?.details,
  );
}

export async function api<T = unknown>(
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = init.method ?? (init.body === undefined ? "GET" : "POST");
  const headers: Record<string, string> = { accept: "application/json" };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["x-csrf-token"] = csrf;
  }
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export type Membership = {
  merchant_id: string;
  trading_name: string;
  slug: string;
  status: string;
  role: string;
};

export type Me = {
  id: string;
  email: string;
  display_name: string | null;
  is_platform_admin: boolean;
  memberships: Membership[];
};

export type Merchant = {
  id: string;
  slug: string;
  legal_name: string;
  trading_name: string;
  nzbn: string | null;
  status: string;
  live_enabled: boolean;
  role?: string;
};

export type PublicConfig = {
  environment: string;
  payments_provider: string;
  live_payments_enabled: boolean;
};
