import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Archive, ChevronRight, Download, Eye, FileQuestion, Lock, Pencil, Play, Plus, RotateCcw, Upload } from 'lucide-react';
import { api, errorMessage } from '../../lib/api';
import { Button } from '../../components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Spinner } from '../../components/ui/spinner';
import { cn } from '../../lib/utils';
import { Input } from '../../components/ui/input';
import type { GameSummary } from '../../types/host';

/** A course's quizzes. There is no delete: quizzes are archived so results are kept. */
export default function QuizzesPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState<GameSummary[]>([]);
  const [archived, setArchived] = useState<GameSummary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<number | 'create' | 'import' | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [maxPlayers, setMaxPlayers] = useState('150');

  // Guards against a slow response for a previous course overwriting the current one.
  const latestCourse = useRef(courseId);

  async function load() {
    const id = courseId;
    try {
      const [a, z] = await Promise.all([
        api.get<GameSummary[]>(`/host/courses/${courseId}/games`),
        api.get<GameSummary[]>(`/host/courses/${courseId}/games?archived=true`),
      ]);
      if (latestCourse.current !== id) return;
      setActive(a);
      setArchived(z);
    } catch (err) {
      if (latestCourse.current !== id) return;
      setError(errorMessage(err, 'Failed to load quizzes'));
    } finally {
      if (latestCourse.current === id) setLoading(false);
    }
  }

  useEffect(() => {
    latestCourse.current = courseId;
    setActive([]); setArchived([]); setError(''); setLoading(true);
    void load();
  }, [courseId]);

  async function run<T>(key: typeof busy, fn: () => Promise<T>, fallback: string) {
    setBusy(key);
    setError('');
    try {
      return await fn();
    } catch (err) {
      setError(errorMessage(err, fallback));
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function createQuiz(e: React.FormEvent) {
    e.preventDefault();
    const created = await run('create', () =>
      api.post<GameSummary>(`/host/courses/${courseId}/games`, {
        title,
        description,
        max_players: Number(maxPlayers),
      }), 'Failed to create quiz');
    if (created) navigate(`/quizzes/${created.id}/edit`);
  }

  async function importQuiz(file: File) {
    const form = new FormData();
    form.append('file', file);
    const result = await run('import', () =>
      api.postForm<{ game_id: number }>(`/host/courses/${courseId}/games/import`, form),
      'Import failed');
    if (fileRef.current) fileRef.current.value = '';
    if (result) navigate(`/quizzes/${result.game_id}/edit`);
  }

  async function startRoom(g: GameSummary) {
    const room = await run(g.id, () =>
      api.post<{ room_code: string }>('/game/rooms', { game_id: g.id }),
      'Failed to start room');
    if (room) navigate(`/game/${room.room_code}/lobby`);
  }

  async function archive(g: GameSummary) {
    if (!confirm(`Archive "${g.title}"? It keeps its results but can't start rooms until restored.`)) return;
    if (await run(g.id, () => api.post(`/host/games/${g.id}/archive`), 'Archive failed') !== undefined) await load();
  }

  async function restore(g: GameSummary) {
    if (await run(g.id, () => api.post(`/host/games/${g.id}/restore`), 'Restore failed') !== undefined) await load();
  }

  async function exportQuiz(g: GameSummary) {
    await run(g.id, () => api.download(`/host/games/${g.id}/export`), 'Export failed');
  }

  if (loading) {
    return (
      <div aria-busy="true">
        <span className="sr-only">Loading quizzes</span>
        <div className="flex items-center justify-between gap-4">
          <div className="skeleton h-9 w-40 rounded-lg" aria-hidden="true" />
          <div className="skeleton h-11 w-56 rounded-lg" aria-hidden="true" />
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-44 rounded-2xl" aria-hidden="true" />
          ))}
        </div>
      </div>
    );
  }

  const plural = (n: number) => `${n} question${n !== 1 ? 's' : ''}`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="text-heading text-primary">Quizzes</h2>
          <Badge tone="neutral" size="md" className="tabular-nums" aria-label={`${active.length} active`}>
            {active.length}
          </Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void importQuiz(f); }}
          />
          <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={busy === 'import'}>
            {busy === 'import' ? <Spinner /> : <Upload size={16} aria-hidden="true" />}
            {busy === 'import' ? 'Importing…' : 'Import JSON'}
          </Button>
          <Button onClick={() => setShowForm((v) => !v)} aria-expanded={showForm}>
            <Plus
              size={16}
              aria-hidden="true"
              className={cn('transition-transform duration-base ease-spring', showForm && 'rotate-45')}
            />
            New quiz
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="animate-slide-down rounded-lg bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}

      {showForm && (
        <Card variant="glass-strong" className="animate-slide-down">
          <CardHeader>
            <CardTitle className="text-subheading">New quiz</CardTitle>
            <CardDescription>The quiz belongs to this course permanently.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={(e) => void createQuiz(e)} className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="new-quiz-title" className="block text-sm font-semibold text-secondary">Title</label>
                <Input id="new-quiz-title" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="new-quiz-description" className="block text-sm font-semibold text-secondary">Description</label>
                <Input id="new-quiz-description" placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="new-quiz-max" className="block text-sm font-semibold text-secondary">Max players</label>
                <Input id="new-quiz-max" type="number" min="1" max="500" className="w-32 tabular-nums" value={maxPlayers} onChange={(e) => setMaxPlayers(e.target.value)} />
              </div>
              <div className="flex flex-wrap gap-2 pt-2">
                <Button type="submit" loading={busy === 'create'} disabled={!title.trim()}>Create</Button>
                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {active.length === 0 && (
        <div className="flex animate-scale-in flex-col items-center gap-2 rounded-2xl glass-subtle px-6 py-12 text-center">
          <FileQuestion size={32} className="text-muted" aria-hidden="true" />
          <p className="text-body-lg font-semibold text-primary">No quizzes yet.</p>
          <p className="text-body text-secondary">Create a new quiz or import one from a JSON file.</p>
        </div>
      )}

      {active.length > 0 && (
        <ul className="stagger grid gap-4 md:grid-cols-2">
          {active.map((g) => (
            <li key={g.id} className="flex animate-slide-up flex-col rounded-2xl glass p-5 sm:p-6">
              <div className="flex items-start justify-between gap-3">
                <h3 className="min-w-0 break-words text-subheading text-primary">{g.title}</h3>
                {g.has_active_room && (
                  <span title="A room using this quiz is open; edits are locked">
                    <Badge tone="warning">
                      <Lock size={12} aria-hidden="true" /> Room open
                    </Badge>
                  </span>
                )}
              </div>
              {g.description && (
                <p className="mt-1 line-clamp-2 text-body text-secondary">{g.description}</p>
              )}
              <p className="mb-5 mt-3 flex-1 text-caption text-muted">
                <span className="font-semibold tabular-nums text-secondary">{plural(g.question_count)}</span>
              </p>

              <div className="flex items-center gap-2 border-t border-line pt-4">
                <Button size="sm" onClick={() => void startRoom(g)} disabled={busy === g.id || g.question_count === 0}>
                  <Play size={14} aria-hidden="true" /> Start room
                </Button>
                <Button size="sm" variant="secondary" onClick={() => navigate(`/quizzes/${g.id}/edit`)}>
                  <Pencil size={14} aria-hidden="true" /> Edit
                </Button>
                <div className="ml-auto flex shrink-0 gap-0.5">
                  <Button size="sm" variant="ghost" className="px-2.5" onClick={() => void exportQuiz(g)} disabled={busy === g.id} aria-label={`Export ${g.title}`} title="Export">
                    <Download size={16} aria-hidden="true" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="px-2.5"
                    onClick={() => void archive(g)}
                    disabled={busy === g.id || g.has_active_room}
                    title={g.has_active_room ? 'End the open room first' : 'Archive'}
                    aria-label={`Archive ${g.title}`}
                  >
                    <Archive size={16} aria-hidden="true" />
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      <section className="pt-2">
        <button
          onClick={() => setShowArchived((v) => !v)}
          aria-expanded={showArchived}
          aria-controls="archived-quizzes"
          className="group inline-flex min-h-11 items-center gap-2 rounded-lg px-2 -ml-2 text-body font-semibold text-secondary transition-colors duration-fast hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
        >
          <ChevronRight
            size={18}
            aria-hidden="true"
            className={cn('transition-transform duration-base ease-smooth', showArchived && 'rotate-90')}
          />
          Archived quizzes ({archived.length})
        </button>
        {showArchived && (
          <ul id="archived-quizzes" className="stagger mt-3 space-y-2">
            {archived.length === 0 && <li className="animate-fade-in px-2 text-body text-muted">None.</li>}
            {archived.map((g) => (
              <li
                key={g.id}
                className="flex animate-slide-down flex-wrap items-center justify-between gap-3 rounded-xl glass-subtle px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="break-words font-semibold text-secondary">{g.title}</p>
                  <p className="text-caption text-muted">
                    <span className="tabular-nums">{plural(g.question_count)}</span>, archived
                  </p>
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/quizzes/${g.id}/edit`)}>
                    <Eye size={14} aria-hidden="true" /> View
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void exportQuiz(g)} disabled={busy === g.id} aria-label={`Export ${g.title}`} title="Export">
                    <Download size={16} aria-hidden="true" />
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => void restore(g)} disabled={busy === g.id}>
                    <RotateCcw size={14} aria-hidden="true" /> Restore
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
