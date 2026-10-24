/**
 * Where the Host app lives. Behind nginx (:8080) it is /host/ on the same origin, so the
 * admin may already be signed in there. On the local dev servers the apps run on different
 * ports; set VITE_HOST_APP_URL (e.g. http://localhost:5173) and expect to sign in again.
 */
export const HOST_APP_URL: string = (import.meta.env.VITE_HOST_APP_URL ?? '/host').replace(/\/$/, '');

export function hostAppLink(path: string): string {
  return `${HOST_APP_URL}${path}`;
}
