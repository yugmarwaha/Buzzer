import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, api } from '../lib/api';
import { isTokenExpired } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { Progress } from '../components/ui/progress';
import { staggerStyle } from '../components/ui/motion';
import type { PastQuizItem } from '../types/past';

function fmtDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '';
}

/** The signed-in player's finished quizzes (completed or abandoned). */
export default function PastQuizzesPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<PastQuizItem[] | null>(null);
  const [guest, setGuest] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isTokenExpired(localStorage.getItem('token'))) {
      navigate('/signin', { replace: true });
      return;
    }
    api
      .get<PastQuizItem[]>('/player/past-quizzes')
      .then(setItems)
      .catch((err) => {
        // A guest token is rejected (403); keep it — the guest may still be mid-game.
        if (err instanceof ApiError && err.status === 403) setGuest(true);
        else if (err instanceof ApiError && err.status === 401) navigate('/signin', { replace: true });
        else setError(err instanceof Error ? err.message : 'Failed to load past quizzes');
      });
  }, [navigate]);

  return (
    <div className="mx-auto min-h-screen max-w-lg space-y-4 p-4">
      <div className="flex animate-fade-in items-center justify-between gap-3 pt-2">
        <h1 className="text-title text-primary">Past quizzes</h1>
        <Link to="/join" className="inline-flex min-h-11 shrink-0 items-center text-body font-semibold text-accent-text hover:underline">Join a game</Link>
      </div>

      {guest && (
        <Card variant="glass" className="animate-slide-up space-y-3 p-5">
          <p className="text-body-lg text-secondary">Past quizzes need a NetID or local account.</p>
          <Button size="lg" onClick={() => navigate('/signin')}>Sign in</Button>
        </Card>
      )}
      {error && <p role="alert" className="text-body text-danger">{error}</p>}
      {items && items.length === 0 && (
        <Card variant="glass-subtle" className="animate-fade-in p-6 text-center text-body-lg text-secondary">No finished quizzes yet.</Card>
      )}

      <div className="space-y-3">
        {items?.map((q, i) => (
          <Link
            key={q.session_id}
            to={`/past/${q.session_id}`}
            style={staggerStyle(i)}
            className="lift block animate-slide-up rounded-2xl glass p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-subheading text-primary">{q.game_title}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-caption text-muted">
                  <span>{q.course_name}</span>
                  <span>{fmtDate(q.completed_at)}</span>
                  {q.status === 'ABANDONED' && <Badge tone="warning">Abandoned</Badge>}
                </p>
              </div>
              <p className="shrink-0 text-right text-heading tabular-nums text-primary">
                {q.score.toLocaleString()}
                <span className="text-caption text-muted"> / {q.max_score.toLocaleString()}</span>
              </p>
            </div>
            <Progress
              className="mt-3"
              size="sm"
              value={q.score}
              max={Math.max(q.max_score, 1)}
              label={`Score ${q.score} of ${q.max_score}`}
            />
          </Link>
        ))}
      </div>
    </div>
  );
}
