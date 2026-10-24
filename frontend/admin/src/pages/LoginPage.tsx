import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { api, hasLiveAdminToken } from '../lib/api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card, CardContent, CardHeader } from '../components/ui/card';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  // Already signed in as an admin: skip the form. An expired or non-admin token stays on it.
  if (hasLiveAdminToken()) return <Navigate to="/users" replace />;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.post<{ access_token: string }>('/auth/login', { username, password });
      localStorage.setItem('token', data.access_token);
      navigate('/users');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card variant="glass-strong" className="w-full max-w-sm animate-scale-in">
        <CardHeader className="pt-8 text-center">
          <p className="text-title font-display text-accent-text" aria-hidden="true">Buzzer</p>
          <h1 className="mt-2 text-heading text-primary">Admin Login</h1>
          <p className="mt-1 text-caption text-muted">Buzzer Administration</p>
        </CardHeader>
        <CardContent className="pb-8">
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
            <Input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            {error && <p role="alert" className="rounded-lg bg-danger-tint px-3 py-2 text-sm text-danger">{error}</p>}
            <Button type="submit" size="lg" className="w-full" loading={loading}>
              {loading ? 'Signing in\u2026' : 'Sign In'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
