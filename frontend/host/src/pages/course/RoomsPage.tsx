import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ApiError, api, errorMessage } from '../../lib/api';
import { AlertTriangle, LogIn, Power, Radio, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { cn } from '../../lib/utils';
import type { ActiveRoom } from '../../types/host';

/**
 * Every LOBBY/IN_PROGRESS room in the course, including other hosts' rooms. The list comes
 * from the database, so stale rooms (no live state) still show — they are what blocks
 * edits — and their starter or an admin can end them here.
 */
export default function RoomsPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const navigate = useNavigate();
  const [rooms, setRooms] = useState<ActiveRoom[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [ending, setEnding] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Guards against a slow response for a previous course overwriting the current one.
  const latestCourse = useRef(courseId);

  async function load() {
    const id = courseId;
    try {
      const data = await api.get<ActiveRoom[]>(`/host/courses/${courseId}/rooms`);
      if (latestCourse.current !== id) return;
      setRooms(data);
      setError('');
    } catch (err) {
      if (latestCourse.current !== id) return;
      setError(errorMessage(err, 'Failed to load rooms'));
    } finally {
      if (latestCourse.current === id) setLoading(false);
    }
  }

  useEffect(() => {
    latestCourse.current = courseId;
    setRooms([]); setError(''); setLoading(true);
    void load();
  }, [courseId]);

  // Background refresh every 5 s while the tab is visible. It never shows a loading state or
  // an error: a failed refresh keeps the list on screen, and requests never overlap.
  const inFlight = useRef(false);
  useEffect(() => {
    async function quietLoad() {
      if (inFlight.current || document.visibilityState !== 'visible') return;
      inFlight.current = true;
      const id = courseId;
      try {
        const data = await api.get<ActiveRoom[]>(`/host/courses/${courseId}/rooms`);
        if (latestCourse.current === id) setRooms(data);
      } catch {
        // Keep the old list; the manual Refresh button reports errors.
      } finally {
        inFlight.current = false;
      }
    }
    const timer = window.setInterval(() => void quietLoad(), 5000);
    document.addEventListener('visibilitychange', quietLoad);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', quietLoad);
    };
  }, [courseId]);

  async function endRoom(r: ActiveRoom) {
    const who = r.host_display_name ?? 'Unknown host';
    const message = r.has_answers
      ? `End room ${r.room_code} (Quiz '${r.game_title}'), started by ${who}? Players have answered. It will be saved as Abandoned and their results kept.`
      : `End room ${r.room_code}, started by ${who}? No one has answered; the room will be removed.`;
    if (!confirm(message)) return;
    setEnding(r.session_id);
    setError('');
    try {
      await api.post(`/host/sessions/${r.session_id}/end`);
    } catch (err) {
      // 404/409 = already ended (or removed) elsewhere; treat as done.
      if (!(err instanceof ApiError && (err.status === 404 || err.status === 409))) {
        setError(errorMessage(err, 'Failed to end room'));
      }
    } finally {
      setEnding(null);
      await load();
    }
  }

  async function refresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  if (loading) {
    return (
      <div aria-busy="true">
        <span className="sr-only">Loading rooms</span>
        <div className="flex items-center justify-between gap-4">
          <div className="skeleton h-9 w-48 rounded-lg" aria-hidden="true" />
          <div className="skeleton h-9 w-28 rounded-lg" aria-hidden="true" />
        </div>
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {[0, 1].map((i) => (
            <div key={i} className="skeleton h-52 rounded-2xl" aria-hidden="true" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="text-heading text-primary">Active rooms</h2>
          <Badge tone="neutral" size="md" className="tabular-nums" aria-label={`${rooms.length} open`}>
            {rooms.length}
          </Badge>
        </div>
        <Button size="sm" variant="ghost" onClick={() => void refresh()} disabled={refreshing}>
          <RefreshCw size={16} aria-hidden="true" className={cn(refreshing && 'animate-spin')} /> Refresh
        </Button>
      </div>

      {error && (
        <p role="alert" className="animate-slide-down rounded-lg bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}

      {rooms.length === 0 && (
        <div className="flex animate-scale-in flex-col items-center gap-2 rounded-2xl glass-subtle px-6 py-12 text-center">
          <Radio size={32} className="text-muted" aria-hidden="true" />
          <p className="text-body-lg font-semibold text-primary">No open rooms in this course.</p>
          <p className="text-body text-secondary">Start a room from the Quizzes tab.</p>
        </div>
      )}

      {rooms.length > 0 && (
        <ul className="stagger grid gap-4 lg:grid-cols-2">
          {rooms.map((r) => {
            const running = r.status === 'IN_PROGRESS';
            return (
              <li key={r.session_id} className="flex animate-slide-up flex-col rounded-2xl glass p-5 sm:p-6">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-caption text-muted">Room code</p>
                    <p className="font-mono text-heading font-bold tracking-[0.14em] text-primary">{r.room_code}</p>
                  </div>
                  <Badge tone={!r.live ? 'neutral' : running ? 'success' : 'warning'} size="md">
                    {r.live && (
                      <span
                        aria-hidden="true"
                        className={cn('size-2 rounded-pill animate-pulse-soft', running ? 'bg-success' : 'bg-warning')}
                      />
                    )}
                    {running ? 'In progress' : 'Lobby'}
                  </Badge>
                </div>

                <h3 className="mt-3 break-words text-subheading text-primary">{r.game_title}</h3>
                <p className="mt-0.5 text-caption text-muted">Started by {r.host_display_name ?? 'Unknown host'}</p>

                <dl className="mt-4 grid grid-cols-2 gap-3">
                  <div className="rounded-xl glass-subtle px-4 py-3">
                    <dt className="text-caption text-muted">Players</dt>
                    <dd className="text-heading tabular-nums text-primary">{r.player_count ?? '—'}</dd>
                  </div>
                  <div className="rounded-xl glass-subtle px-4 py-3">
                    <dt className="text-caption text-muted">Answered</dt>
                    <dd className="text-heading tabular-nums text-primary">{r.answered_count}</dd>
                  </div>
                </dl>

                {!r.live && (
                  <p className="mt-4 flex items-start gap-2 text-sm font-medium text-warning">
                    <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
                    Not running. Room state has expired or the server restarted.
                  </p>
                )}
                {!r.can_end && (
                  <p className="mt-4 text-sm text-muted">
                    Started by {r.host_display_name ?? 'another host'}. Only they or an admin can end it.
                  </p>
                )}

                {r.can_end && (
                  <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
                    {r.live && (
                      <Button size="sm" variant="secondary" onClick={() => navigate(`/game/${r.room_code}/lobby`)}>
                        <LogIn size={14} aria-hidden="true" /> Rejoin
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="danger"
                      className="ml-auto"
                      onClick={() => void endRoom(r)}
                      loading={ending === r.session_id}
                    >
                      <Power size={14} aria-hidden="true" /> End room
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
