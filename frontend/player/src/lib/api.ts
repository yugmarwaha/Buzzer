const BASE = '/api';

/** Error carrying the HTTP status (e.g. 403 = guest token on Past Quizzes). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token');
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers as Record<string, string> ?? {}),
    },
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { message?: string; detail?: unknown };
    const detail = typeof err.detail === 'string' ? err.detail : undefined;
    throw new ApiError(err.message ?? detail ?? `HTTP ${res.status}`, res.status);
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
};
