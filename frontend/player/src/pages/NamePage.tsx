import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import { isTokenExpired } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Badge } from '../components/ui/badge';

type Mode = 'guest' | 'netid' | 'local';

export default function NamePage() {
  const { code = '' } = useParams<{ code: string }>();
  const navigate = useNavigate();

  // Only treat the token as valid if it is present AND not expired. State, so "Use a
  // different account" can drop it and show the guest/sign-in options again.
  const [isAuthenticated, setIsAuthenticated] = useState(() => !isTokenExpired(localStorage.getItem('token')));
  const [mode, setMode] = useState<Mode>(isAuthenticated ? 'netid' : 'guest');

  // Guest fields — pre-fill from localStorage if available
  const [displayName, setDisplayName] = useState(() => localStorage.getItem('playerName') ?? '');
  const [email, setEmail] = useState(() => localStorage.getItem('playerEmail') ?? '');

  // Login fields
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // Sign out of this player session only (the stored token); the guest name/email pre-fill stays.
  function switchAccount() {
    localStorage.removeItem('token');
    setIsAuthenticated(false);
    setMode('guest');
    setError('');
  }

  async function handleGuest(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.post<{ access_token: string }>('/auth/guest', {
        display_name: displayName.trim(),
        email: email.trim().toLowerCase(),
        room_code: code,
      });
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('playerName', displayName.trim());
      localStorage.setItem('playerEmail', email.trim().toLowerCase());
      navigate(`/game/${code}/lobby`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to join');
    } finally {
      setLoading(false);
    }
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.post<{ access_token: string }>('/auth/login', { username, password });
      localStorage.setItem('token', data.access_token);
      navigate(`/game/${code}/lobby`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card variant="glass-strong" className="w-full max-w-sm animate-slide-up rounded-3xl">
        <CardHeader className="p-6 pb-2 text-center">
          <h1 className="text-heading text-primary">
            Join Room <Badge tone="accent" size="lg" className="ml-1 align-middle font-mono tracking-widest">{code}</Badge>
          </h1>

          {/* Mode toggle — hidden when already authenticated */}
          {!isAuthenticated && (
            <div className="mt-4 flex gap-1 rounded-xl bg-track p-1">
              <button
                type="button"
                onClick={() => { setMode('guest'); setError(''); }}
                className={`min-h-11 flex-1 rounded-lg py-2 text-base font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  mode === 'guest'
                    ? 'bg-accent text-accent-fg shadow-button'
                    : 'text-secondary hover:text-primary'
                }`}
              >
                Join as Guest
              </button>
              <button
                type="button"
                onClick={() => { setMode('netid'); setError(''); }}
                className={`min-h-11 flex-1 rounded-lg py-2 text-base font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  mode === 'netid' || mode === 'local'
                    ? 'bg-accent text-accent-fg shadow-button'
                    : 'text-secondary hover:text-primary'
                }`}
              >
                Sign In
              </button>
            </div>
          )}
        </CardHeader>

        <CardContent className="px-6 pb-6">
          {mode === 'guest' && (
            <form onSubmit={handleGuest} className="space-y-4">
              <div>
                <label className="mb-1.5 block text-body font-semibold text-secondary">Display Name</label>
                <Input
                  type="text"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  placeholder="Your name"
                  maxLength={50}
                  required
                  autoFocus
                />
              </div>
              <div>
                <label className="mb-1.5 block text-body font-semibold text-secondary">Email</label>
                <Input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </div>
              {error && <p role="alert" className="animate-fade-in text-center text-body text-danger">{error}</p>}
              <Button
                type="submit"
                className="w-full"
                size="xl"
                loading={loading}
                disabled={!displayName.trim() || !email.trim()}
              >
                {loading ? 'Joining…' : 'Join Game'}
              </Button>
            </form>
          )}

          {mode === 'netid' && (
            <div className="space-y-3">
              {isAuthenticated ? (
                /* Already signed in via OAuth2 — go straight to lobby, or switch account */
                <>
                  <Button
                    className="w-full"
                    size="xl"
                    onClick={() => navigate(`/game/${code}/lobby`)}
                  >
                    Join Game
                  </Button>
                  <button
                    type="button"
                    onClick={switchAccount}
                    className="min-h-11 w-full text-body text-muted transition-colors hover:text-primary"
                  >
                    Use a different account
                  </button>
                </>
              ) : (
                /* Trigger OAuth2 — save room code so we can return here after auth */
                <a
                  href="/api/auth/oauth2-callback?redirect_to=/player/login"
                  onClick={() => {
                    sessionStorage.removeItem('postLoginPath');
                    sessionStorage.setItem('joinRoomCode', code);
                  }}
                  className="flex min-h-14 w-full items-center justify-center rounded-lg bg-accent px-4 py-3 text-lg font-semibold text-accent-fg shadow-button transition-[background-color,box-shadow] duration-fast hover:bg-accent-hover hover:shadow-glow"
                >
                  Sign in with UW NetID
                </a>
              )}
              {error && <p role="alert" className="animate-fade-in text-center text-body text-danger">{error}</p>}
              {!isAuthenticated && (
                <button
                  type="button"
                  onClick={() => { setMode('local'); setError(''); }}
                  className="min-h-11 w-full text-body text-muted transition-colors hover:text-primary"
                >
                  Use local account instead
                </button>
              )}
            </div>
          )}

          {mode === 'local' && (
            <form onSubmit={handleLogin} className="space-y-4">
              <Input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="Username"
                autoComplete="username"
                required
                autoFocus
              />
              <Input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Password"
                autoComplete="current-password"
                required
              />
              {error && <p role="alert" className="animate-fade-in text-center text-body text-danger">{error}</p>}
              <Button
                type="submit"
                className="w-full"
                size="xl"
                loading={loading}
                disabled={!username || !password}
              >
                {loading ? 'Signing in…' : 'Sign In & Join'}
              </Button>
              <button
                type="button"
                onClick={() => { setMode('netid'); setError(''); }}
                className="min-h-11 w-full text-body text-muted transition-colors hover:text-primary"
              >
                ← Back to NetID sign in
              </button>
            </form>
          )}

          <button
            type="button"
            onClick={() => navigate('/join')}
            className="mt-2 min-h-11 w-full text-body text-muted transition-colors hover:text-primary"
          >
            ← Different room code
          </button>
        </CardContent>
      </Card>
    </div>
  );
}
