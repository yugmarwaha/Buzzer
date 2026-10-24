const BASE = '/api';

// App errors send {error, message}; FastAPI validation/HTTP errors send {detail}.
function errorText(err: unknown, status: number): string {
  const body = err as { message?: string; detail?: string };
  return body.message ?? body.detail ?? `HTTP ${status}`;
}

const CLOCK_SKEW_MS = 10_000;

/** True when a stored admin access token has not expired. Not a signature check; the server decides. */
export function hasLiveAdminToken(): boolean {
  const token = localStorage.getItem('token');
  if (!token) return false;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (typeof payload.exp !== 'number' || !Number.isFinite(payload.exp)) return false;
    if (payload.token_type !== 'access') return false;
    if (typeof payload.role !== 'string' || payload.role.toUpperCase() !== 'ADMIN') return false;
    return payload.exp * 1000 + CLOCK_SKEW_MS > Date.now();
  } catch {
    return false;
  }
}

/** An expired or rejected token ends the session: clear it and return to the login page once. */
function endSessionOn401(res: Response, path: string): void {
  if (res.status !== 401 || path.startsWith('/auth/login')) return;
  localStorage.removeItem('token');
  const loginPath = `${import.meta.env.BASE_URL}login`;
  if (window.location.pathname !== loginPath) window.location.assign(loginPath);
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
    endSessionOn401(res, path);
    const err = await res.json().catch(() => ({}));
    throw new Error(errorText(err, res.status));
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

/** Trigger a file download from a fetch response */
async function downloadFetch(path: string): Promise<void> {
  const token = localStorage.getItem('token');
  const res = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    endSessionOn401(res, path);
    const err = await res.json().catch(() => ({}));
    throw new Error(errorText(err, res.status));
  }
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

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
  /** POST multipart/form-data (for file uploads) */
  postForm: <T>(path: string, form: FormData) => {
    const token = localStorage.getItem('token');
    return fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async (res) => {
      if (!res.ok) {
        endSessionOn401(res, path);
        const err = await res.json().catch(() => ({}));
        throw new Error(errorText(err, res.status));
      }
      const text = await res.text();
      return (text ? JSON.parse(text) : {}) as T;
    });
  },
  download: downloadFetch,
};
