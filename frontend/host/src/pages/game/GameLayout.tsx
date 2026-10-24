import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api } from '../../lib/api';
import { QRCodeSVG } from 'qrcode.react';
import { AlertTriangle, ArrowLeft, Power, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { io, Socket } from 'socket.io-client';
import type {
  AnswerPhaseEndedPayload,
  AnswerStatusPayload,
  HostGameOverPayload,
  HostPhase,
  HostResultsPayload,
  PlayerInfo,
  PlayerJoinedPayload,
  PlayerLeftPayload,
  QuestionPayload,
  SyncStatePayload,
} from '../../types/game';

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface GameContextValue {
  phase: HostPhase;
  roomCode: string;
  gameTitle: string;
  players: PlayerInfo[];
  playerCount: number;
  currentQuestion: QuestionPayload | null;
  answeredCount: number;
  allAnswered: boolean;       // every player has answered (drives auto-advance)
  answerPhaseEnded: boolean;     // timer expired or all answered (drives Lock button)
  questionLocked: boolean;       // host explicitly locked the question
  lockedTimerSeconds: number | null; // remaining seconds when locked (for reconnect)
  questionResults: HostResultsPayload | null;
  gameOver: HostGameOverPayload | null;
  autoAdvance: boolean;
  setAutoAdvance: (v: boolean) => void;
  emitAdvance: () => void;
  emitLockQuestion: () => void;
}

const GameContext = createContext<GameContextValue | null>(null);

export function useGame(): GameContextValue {
  const ctx = useContext(GameContext);
  if (!ctx) throw new Error('useGame must be used within GameLayout');
  return ctx;
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export default function GameLayout() {
  const { code = '' } = useParams<{ code: string }>();
  // The lobby already shows a large QR code and room code, so the corner panel is skipped there.
  const { pathname } = useLocation();
  const onLobby = pathname.endsWith('/lobby');
  // Game over takes no new players, so it shows no join panel either.
  const onGameOver = pathname.endsWith('/gameover');
  const navigate = useNavigate();
  // react-router 6 returns a new navigate function on every pathname change; keeping it in
  // a ref keeps the socket effect below from reconnecting on every in-game navigation (the
  // server treats that as a host disconnect: auto-lock, "host disconnected", auto-unlock).
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const socketRef = useRef<Socket | null>(null);
  const currentQuestionIdRef = useRef<number | null>(null);

  const [phase, setPhase] = useState<HostPhase>('lobby');
  const [gameTitle, setGameTitle] = useState('');
  const [players, setPlayers] = useState<PlayerInfo[]>([]);
  const [playerCount, setPlayerCount] = useState(0);
  const [currentQuestion, setCurrentQuestion] = useState<QuestionPayload | null>(null);
  const [answeredCount, setAnsweredCount] = useState(0);
  const [allAnswered, setAllAnswered] = useState(false);
  const [answerPhaseEnded, setAnswerPhaseEnded] = useState(false);
  const [questionLocked, setQuestionLocked] = useState(false);
  const [lockedTimerSeconds, setLockedTimerSeconds] = useState<number | null>(null);
  const [questionResults, setQuestionResults] = useState<HostResultsPayload | null>(null);
  const [gameOver, setGameOver] = useState<HostGameOverPayload | null>(null);
  const [autoAdvance, setAutoAdvance] = useState(false);
  // Fatal: the host could not (re)join the room. Replaces the game with an error screen.
  const [error, setError] = useState('');
  // Recoverable (room busy, a failed lock/advance or End game): a dismissible notice; the game goes on.
  const [notice, setNotice] = useState('');
  // True once this socket has joined the room (first sync_state); reset on disconnect, so a
  // failed rejoin is still fatal.
  const joinedRef = useRef(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);
  // Render-time mirrors for emitAdvance (a stable callback): the screen we advance from.
  const phaseRef = useRef<HostPhase>(phase);
  phaseRef.current = phase;
  const questionNumberRef = useRef<number | null>(null);
  questionNumberRef.current = currentQuestion?.questionNumber ?? null;
  const lastAdvanceRef = useRef<{ key: string; at: number } | null>(null);
  // Latched once game over is shown: a sync_state built before the game ended can arrive
  // after game_over and must not pull the screen back to results.
  const gameOverRef = useRef(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token || !code) {
      navigateRef.current('/courses', { replace: true });
      return;
    }

    api.get<{ game_title: string; session_id: string }>(`/game/rooms/${code}`)
      .then(data => { setGameTitle(data.game_title); setSessionId(data.session_id); })
      .catch(() => {}); // non-fatal — title stays blank if fetch fails

    const sock = io({
      path: '/socket.io',
      auth: (cb) => cb({ token: localStorage.getItem('token') ?? '' }),
      transports: ['websocket', 'polling'],
    });
    socketRef.current = sock;

    sock.on('connect', () => {
      setError('');
      setNotice('');
      lastAdvanceRef.current = null;
      sock.emit('join_room', { room_code: code, role: 'HOST' });
    });

    sock.on('connect_error', (err) => {
      const message = `Connection error: ${err.message}`;
      if (joinedRef.current) setNotice(message);
      else setError(message);
    });

    sock.on('disconnect', () => {
      joinedRef.current = false;
    });

    sock.on('sync_state', (data: SyncStatePayload) => {
      // The screen may have changed under us (also sent when the server ignores an
      // advance), so the next advance must not be dropped as a repeat.
      lastAdvanceRef.current = null;
      joinedRef.current = true;
      if (data.gameOver) {
        gameOverRef.current = true;
        setGameOver(data.gameOver);
        setPhase('gameover');
        navigateRef.current(`/game/${code}/gameover`, { replace: true });
        return;
      }
      if (gameOverRef.current) return;
      setPlayers(data.players ?? []);
      setPlayerCount(data.playerCount ?? data.players?.length ?? 0);
      if (data.status === 'LOBBY') {
        setPhase('lobby');
        navigateRef.current(`/game/${code}/lobby`, { replace: true });
      } else if (data.status === 'IN_PROGRESS') {
        if (data.currentQuestion) {
          setCurrentQuestion(data.currentQuestion);
          currentQuestionIdRef.current = data.currentQuestion.questionId;
        }
        // Mirror the server's flag both ways so a stale local lock can't survive a resync.
        setQuestionLocked(!!data.questionLocked);
        setLockedTimerSeconds(data.questionLocked ? data.timerRemainingSeconds ?? null : null);
        // The rest of the screen's data lives only in this component, so a reload would
        // otherwise show 0 answered / an empty results chart: take it from the server.
        setAnsweredCount(data.answeredCount ?? 0);
        setAllAnswered(!!data.allAnswered);
        // The answer phase is over when time has run out: the frozen time while locked, else
        // the time left from startedAt (same calc as QuestionPage). A reload after the timer
        // expired missed answer_phase_ended, and auto-advance waits for this flag.
        const q = data.currentQuestion;
        const remaining = data.questionLocked
          ? data.timerRemainingSeconds ?? null
          : q?.startedAt
            ? q.timeLimitSeconds - (Date.now() - new Date(q.startedAt).getTime()) / 1000
            : null;
        setAnswerPhaseEnded(!!data.allAnswered || (remaining !== null && remaining <= 0));
        setQuestionResults(data.questionResults ?? null);
        if (data.questionPhase === 'QUESTION') {
          setPhase('question');
          navigateRef.current(`/game/${code}/question`, { replace: true });
        } else if (data.questionPhase === 'RESULTS') {
          setPhase('results');
          navigateRef.current(`/game/${code}/results`, { replace: true });
        }
      } else if (data.status === 'ABANDONED') {
        navigateRef.current('/courses', { replace: true });
      }
    });

    sock.on('player_joined', (data: PlayerJoinedPayload) => {
      setPlayers(prev =>
        prev.some(p => p.userId === data.userId)
          ? prev
          : [...prev, { userId: data.userId, displayName: data.displayName }]
      );
      setPlayerCount(data.playerCount);
    });

    sock.on('player_left', (data: PlayerLeftPayload) => {
      setPlayers(prev => prev.filter(p => p.userId !== data.userId));
      setPlayerCount(data.playerCount);
    });

    sock.on('new_question', (data: QuestionPayload) => {
      currentQuestionIdRef.current = data.questionId;
      setCurrentQuestion(data);
      setAnsweredCount(0);
      setAllAnswered(false);
      setAnswerPhaseEnded(false);
      setQuestionLocked(false);
      setLockedTimerSeconds(null);
      setQuestionResults(null);
      setPhase('question');
      navigateRef.current(`/game/${code}/question`);
    });

    sock.on('answer_status', (data: AnswerStatusPayload) => {
      // A late count for the previous question can arrive after new_question.
      if (data.questionId !== currentQuestionIdRef.current) return;
      setAnsweredCount(data.answeredCount);
      if (data.totalPlayers) setPlayerCount(data.totalPlayers);
    });

    sock.on('answer_phase_ended', (data: AnswerPhaseEndedPayload) => {
      // Ignore stale events from a previous question's orphaned timer task
      if (data.questionId !== currentQuestionIdRef.current) return;
      setAnsweredCount(data.answeredCount);
      if (data.totalPlayers) setPlayerCount(data.totalPlayers);
      setAnswerPhaseEnded(true);
      if (data.allAnswered) setAllAnswered(true);
    });

    // Lock events name their question; ignore a late one from an earlier question.
    const isStale = (questionId: number) =>
      currentQuestionIdRef.current !== null && questionId !== currentQuestionIdRef.current;

    sock.on('question_locked', (data: { questionId: number; remainingSeconds: number }) => {
      if (isStale(data.questionId)) return;
      setQuestionLocked(true);
      setLockedTimerSeconds(data.remainingSeconds);
    });

    sock.on('question_unlocked', (data: { questionId: number }) => {
      if (isStale(data.questionId)) return;
      setQuestionLocked(false);
      setLockedTimerSeconds(null);
    });

    sock.on('question_results', (data: HostResultsPayload) => {
      setQuestionResults(data);
      setPhase('results');
      navigateRef.current(`/game/${code}/results`);
    });

    sock.on('game_over', (data: HostGameOverPayload) => {
      gameOverRef.current = true;
      setGameOver(data);
      setPhase('gameover');
      navigateRef.current(`/game/${code}/gameover`);
    });

    sock.on('game_abandoned', () => {
      navigateRef.current('/courses', { replace: true });
    });

    sock.on('error', (data: { message: string }) => {
      if (joinedRef.current) setNotice(data.message);
      else setError(data.message);
    });

    return () => {
      sock.disconnect();
      socketRef.current = null;
    };
  }, [code]);

  /** Advance from the current screen. A second call for the same screen within 3s is
   *  dropped (double click, auto-advance racing a click, or React StrictMode running the
   *  ResultsPage countdown updater twice); the dedupe resets on connect and sync_state.
   *  The server ignores an advance whose from_phase/question_index it has already left,
   *  or a duplicate of one already running, and answers it with a fresh sync_state, so a
   *  question is never skipped. An emit made while disconnected is not replayed usefully:
   *  socket.io-client flushes it before `connect`, i.e. before join_room registers the
   *  socket, so the server drops it; the sync_state after rejoining restores the screen. */
  const emitAdvance = useCallback(() => {
    const p = phaseRef.current;
    if (p === 'gameover') return;
    const fromPhase = p === 'question' ? 'QUESTION' : p === 'results' ? 'RESULTS' : 'LOBBY';
    // Unknown question number: send no index, so the server checks the phase only.
    const qn = questionNumberRef.current;
    const questionIndex = fromPhase === 'LOBBY' ? -1 : qn !== null ? qn - 1 : null;
    const key = `${fromPhase}:${questionIndex ?? '?'}`;
    const now = Date.now();
    const last = lastAdvanceRef.current;
    if (last && last.key === key && now - last.at < 3000) return;
    lastAdvanceRef.current = { key, at: now };
    socketRef.current?.emit(
      'host_advance',
      questionIndex === null
        ? { from_phase: fromPhase }
        : { from_phase: fromPhase, question_index: questionIndex }
    );
  }, []);

  const emitLockQuestion = useCallback(() => {
    socketRef.current?.emit('host_lock_question', {});
  }, []);

  /** Same HTTP end route as the Rooms tab. On success (or if it was already ended) we go
   *  straight to /courses; the server also emits game_abandoned to the players. */
  async function endGame() {
    if (!sessionId) return;
    if (!confirm('End this game now? Answers so far are kept (saved as Abandoned); a room with no answers is removed.')) return;
    setEnding(true);
    try {
      await api.post<{ outcome?: string }>(`/host/sessions/${sessionId}/end`);
      navigate('/courses', { replace: true });
    } catch (err) {
      // 404/409 = already ended (or removed) elsewhere; either way this room is over.
      if (err instanceof ApiError && (err.status === 404 || err.status === 409)) {
        navigate('/courses', { replace: true });
      } else {
        // The room is still running, so keep the game on screen and say what failed.
        setNotice(err instanceof Error ? err.message : 'Failed to end the game');
      }
    } finally {
      setEnding(false);
    }
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div role="alert" className="glass-strong rounded-3xl w-full max-w-lg p-8 flex flex-col items-center gap-6 text-center animate-scale-in">
          <AlertTriangle className="size-12 text-danger" aria-hidden="true" />
          <p className="text-xl font-semibold text-danger">{error}</p>
          <Button variant="outline" size="lg" onClick={() => navigate('/courses')}>
            <ArrowLeft className="size-5" aria-hidden="true" />
            Back to courses
          </Button>
        </div>
      </div>
    );
  }

  const playerJoinUrl = `${window.location.origin}/player/join?code=${code}`;

  return (
    <GameContext.Provider
      value={{ phase, roomCode: code, gameTitle, players, playerCount, currentQuestion, answeredCount, allAnswered, answerPhaseEnded, questionLocked, lockedTimerSeconds, questionResults, gameOver, autoAdvance, setAutoAdvance, emitAdvance, emitLockQuestion }}
    >
      {/* Lobby and game over have their own visible h1; question and results screens get one for screen readers. */}
      {pathname.endsWith('/question') && <h1 className="sr-only">Question in progress</h1>}
      {pathname.endsWith('/results') && <h1 className="sr-only">Question results</h1>}
      {onLobby ? (
        <Outlet />
      ) : (
        // Past the lobby the page content keeps the full width, so each page centers itself.
        // The join panel is a fixed bottom-right tile only when the side gutter beside the
        // widest page (max-w-6xl, 72rem) has room for it; otherwise it sits in a row below
        // the content. Either way it never covers the content. Game over has no panel.
        // pt-14 (56px, the toggle is 8px + 48px) keeps the fixed corner controls (theme toggle, End game) clear of the page header.
        <div className="flex min-h-screen flex-col pt-14">
          <div className="flex min-w-0 flex-1 flex-col">
            <Outlet />
          </div>

          {/* Join panel for late joiners, once the game is past the lobby */}
          {!onGameOver && (
            <div className="flex shrink-0 justify-center p-2 min-[1480px]:fixed min-[1480px]:bottom-4 min-[1480px]:right-4 min-[1480px]:z-20 min-[1480px]:p-0">
              <div className="glass rounded-2xl flex flex-col items-center gap-2 p-3 animate-slide-up">
                <div className="rounded-lg bg-white p-1.5">{/* theme-exempt: QR code must stay black on white */}
                  <QRCodeSVG value={playerJoinUrl} size={96} title="QR code to join this game" />
                </div>
                <div className="text-center">
                  <p className="mb-1 text-sm font-semibold text-muted leading-none">Room code</p>
                  <p className="font-mono text-2xl font-bold tracking-widest text-primary tabular-nums leading-none">{code}</p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Recoverable error: top center, clear of the corner controls; the game keeps running. */}
      {notice && (
        <div
          role="alert"
          className="glass-strong fixed left-1/2 top-4 z-40 flex w-[min(36rem,calc(100%-2rem))] -translate-x-1/2 items-center gap-3 rounded-2xl px-4 py-3 animate-slide-down"
        >
          <AlertTriangle className="size-5 shrink-0 text-danger" aria-hidden="true" />
          <p className="min-w-0 flex-1 text-sm font-semibold text-danger">{notice}</p>
          <button
            type="button"
            aria-label="Dismiss message"
            onClick={() => setNotice('')}
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-secondary transition-colors duration-fast hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* End game — not offered once the game is over */}
      {phase !== 'gameover' && sessionId && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => void endGame()}
          disabled={ending}
          className="fixed top-4 right-4 z-30 border-danger bg-surface text-danger shadow-soft hover:bg-danger-tint"
        >
          <Power className="size-4" aria-hidden="true" />
          {ending ? 'Ending…' : 'End game'}
        </Button>
      )}
    </GameContext.Provider>
  );
}
