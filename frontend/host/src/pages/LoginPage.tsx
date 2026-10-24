import { useEffect, useState } from 'react';
import { BellRing } from 'lucide-react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { api, hasLiveToken } from '../lib/api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Spinner } from '../components/ui/spinner';
import { cn } from '../lib/utils';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Handle return from OAuth2 — exchange temp token for full access token
  useEffect(() => {
    if (searchParams.get('from') !== 'oauth2') return;

    const oauthError = searchParams.get('error');
    if (oauthError) {
      setError(oauthError);
      return;
    }

    const match = window.location.hash.match(/oauth2_data=([^&]+)/);
    if (!match) return;

    let tempToken: string;
    try {
      const data = JSON.parse(decodeURIComponent(match[1]));
      tempToken = data.temp_token;
      if (!tempToken) throw new Error();
    } catch {
      setError('Invalid authentication response');
      return;
    }

    setLoading(true);
    fetch('/api/auth/exchange-temp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tempToken}` },
    })
      .then(res => res.ok ? res.json() : res.json().then((e: { detail?: string }) => Promise.reject(e.detail ?? 'Sign-in failed')))
      .then((data: { access_token: string }) => {
        localStorage.setItem('token', data.access_token);
        window.history.replaceState(null, '', window.location.pathname);
        navigate('/courses');
      })
      .catch((msg: unknown) => {
        setError(typeof msg === 'string' ? msg : 'Sign-in failed');
        setLoading(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Already signed in: skip the form. Not during the OAuth return, which still needs to swap its temp token.
  if (hasLiveToken() && searchParams.get('from') !== 'oauth2') {
    return <Navigate to="/courses" replace />;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.post<{ access_token: string }>('/auth/login', { username, password });
      localStorage.setItem('token', data.access_token);
      navigate('/courses');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 pb-10 pt-20">
      <Card variant="glass-strong" className="w-full max-w-md animate-scale-in rounded-3xl">
        <CardHeader className="px-6 pb-4 pt-8 sm:px-8">
          <span
            aria-hidden="true"
            className="mb-5 flex size-14 animate-pop items-center justify-center rounded-2xl bg-accent text-accent-fg shadow-button"
          >
            <BellRing size={28} />
          </span>
          <h1 className="text-title text-primary">Buzzer</h1>
          <p className="mt-1 text-body-lg text-secondary">Host Sign In</p>
        </CardHeader>
        <CardContent className="space-y-5 px-6 pb-8 sm:px-8">
          {/* Primary: UW NetID SSO */}
          <a
            href="/api/auth/oauth2-callback?redirect_to=/host/login"
            aria-disabled={loading || undefined}
            className={cn(
              'flex min-h-12 w-full items-center justify-center gap-2 rounded-lg px-6 py-3 text-lg font-semibold',
              'transition-[background-color,box-shadow,transform] duration-fast ease-smooth',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
              loading
                ? 'pointer-events-none bg-raised text-muted'
                : 'bg-accent text-accent-fg shadow-button hover:bg-accent-hover hover:shadow-glow motion-safe:hover:-translate-y-px motion-safe:active:scale-[0.98]'
            )}
          >
            Sign in with UW NetID
          </a>

          <div className="flex items-center gap-3">
            <div className="flex-1 border-t border-line" />
            <span className="text-caption text-muted">or local account</span>
            <div className="flex-1 border-t border-line" />
          </div>

          {/* Secondary: username/password for admin & local accounts */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="host-username" className="block text-sm font-semibold text-secondary">Username</label>
              <Input
                id="host-username"
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="Username"
                autoComplete="username"
                required
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="host-password" className="block text-sm font-semibold text-secondary">Password</label>
              <Input
                id="host-password"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Password"
                autoComplete="current-password"
                aria-invalid={error ? true : undefined}
                required
              />
            </div>
            {error && (
              <p role="alert" className="animate-slide-down rounded-lg bg-danger-tint px-3.5 py-2.5 text-sm font-medium text-danger">
                {error}
              </p>
            )}
            <Button type="submit" variant="secondary" size="lg" className="w-full" disabled={loading}>
              {loading && <Spinner />}
              {loading ? 'Signing in…' : 'Sign In'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
