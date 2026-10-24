import { QRCodeSVG } from 'qrcode.react';
import { Play, Users } from 'lucide-react';
import { useGame } from './GameLayout';
import { Button } from '../../components/ui/button';
import { staggerStyle } from '../../components/ui/motion';
import { cn } from '../../lib/utils';

export default function LobbyPage() {
  const { roomCode, gameTitle, playerCount, emitAdvance, autoAdvance, setAutoAdvance } = useGame();

  const playerJoinUrl = `${window.location.origin}/player/join?code=${roomCode}`;
  const waiting = playerCount === 0;

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-6 px-6 pb-16 pt-20 [@media(min-height:760px)]:gap-10">
      {/* Quiz name */}
      {gameTitle && (
        <h1 className="max-w-5xl text-center text-title text-primary text-balance animate-slide-down">{gameTitle}</h1>
      )}

      {/* Join panel: QR tile + room code. The room code is the hero of this screen. */}
      <section
        aria-label="How to join"
        className="glass-strong rounded-3xl p-6 md:p-8 flex flex-col md:flex-row items-center gap-6 md:gap-10 animate-scale-in"
      >
        <div className="shrink-0 rounded-2xl bg-white p-4 shadow-float">{/* theme-exempt: QR code must stay black on white */}
          <QRCodeSVG
            value={playerJoinUrl}
            size={224}
            title="QR code to join this game"
            className="size-40 [@media(min-height:760px)]:size-56"
          />
        </div>

        <div className="flex flex-col items-center md:items-start gap-3 text-center md:text-left">
          <p className="text-xl font-semibold text-muted">Room code</p>
          <p className="select-all font-mono text-code font-bold text-primary tabular-nums leading-none">
            {roomCode}
          </p>
          {/* Join instructions */}
          <p className="max-w-md text-xl text-secondary">
            Scan QR code or go to{' '}
            <span className="inline-block max-w-full font-semibold text-accent-text">{window.location.host}/player</span>
          </p>
        </div>
      </section>

      {/* Player count, then the controls */}
      <div className="w-full max-w-4xl flex flex-col items-center gap-5 animate-slide-up" style={staggerStyle(2)}>
        <div
          aria-live="polite"
          className="glass-subtle rounded-pill flex items-center gap-3 px-6 py-3 text-xl text-secondary"
        >
          {/* Live dot: the room is open and listening for joins */}
          <span className="size-3 shrink-0 rounded-pill bg-success animate-pulse-soft" aria-hidden="true" />
          <Users className="size-6 text-accent-text" aria-hidden="true" />
          {waiting ? (
            <span>Waiting for players to join…</span>
          ) : (
            <span className="flex items-baseline gap-2">
              <span key={playerCount} className="inline-block text-3xl font-bold text-primary tabular-nums animate-pop">
                {playerCount}
              </span>
              <span>player{playerCount !== 1 ? 's' : ''} joined</span>
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
          {/* Auto-advance toggle */}
          <button
            type="button"
            role="switch"
            aria-checked={autoAdvance}
            className="group flex min-h-12 items-center gap-3 rounded-lg px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
            onClick={() => setAutoAdvance(!autoAdvance)}
          >
            <span
              className={cn(
                'relative h-8 w-14 shrink-0 rounded-pill transition-colors duration-base ease-swap',
                autoAdvance ? 'bg-accent' : 'bg-track'
              )}
            >
              <span
                className={cn(
                  'absolute top-1 size-6 rounded-pill shadow-soft transition-transform duration-base ease-spring',
                  autoAdvance ? 'translate-x-7 bg-accent-fg' : 'translate-x-1 border-2 border-line-strong bg-surface'
                )}
              />
            </span>
            <span className="select-none text-xl text-secondary transition-colors duration-fast group-hover:text-primary">
              Auto-advance — run game hands-free
            </span>
          </button>

          {/* Start button */}
          <Button size="xl" onClick={emitAdvance} disabled={waiting} className="min-w-64 px-12">
            {waiting ? 'Waiting for players…' : (<><Play className="size-6" aria-hidden="true" />Start Game</>)}
          </Button>
        </div>
      </div>
    </div>
  );
}
