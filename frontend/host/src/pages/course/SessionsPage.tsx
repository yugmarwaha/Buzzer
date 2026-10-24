import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Download, FileText, ChevronDown, ChevronUp, UserCheck, Users, Calendar, ClipboardList } from 'lucide-react';
import { api, errorMessage } from '../../lib/api';
import { Button } from '../../components/ui/button';
import { Card } from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Badge, type BadgeTone } from '../../components/ui/badge';
import { staggerStyle } from '../../components/ui/motion';
import { cn } from '../../lib/utils';
import { Sparkline } from '../../components/charts';
import type { SessionItem } from '../../types/host';

interface Guest { id: string; display_name: string | null; email: string | null }

interface ExportOptions {
  format: 'canvas' | 'raw';
  title: string;
  assignmentId: string;
  sisDomain: string;
  rosterOnly: boolean;
  perQuestion: boolean;
}

const STATUS_TONE: Record<string, BadgeTone> = {
  LOBBY: 'neutral',
  IN_PROGRESS: 'success',
  COMPLETED: 'accent',
  ABANDONED: 'danger',
};

function statusLabel(s: string) {
  if (s === 'IN_PROGRESS') return 'In Progress';
  return s.charAt(0) + s.slice(1).toLowerCase();
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Finished sessions of one course (any host's): COMPLETED, plus ABANDONED ones that have
 * answers. Any course host can download them. Sessions can't be deleted.
 */
export default function SessionsPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [exportOpts, setExportOpts] = useState<Record<string, ExportOptions>>({});
  const [downloading, setDownloading] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState<string | null>(null);
  const [mergeFor, setMergeFor] = useState<string | null>(null);
  const [guests, setGuests] = useState<Guest[]>([]);
  const [netids, setNetids] = useState<Record<string, string>>({});

  // Guards against a slow response for a previous course overwriting the current one.
  const latestCourse = useRef(courseId);

  async function load() {
    const id = courseId;
    setLoading(true);
    setError('');
    try {
      const data = await api.get<SessionItem[]>(`/host/courses/${courseId}/sessions`);
      if (latestCourse.current !== id) return;
      setSessions(data);
    } catch (err) {
      if (latestCourse.current !== id) return;
      setError(errorMessage(err, 'Failed to load sessions'));
    } finally {
      if (latestCourse.current === id) setLoading(false);
    }
  }

  useEffect(() => {
    latestCourse.current = courseId;
    setSessions([]); setExpandedId(null); setMergeFor(null);
    void load();
  }, [courseId]);

  async function openMerge(sessionId: string) {
    if (mergeFor === sessionId) { setMergeFor(null); return; }
    setError('');
    try {
      setGuests(await api.get<Guest[]>(`/game/sessions/${sessionId}/guests`));
      setMergeFor(sessionId);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load guests'));
    }
  }

  async function mergeGuest(sessionId: string, guestId: string) {
    const target = (netids[guestId] ?? '').trim();
    if (!target) return;
    setError('');
    try {
      await api.post(`/game/sessions/${sessionId}/merge-guest`, {
        guest_user_id: guestId,
        target_netid: target,
      });
      setGuests((prev) => prev.filter((g) => g.id !== guestId));
    } catch (err) {
      setError(errorMessage(err, 'Merge failed'));
    }
  }

  function getOpts(sessionId: string, gameTitle: string): ExportOptions {
    return exportOpts[sessionId] ?? {
      format: 'canvas',
      title: gameTitle,
      assignmentId: '',
      sisDomain: 'wisc.edu',
      rosterOnly: true,
      perQuestion: false,
    };
  }

  function setOpt<K extends keyof ExportOptions>(sessionId: string, gameTitle: string, key: K, val: ExportOptions[K]) {
    setExportOpts((prev) => ({
      ...prev,
      [sessionId]: { ...getOpts(sessionId, gameTitle), [key]: val },
    }));
  }

  async function handleExport(session: SessionItem) {
    const opts = getOpts(session.session_id, session.game_title);
    setDownloading(session.session_id);
    setError('');
    try {
      const params = new URLSearchParams({ format: opts.format });
      if (opts.format === 'canvas') {
        const columnTitle = opts.assignmentId.trim()
          ? `${opts.title} (${opts.assignmentId.trim()})`
          : opts.title;
        if (columnTitle) params.set('title', columnTitle);
        params.set('sis_domain', opts.sisDomain);
        params.set('roster_only', String(opts.rosterOnly));
        params.set('per_question', String(opts.perQuestion));
      } else {
        params.set('per_question', String(opts.perQuestion));
      }
      await api.download(`/host/sessions/${session.session_id}/export?${params}`);
    } catch (err) {
      setError(errorMessage(err, 'Export failed'));
    } finally {
      setDownloading(null);
    }
  }

  async function handleReport(sessionId: string) {
    setReportLoading(sessionId);
    setError('');
    try {
      await api.download(`/host/sessions/${sessionId}/report`);
    } catch (err) {
      setError(errorMessage(err, 'Report download failed'));
    } finally {
      setReportLoading(null);
    }
  }

  // Oldest to newest, so the trend reads left to right.
  const trend = [...sessions].sort((a, b) => a.created_at.localeCompare(b.created_at));

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h2 className="text-heading text-primary">Finished sessions</h2>
        {!loading && sessions.length > 0 && (
          <p className="mt-1 text-body text-muted tabular-nums">
            {sessions.length} session{sessions.length !== 1 ? 's' : ''}
          </p>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-4 animate-fade-in rounded-lg border border-danger bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}

      {!loading && sessions.length >= 3 && new Set(trend.map((t) => t.player_count)).size > 1 && (
        <div className="glass mb-6 animate-fade-in rounded-2xl p-4">
          <h3 className="mb-2 text-subheading text-primary">Players per session</h3>
          <Sparkline
            title="Players per session"
            values={trend.map((t) => t.player_count)}
            labels={trend.map((t) => `${new Date(t.created_at).toLocaleDateString()} · ${t.game_title}`)}
          />
          <p className="mt-2 text-caption text-muted">Oldest session on the left, newest on the right.</p>
        </div>
      )}

      {loading ? (
        <div className="space-y-3" role="status" aria-label="Loading sessions">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-28 rounded-2xl" />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="glass animate-fade-in rounded-2xl px-6 py-14 text-center">
          <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-accent-hint text-accent-text">
            <ClipboardList size={28} aria-hidden="true" />
          </div>
          <h3 className="text-heading text-primary">No finished sessions yet.</h3>
          <p className="mx-auto mt-2 max-w-md text-body text-muted">
            Host a quiz from the Quizzes tab. Finished games appear here with reports and score downloads.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {sessions.map((s, idx) => {
            const isExpanded = expandedId === s.session_id;
            const opts = getOpts(s.session_id, s.game_title);
            const isDownloading = downloading === s.session_id;

            return (
              <Card
                key={s.session_id}
                variant="glass"
                className="animate-slide-up overflow-hidden"
                style={staggerStyle(idx)}
              >
                {/* Session row */}
                <div className="flex flex-wrap items-center gap-x-6 gap-y-4 px-6 py-5">
                  <div className="min-w-0 flex-1 basis-64">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <span className="text-subheading text-primary">{s.game_title}</span>
                      <Badge tone={STATUS_TONE[s.status] ?? 'neutral'}>{statusLabel(s.status)}</Badge>
                    </div>
                    <p className="mt-1 text-body text-muted">
                      Hosted by {s.host_display_name ?? 'Unknown host'}
                    </p>
                    <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-body text-secondary">
                      <span>
                        Room <span className="font-mono text-primary">{s.room_code}</span>
                      </span>
                      <span className="inline-flex items-center gap-1.5 tabular-nums">
                        <Users size={15} aria-hidden="true" className="text-muted" />
                        {s.player_count} player{s.player_count !== 1 ? 's' : ''}
                      </span>
                      <span className="inline-flex items-center gap-1.5 tabular-nums">
                        <Calendar size={15} aria-hidden="true" className="text-muted" />
                        {fmtDate(s.created_at)}
                        {s.completed_at && <span> → {fmtDate(s.completed_at)}</span>}
                      </span>
                    </p>
                  </div>
                  <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={reportLoading === s.session_id}
                      loading={reportLoading === s.session_id}
                      onClick={() => void handleReport(s.session_id)}
                      title="Download standalone HTML report"
                    >
                      <FileText size={15} />
                      {reportLoading === s.session_id ? 'Building…' : 'Report'}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      aria-expanded={isExpanded}
                      onClick={() => setExpandedId(isExpanded ? null : s.session_id)}
                    >
                      <Download size={15} /> Export
                      {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </Button>
                    {s.can_merge_guests && (
                      <Button size="sm" variant="ghost" onClick={() => void openMerge(s.session_id)} title="Merge guest scores into NetID accounts">
                        <UserCheck size={15} /> Guests
                      </Button>
                    )}
                  </div>
                </div>

                {mergeFor === s.session_id && (
                  <div className="animate-fade-in space-y-3 border-t border-line px-6 py-5">
                    <p className="text-subheading text-primary">Merge guests into NetID accounts</p>
                    {guests.length === 0 && <p className="text-body text-muted">No unmerged guests.</p>}
                    {guests.map((g) => (
                      <div key={g.id} className="flex flex-wrap items-center gap-2">
                        <span className="w-48 truncate text-body text-primary">{g.display_name ?? g.email ?? g.id}</span>
                        <Input
                          value={netids[g.id] ?? ''}
                          onChange={(e) => setNetids((prev) => ({ ...prev, [g.id]: e.target.value }))}
                          placeholder="netid"
                          className="text-sm w-40"
                        />
                        <Button size="sm" onClick={() => void mergeGuest(s.session_id, g.id)}>Merge</Button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Export options panel */}
                {isExpanded && (
                  <div className="animate-fade-in space-y-5 border-t border-line px-6 py-5">
                    {/* Format selector */}
                    <div>
                      <p className="mb-2 text-label uppercase text-secondary">Export format</p>
                      <div className="flex gap-2">
                        {(['canvas', 'raw'] as const).map((fmt) => (
                          <button
                            key={fmt}
                            onClick={() => setOpt(s.session_id, s.game_title, 'format', fmt)}
                            aria-pressed={opts.format === fmt}
                            className={cn(
                              'min-h-11 rounded-lg border px-5 py-2 text-base font-semibold transition-colors duration-fast',
                              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
                              opts.format === fmt
                                ? 'border-accent bg-accent text-accent-fg shadow-button'
                                : 'border-line-strong bg-surface text-secondary hover:bg-raised hover:text-primary',
                            )}
                          >
                            {fmt === 'canvas' ? 'Canvas Gradebook' : 'Raw CSV'}
                          </button>
                        ))}
                      </div>
                    </div>

                    {opts.format === 'canvas' && (
                      <>
                        {/* Assignment title + ID */}
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                          <div>
                            <label className="mb-1.5 block text-caption text-secondary">Assignment name</label>
                            <Input
                              value={opts.title}
                              onChange={(e) => setOpt(s.session_id, s.game_title, 'title', e.target.value)}
                              placeholder="Quiz title…"
                              className="text-sm"
                            />
                          </div>
                          <div>
                            <label className="mb-1.5 block text-caption text-secondary">
                              Canvas assignment ID
                              <span className="text-muted ml-1">(optional)</span>
                            </label>
                            <Input
                              value={opts.assignmentId}
                              onChange={(e) => setOpt(s.session_id, s.game_title, 'assignmentId', e.target.value)}
                              placeholder="e.g. 12345"
                              className="text-sm font-mono"
                            />
                          </div>
                        </div>
                        {/* Column header preview */}
                        <p className="-mt-2 text-body text-muted">
                          Column header preview:{' '}
                          <span className="font-mono text-secondary">
                            {opts.assignmentId.trim()
                              ? `${opts.title} (${opts.assignmentId.trim()})`
                              : opts.title || '—'}
                          </span>
                        </p>
                        {/* SIS domain */}
                        <div className="max-w-xs">
                          <label className="mb-1.5 block text-caption text-secondary">
                            SIS Login ID domain
                            <span className="text-muted ml-1">(appended to netid)</span>
                          </label>
                          <Input
                            value={opts.sisDomain}
                            onChange={(e) => setOpt(s.session_id, s.game_title, 'sisDomain', e.target.value)}
                            placeholder="wisc.edu"
                            className="text-sm font-mono"
                          />
                          <p className="mt-1 text-body text-muted">
                            Preview: <span className="font-mono">jsmith{opts.sisDomain ? `@${opts.sisDomain}` : ''}</span>
                          </p>
                        </div>

                        {/* Canvas options */}
                        <div className="space-y-2">
                          <label className="flex items-center gap-2 cursor-pointer select-none">
                            <input
                              type="checkbox"
                              checked={opts.rosterOnly}
                              onChange={(e) => setOpt(s.session_id, s.game_title, 'rosterOnly', e.target.checked)}
                              className="size-4 rounded-sm border-line-strong accent-accent"
                            />
                            <span className="text-sm text-secondary">
                              Roster-matched players only
                              <span className="text-muted ml-1 text-sm">(skip guests and local accounts without a netid)</span>
                            </span>
                          </label>
                          <label className="flex items-center gap-2 cursor-pointer select-none">
                            <input
                              type="checkbox"
                              checked={opts.perQuestion}
                              onChange={(e) => setOpt(s.session_id, s.game_title, 'perQuestion', e.target.checked)}
                              className="size-4 rounded-sm border-line-strong accent-accent"
                            />
                            <span className="text-sm text-secondary">
                              Per-question breakdown
                              <span className="text-muted ml-1 text-sm">(one column per question, otherwise total only)</span>
                            </span>
                          </label>
                        </div>
                      </>
                    )}

                    {opts.format === 'raw' && (
                      <p className="text-body text-muted">
                        Raw CSV includes all players, per-question scores, and totals.
                      </p>
                    )}

                    <Button
                      onClick={() => void handleExport(s)}
                      disabled={isDownloading}
                      loading={isDownloading}
                    >
                      <Download size={16} />
                      {isDownloading ? 'Downloading…' : 'Download CSV'}
                    </Button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
