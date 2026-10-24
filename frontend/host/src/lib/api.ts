const BASE = '/api';

/** Error carrying the HTTP status, so callers can react to e.g. 409 "already ended". */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function toError(res: Response): Promise<ApiError> {
  const err = (await res.json().catch(() => ({}))) as {
    message?: string;
    detail?: unknown;
  };
  // App errors send {error, message}; FastAPI validation/HTTP errors send {detail}.
  // A 422 validation detail is a list of {msg}; show the first one, not "HTTP 422".
  const first = Array.isArray(err.detail) ? (err.detail[0] as { msg?: unknown } | undefined) : undefined;
  const detail =
    typeof err.detail === 'string'
      ? err.detail
      : typeof first?.msg === 'string'
      ? first.msg.replace(/^Value error, /, '')
      : undefined;
  return new ApiError(err.message ?? detail ?? `HTTP ${res.status}`, res.status);
}

function authHeader(): Record<string, string> {
  const token = localStorage.getItem('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
      ...(opts.headers as Record<string, string> ?? {}),
    },
  });
  if (!res.ok) throw await toError(res);
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

/** Trigger a browser download of a file response. */
async function downloadFetch(path: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeader() });
  if (!res.ok) throw await toError(res);
  const disposition = res.headers.get('Content-Disposition') ?? '';
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : 'download';
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  // Revoking right away can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function withBody(method: string, body?: unknown): RequestInit {
  return { method, body: body !== undefined ? JSON.stringify(body) : undefined };
}

/** multipart/form-data request (file uploads); the browser sets the boundary header. */
async function formFetch<T>(method: string, path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method, headers: authHeader(), body: form });
  if (!res.ok) throw await toError(res);
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, withBody('POST', body)),
  put: <T>(path: string, body?: unknown) => apiFetch<T>(path, withBody('PUT', body)),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, withBody('PATCH', body)),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
  /** POST multipart/form-data (file uploads). */
  postForm: <T>(path: string, form: FormData) => formFetch<T>('POST', path, form),
  /** PUT multipart/form-data (T8 image replace). */
  putForm: <T>(path: string, form: FormData) => formFetch<T>('PUT', path, form),
  download: downloadFetch,
};

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** Roles the Host API accepts (backend `require_user`: ADMIN and USER, not GUEST). */
const HOST_ROLES = ['ADMIN', 'USER'];
const CLOCK_SKEW_MS = 10_000;

/**
 * True when a stored token is one the Host can use: access token, ADMIN or USER role,
 * and a numeric `exp` that has not passed (10 s clock skew allowed). Fails closed on any
 * missing or odd claim. The token key is shared with Player and Admin. Not a signature check.
 */
export function hasLiveToken(): boolean {
  const token = localStorage.getItem('token');
  if (!token) return false;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (typeof payload.exp !== 'number' || !Number.isFinite(payload.exp)) return false;
    if (payload.token_type !== 'access') return false;
    if (typeof payload.role !== 'string' || !HOST_ROLES.includes(payload.role.toUpperCase())) return false;
    return payload.exp * 1000 + CLOCK_SKEW_MS > Date.now();
  } catch {
    return false;
  }
}
