import { Users } from 'lucide-react';
import { useGame } from './GameLayout';
import { Badge } from '../../components/ui/badge';

export default function LobbyPage() {
  const { roomCode, playerCount, gameStatus } = useGame();

  const inProgress = gameStatus === 'IN_PROGRESS';
  const statusText = inProgress
    ? 'Waiting for next question…'
    : 'Waiting for host to start…';

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col items-center justify-center px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
      <div className="stagger flex w-full flex-col items-center gap-5 text-center">
        <p className="animate-slide-up text-heading text-primary">{inProgress ? 'Get ready' : "You're in!"}</p>

        <div className="glass-strong flex w-full animate-scale-in flex-col items-center gap-2 rounded-3xl px-6 py-7">
          <p className="text-body text-secondary">Room</p>
          <p className="break-all font-mono text-code text-primary">{roomCode}</p>
        </div>

        <div className="glass flex w-full animate-slide-up flex-col items-center gap-4 rounded-2xl px-6 py-6">
          {/* Three dots breathe in turn while the player waits; still dots under reduced motion. */}
          <div className="flex items-center gap-2.5" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="size-3 animate-pulse-soft rounded-full bg-accent"
                style={{ animationDelay: `${i * 180}ms` }}
              />
            ))}
          </div>
          <p role="status" className="text-body-lg font-semibold text-primary">{statusText}</p>
          <Badge tone="neutral" size="lg" className="tabular-nums">
            <Users className="size-4" aria-hidden="true" />
            <span key={playerCount} className="animate-tick">{playerCount}</span>
            player{playerCount !== 1 ? 's' : ''} in room
          </Badge>
        </div>
      </div>
    </div>
  );
}
