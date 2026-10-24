import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Input } from '../components/ui/input';

/** Sign in without a room code, to reach Past Quizzes. Guests have no history. */
export default function SignInPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.post<{ access_token: string }>('/auth/login', { username, password });
      localStorage.setItem('token', data.access_token);
      navigate('/past', { replace: true });
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
          <h1 className="text-heading text-primary">Sign in for past quizzes</h1>
          <p className="mt-1 text-body text-secondary">Guest players don't have a quiz history.</p>
        </CardHeader>
        <CardContent className="space-y-4 px-6 pb-6">
          <a
            href="/api/auth/oauth2-callback?redirect_to=/player/login"
            onClick={() => sessionStorage.setItem('postLoginPath', '/past')}
            className="flex min-h-14 w-full items-center justify-center rounded-lg bg-accent px-4 py-3 text-lg font-semibold text-accent-fg shadow-button transition-[background-color,box-shadow] duration-fast hover:bg-accent-hover hover:shadow-glow"
          >
            Sign in with NetID
          </a>
          <div className="flex items-center gap-3 text-caption text-muted">
            <div className="flex-1 border-t border-line" /> or a local account <div className="flex-1 border-t border-line" />
          </div>
          <form onSubmit={handleLogin} className="space-y-3">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username" autoComplete="username" required />
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" autoComplete="current-password" required />
            {error && <p role="alert" className="animate-fade-in text-center text-body text-danger">{error}</p>}
            <Button type="submit" className="w-full" size="lg" loading={loading} disabled={!username || !password}>
              {loading ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
          <button
            type="button"
            onClick={() => navigate('/join')}
            className="min-h-11 w-full text-body text-muted transition-colors hover:text-primary"
          >
            ← Back to join
          </button>
        </CardContent>
      </Card>
    </div>
  );
}
