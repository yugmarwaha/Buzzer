import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card, CardContent } from '../components/ui/card';

export default function JoinPage() {
  const [searchParams] = useSearchParams();
  const [roomCode, setRoomCode] = useState(() => searchParams.get('code')?.toUpperCase() ?? '');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleJoin(e?: React.FormEvent) {
    e?.preventDefault();
    const code = roomCode.trim().toUpperCase();
    if (code.length !== 6) {
      setError('Room code must be 6 characters');
      return;
    }
    setError('');
    setLoading(true);
    try {
      await api.get(`/game/rooms/${code}/ping`);
      navigate(`/name/${code}`);
    } catch {
      setError('Room not found. Check the code and try again.');
    } finally {
      setLoading(false);
    }
  }

  // Auto-submit when the page loads with a pre-filled code from QR scan
  useEffect(() => {
    if (searchParams.get('code')) {
      handleJoin();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-6 p-4">
      <div className="animate-fade-in text-center">
        <h1 className="font-display text-title text-primary">Buzzer</h1>
        <p className="mt-2 text-body-lg text-secondary">Enter your room code to join</p>
      </div>
      <Card variant="glass-strong" className="w-full max-w-sm animate-slide-up rounded-3xl">
        <CardContent className="p-6 pt-6">
          <form onSubmit={handleJoin} className="space-y-4">
            <Input
              type="text"
              value={roomCode}
              onChange={e => setRoomCode(e.target.value.toUpperCase())}
              placeholder="XXXXXX"
              maxLength={6}
              className="min-h-16 rounded-xl text-center font-mono text-4xl font-bold uppercase tracking-[0.3em]"
              aria-label="Room code"
              aria-invalid={error ? true : undefined}
              autoCapitalize="characters"
              autoComplete="off"
              autoFocus
            />
            {error && <p role="alert" className="animate-fade-in text-center text-body text-danger">{error}</p>}
            <Button type="submit" className="w-full" size="xl" loading={loading} disabled={loading || roomCode.length < 6}>
              {loading ? 'Checking…' : 'Join Game'}
            </Button>
          </form>
          <div className="mt-6 border-t border-line pt-4 text-center">
            <Link to="/past" className="inline-flex min-h-11 items-center px-3 text-body font-semibold text-accent-text hover:underline">
              Past quizzes
            </Link>
            <p className="text-caption text-muted">NetID or local account sign-in</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
