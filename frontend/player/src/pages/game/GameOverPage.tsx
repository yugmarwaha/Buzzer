import { useNavigate } from 'react-router-dom';
import { Award, Flag, Medal, Trophy, type LucideIcon } from 'lucide-react';
import { useGame } from './GameLayout';
import { Button } from '../../components/ui/button';
import { Card } from '../../components/ui/card';
import { Spinner } from '../../components/ui/spinner';
import { staggerStyle } from '../../components/ui/motion';
import { QuestionRow, RecapStrip } from '../../components/QuestionRecap';
import { cn } from '../../lib/utils';

// Places 1-3 get a medal look built from status/accent tints (no metal tokens exist);
// everyone else gets a neutral flag. Full class names spelled out for the Tailwind scan.
const PLACE: Record<1 | 2 | 3, { icon: LucideIcon; disc: string; text: string; label: string }> = {
  1: { icon: Trophy, disc: 'bg-warning-tint text-warning', text: 'text-warning', label: '1st place' },
  2: { icon: Medal, disc: 'bg-accent-hint text-accent-text', text: 'text-accent-text', label: '2nd place' },
  3: { icon: Award, disc: 'bg-success-tint text-success', text: 'text-success', label: '3rd place' },
};
const OTHER = { icon: Flag, disc: 'bg-raised text-secondary', text: 'text-accent-text', label: '' };

export default function GameOverPage() {
  const { gameOver } = useGame();
  const navigate = useNavigate();

  if (!gameOver) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 p-6">
        <Spinner className="text-accent-text size-8" />
        <p className="text-body text-muted">Loading final results…</p>
      </div>
    );
  }

  const { yourFinalScore, yourFinalRank, playerCount, questionSummary } = gameOver;
  const place = yourFinalRank >= 1 && yourFinalRank <= 3 ? PLACE[yourFinalRank as 1 | 2 | 3] : OTHER;
  const PlaceIcon = place.icon;
  const isPodium = place !== OTHER;

  return (
    <div className="min-h-screen w-full max-w-lg mx-auto flex flex-col gap-5 px-4 pt-2 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
      {/* Final rank hero */}
      <Card variant="glass-strong" className="rounded-3xl px-5 pt-6 pb-7 text-center flex flex-col items-center animate-scale-in">
        <h1 className="text-title text-primary">Game Over!</h1>

        <span
          className={cn(
            'mt-5 flex h-24 w-24 items-center justify-center rounded-pill animate-pop',
            place.disc,
          )}
          style={staggerStyle(2)}
          aria-hidden="true"
        >
          <PlaceIcon className="h-12 w-12" strokeWidth={2.25} />
        </span>
        {isPodium && (
          <p className={cn('mt-3 text-subheading', place.text)}>{place.label}</p>
        )}

        <div className="mt-2 flex items-baseline justify-center gap-3 animate-slide-up" style={staggerStyle(3)}>
          <span className={cn('text-stat tabular-nums', place.text)}>#{yourFinalRank}</span>
          <span className="text-body-lg text-muted tabular-nums">of {playerCount}</span>
        </div>

        <div className="mt-4 w-full border-t border-line pt-4 animate-slide-up" style={staggerStyle(4)}>
          <p className="text-label uppercase text-muted" aria-hidden="true">Final score</p>
          <p className="mt-1 text-heading tabular-nums text-primary">
            {yourFinalScore.toLocaleString()} pts
          </p>
        </div>
      </Card>

      {questionSummary.length > 0 && (
        <div className="animate-slide-up" style={staggerStyle(5)}>
          <RecapStrip items={questionSummary} />
        </div>
      )}

      <section className="flex flex-col gap-3" aria-labelledby="recap-heading">
        <h2 id="recap-heading" className="text-subheading text-primary">Question Recap</h2>
        {questionSummary.map((item, i) => (
          <QuestionRow key={item.questionId} item={item} index={i} style={staggerStyle(6 + i)} urls={gameOver.imageUrls} />
        ))}
      </section>

      <div className="pt-1">
        <Button
          variant="primary"
          size="lg"
          className="w-full"
          onClick={() => {
            localStorage.removeItem('token');
            navigate('/join');
          }}
        >
          Play Again
        </Button>
      </div>
    </div>
  );
}
