import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, WifiOff } from 'lucide-react';
import { isTokenExpired } from '../../lib/utils';
import { Button } from '../../components/ui/button';
import { io, Socket } from 'socket.io-client';
import type {
  AnswerResultPayload,
  PlayerGameOverPayload,
  PlayerJoinedPayload,
  PlayerPhase,
  PlayerResultsPayload,
  QuestionPayload,
  SyncStatePayload,
} from '../../types/game';

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface GameContextValue {
  phase: PlayerPhase;
  gameStatus: 'LOBBY' | 'IN_PROGRESS';
  roomCode: string;
  playerCount: number;
  hostDisconnected: boolean;
  currentQuestion: QuestionPayload | null;
  questionLocked: boolean;
  lastAnswerData: Record<string, unknown> | null;
  answerResult: AnswerResultPayload | null;
  questionResults: PlayerResultsPayload | null;
  gameOver: PlayerGameOverPayload | null;
  // Bumped on every new_question push (also a resend of the same question after a
  // reconnect), so QuestionPage can reset its local answer state per push.
  questionKey: number;
  emitAnswer: (questionId: number, answerData: Record<string, unknown>, answerTimeMs: number) => void;
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
  const navigate = useNavigate();
  // react-router 6 returns a new navigate function on every pathname change; keeping it in
  // a ref keeps the socket effect below from tearing down and reopening the connection on
  // every in-game navigation (which dropped lock/unlock events between sockets).
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const socketRef = useRef<Socket | null>(null);
  // Ref mirrors phase so socket event closures (registered once) can read current value.
  const phaseRef = useRef<PlayerPhase>('lobby');
  const currentQuestionIdRef = useRef<number | null>(null);
  // True once this socket's join_room succeeded (its sync_state arrived); reset on connect.
  const joinedRef = useRef(false);

  const [phase, setPhase] = useState<PlayerPhase>('lobby');
  const [gameStatus, setGameStatus] = useState<'LOBBY' | 'IN_PROGRESS'>('LOBBY');
  const [playerCount, setPlayerCount] = useState(0);
  const [hostDisconnected, setHostDisconnected] = useState(false);
  const [currentQuestion, setCurrentQuestion] = useState<QuestionPayload | null>(null);
  const [questionLocked, setQuestionLocked] = useState(false);
  const [lastAnswerData, setLastAnswerData] = useState<Record<string, unknown> | null>(null);
  const [answerResult, setAnswerResult] = useState<AnswerResultPayload | null>(null);
  const [questionResults, setQuestionResults] = useState<PlayerResultsPayload | null>(null);
  const [gameOver, setGameOver] = useState<PlayerGameOverPayload | null>(null);
  const [questionKey, setQuestionKey] = useState(0);
  // Blocking error (connection or join failure): replaces the game with an error screen.
  const [error, setError] = useState('');
  // Recoverable error after joining (e.g. a late answer): a short notice over the game.
  const [notice, setNotice] = useState('');

  // While the banner shows, shift the theme toggle and the reserved top strip down by its height.
  const bannerShown = hostDisconnected && phase !== 'gameover';
  const { pathname } = useLocation();
  useEffect(() => {
    const root = document.documentElement;
    if (bannerShown) root.style.setProperty('--banner-h', '2.25rem');
    else root.style.removeProperty('--banner-h');
    return () => { root.style.removeProperty('--banner-h'); };
  }, [bannerShown]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token || isTokenExpired(token)) {
      localStorage.removeItem('token');
      navigateRef.current(code ? `/name/${code}` : '/join', { replace: true });
      return;
    }
    if (!code) {
      navigateRef.current('/join', { replace: true });
      return;
    }

    const sock = io({
      path: '/socket.io',
      auth: (cb) => cb({ token: localStorage.getItem('token') ?? '' }),
      transports: ['websocket', 'polling'],
    });
    socketRef.current = sock;

    sock.on('connect', () => {
      joinedRef.current = false;
      setError('');
      if (phaseRef.current !== 'gameover') {
        sock.emit('join_room', { room_code: code, role: 'PLAYER' });
      }
    });

    sock.on('connect_error', (err) => {
      if (phaseRef.current !== 'gameover') {
        setError(`Connection error: ${err.message}`);
      }
    });

    sock.on('sync_state', (data: SyncStatePayload) => {
      // A sync built before the game ended can arrive after game_over: stay on game over.
      if (phaseRef.current === 'gameover') return;
      joinedRef.current = true;
      if (data.gameOver) {
        // Game-over summary (reload of game over, or a join that raced the final
        // advance): go to game over from whatever screen this is (results, feedback...).
        phaseRef.current = 'gameover';
        setGameOver(data.gameOver);
        setPhase('gameover');
        navigateRef.current(`/game/${code}/gameover`, { replace: true });
        sock.disconnect();
        return;
      }
      setPlayerCount(data.playerCount ?? 0);
      // Mirror the server's flag both ways so a stale local lock can't survive a resync.
      setQuestionLocked(!!data.questionLocked);
      if (data.status === 'LOBBY') {
        setGameStatus('LOBBY');
        setPhase('lobby');
        navigateRef.current(`/game/${code}/lobby`, { replace: true });
      } else if (data.status === 'IN_PROGRESS') {
        // Game already running. An unanswered open question is pushed right after as
        // new_question; the other screens are restored here (page reload or reconnect).
        setGameStatus('IN_PROGRESS');
        // The question on screen and this player's own answer to it, so the results
        // screen's "You answered" line (and a later live results event) survive a reload.
        if (data.question) {
          currentQuestionIdRef.current = data.question.questionId;
          setCurrentQuestion(data.question);
          setLastAnswerData(
            data.yourAnswer?.questionId === data.question.questionId
              ? data.yourAnswer.answerData
              : null,
          );
        }
        if (data.questionPhase === 'RESULTS' && data.questionResults) {
          setQuestionResults(data.questionResults);
          setPhase('results');
          navigateRef.current(`/game/${code}/results`, { replace: true });
        } else if (data.questionPhase === 'QUESTION' && data.hasAnswered) {
          setPhase('feedback');
          navigateRef.current(`/game/${code}/feedback`, { replace: true });
        }
      }
    });

    sock.on('player_joined', (data: PlayerJoinedPayload) => {
      setPlayerCount(data.playerCount);
    });

    sock.on('new_question', (data: QuestionPayload) => {
      currentQuestionIdRef.current = data.questionId;
      setCurrentQuestion(data);
      setQuestionKey((k) => k + 1);
      setQuestionLocked(data.questionLocked === true);
      setLastAnswerData(null);
      setAnswerResult(null);
      setQuestionResults(null);
      // A live new_question means the host is back; a locked one (pushed on reconnect)
      // may be locked because the host is away, so it leaves the banner as it is.
      if (!data.questionLocked) setHostDisconnected(false);
      setPhase('question');
      navigateRef.current(`/game/${code}/question`);
    });

    // Lock events name their question; ignore a late one from an earlier question.
    const isStale = (questionId: number) =>
      currentQuestionIdRef.current !== null && questionId !== currentQuestionIdRef.current;

    sock.on('question_locked', (data: { questionId: number }) => {
      if (isStale(data.questionId)) return;
      setQuestionLocked(true);
    });

    sock.on('question_unlocked', (data: { questionId: number }) => {
      if (isStale(data.questionId)) return;
      setQuestionLocked(false);
    });

    sock.on('answer_received', (data: AnswerResultPayload) => {
      if (data.alreadyAnswered) return; // ignore duplicate-submit echo
      setAnswerResult(data);
      setPhase('feedback');
      navigateRef.current(`/game/${code}/feedback`);
    });

    sock.on('question_results', (data: PlayerResultsPayload) => {
      setQuestionResults(data);
      setPhase('results');
      navigateRef.current(`/game/${code}/results`);
    });

    sock.on('game_over', (data: PlayerGameOverPayload) => {
      phaseRef.current = 'gameover';
      setGameOver(data);
      setPhase('gameover');
      navigateRef.current(`/game/${code}/gameover`);
      sock.disconnect();
    });

    sock.on('host_disconnected', () => {
      setHostDisconnected(true);
    });

    sock.on('host_reconnected', () => {
      setHostDisconnected(false);
    });

    sock.on('game_abandoned', () => {
      navigateRef.current('/join', { replace: true });
    });

    // Before the join succeeds (auth, room not accepting players...) an error is fatal for
    // this socket. After it, errors are recoverable (a late answer gets "Question is locked",
    // "No active question"...): show a short notice and keep the game running.
    sock.on('error', (data: { message: string }) => {
      if (phaseRef.current === 'gameover') return;
      if (joinedRef.current) setNotice(data.message);
      else setError(data.message);
    });

    return () => {
      sock.disconnect();
      socketRef.current = null;
    };
  }, [code]);

  // The notice hides itself after a few seconds.
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(''), 4000);
    return () => clearTimeout(id);
  }, [notice]);

  function emitAnswer(questionId: number, answerData: Record<string, unknown>, answerTimeMs: number) {
    setLastAnswerData(answerData);
    socketRef.current?.emit('submit_answer', { question_id: questionId, answer_data: answerData, answer_time_ms: answerTimeMs });
  }

  if (error && phase !== 'gameover') {
    return (
      <div className="mx-auto flex min-h-screen w-full max-w-md items-center justify-center px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
        <div role="alert" className="glass-strong flex w-full animate-scale-in flex-col items-center gap-4 rounded-3xl px-6 py-8 text-center">
          <span className="flex size-14 animate-pop items-center justify-center rounded-full bg-danger-tint text-danger">
            <AlertTriangle className="size-7" aria-hidden="true" />
          </span>
          <p className="break-words text-body-lg font-semibold text-danger">{error}</p>
          <Button size="lg" className="w-full" onClick={() => navigate('/join')}>
            Back to Join
          </Button>
        </div>
      </div>
    );
  }

  return (
    <GameContext.Provider
      value={{ phase, gameStatus, roomCode: code, playerCount, hostDisconnected, currentQuestion, questionLocked, lastAnswerData, answerResult, questionResults, gameOver, questionKey, emitAnswer }}
    >
      {bannerShown && (
        <div role="status" className="fixed inset-x-0 top-0 z-50 flex h-9 animate-slide-down items-center justify-center gap-2 bg-warning px-3 text-sm font-semibold text-warning-fg shadow-raised">
          <WifiOff className="size-4 shrink-0" aria-hidden="true" />
          <span className="truncate">Host disconnected — waiting for them to reconnect…</span>
        </div>
      )}
      {notice && phase !== 'gameover' && (
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex justify-center px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <p role="alert" className="glass-strong flex max-w-md animate-slide-up items-center gap-2 rounded-2xl px-4 py-3 text-body font-semibold text-danger">
            <AlertTriangle className="size-5 shrink-0" aria-hidden="true" />
            <span className="break-words">{notice}</span>
          </p>
        </div>
      )}
      {/* Game over has its own visible h1; every other screen gets one for screen readers. */}
      {!pathname.endsWith('/gameover') && <h1 className="sr-only">Buzzer game {code}</h1>}
      <Outlet />
    </GameContext.Provider>
  );
}
