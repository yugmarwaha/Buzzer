import { useEffect, useState } from 'react';
import { Check, CircleDot, Minus, Trophy, X, type LucideIcon } from 'lucide-react';
import { useGame } from './GameLayout';
import { cn, formatNum, optionAnswerLabel, optionDisplay } from '../../lib/utils';
import { QuestionImage } from '../../components/QuestionImage';
import type { ImageUrls, QuestionConfig } from '../../types/game';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import { Progress } from '../../components/ui/progress';
import { Spinner } from '../../components/ui/spinner';
import { staggerStyle } from '../../components/ui/motion';

function describeAnswer(
  lastAnswerData: Record<string, unknown> | null,
  type: string,
  options: string[] | undefined,
  unit?: string,
): string | null {
  if (!lastAnswerData) return null;
  if (type === 'multiple_choice') {
    const idx = lastAnswerData.selectedIndex;
    if (typeof idx === 'number' && options && options[idx] !== undefined) {
      return optionAnswerLabel(options, idx);
    }
  }
  if (type === 'true_false') {
    return lastAnswerData.selectedValue ? 'True' : 'False';
  }
  if (type === 'fill_in_the_blank') {
    return typeof lastAnswerData.text === 'string' ? lastAnswerData.text : null;
  }
  if (type === 'multi_select') {
    const indices = lastAnswerData.selectedIndices;
    if (!Array.isArray(indices) || indices.length === 0) return 'No selection';
    return indices
      .filter((i): i is number => typeof i === 'number')
      .map((i) => (options && options[i] !== undefined ? optionAnswerLabel(options, i) : String.fromCharCode(65 + i)))
      .join(', ');
  }
  if (type === 'pin_drop') {
    const { x, y } = lastAnswerData;
    return typeof x === 'number' && typeof y === 'number' ? `(${x}, ${y})` : null;
  }
  if (type === 'closest_guess') {
    const v = lastAnswerData.value;
    return typeof v === 'number' ? `${formatNum(v)}${unit ? ` ${unit}` : ''}` : null;
  }
  return null;
}

/** T8: images of the options this player chose (multiple choice, multi-select). */
function chosenImages(
  lastAnswerData: Record<string, unknown> | null,
  config: QuestionConfig | undefined,
  urls: ImageUrls,
) {
  if (!lastAnswerData || !config?.optionImageIds) return null;
  const picked = typeof lastAnswerData.selectedIndex === 'number'
    ? [lastAnswerData.selectedIndex]
    : Array.isArray(lastAnswerData.selectedIndices)
      ? lastAnswerData.selectedIndices.filter((i): i is number => typeof i === 'number')
      : [];
  const withImage = picked.filter((i) => config.optionImageIds?.[i] != null);
  if (withImage.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {withImage.map((i) => (
        <QuestionImage
          key={i}
          id={config.optionImageIds?.[i]}
          urls={urls}
          alt={`${optionDisplay('', i)} image`}
          className="h-20 w-24 min-h-0 p-0 text-xs"
        />
      ))}
    </div>
  );
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Counts from 0 up to `target` over `ms` (ease-out), in whole numbers until the last frame,
 * which shows the exact target (partial points such as 7.53). Under reduced motion it
 * returns the target at once. Screen readers get the final value from a separate sr-only span.
 */
function useCountUp(target: number, ms = 900): number {
  const [value, setValue] = useState(() => (prefersReducedMotion() ? target : 0));
  useEffect(() => {
    if (prefersReducedMotion() || target === 0) {
      setValue(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(t < 1 ? Math.round(target * eased) : target);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

/** Points with up to 2 decimals and no trailing zeros (7.53, 7.5, 8). */
function formatPoints(v: number): string {
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

type Outcome = 'recorded' | 'correct' | 'partial' | 'incorrect' | 'none';

// Full class names spelled out so the Tailwind scan sees them.
const OUTCOME: Record<Outcome, { icon: LucideIcon; ring: string; text: string; tone: 'accent' | 'success' | 'warning' | 'danger' }> = {
  recorded: { icon: CircleDot, ring: 'bg-accent-hint text-accent-text', text: 'text-accent-text', tone: 'accent' },
  correct: { icon: Check, ring: 'bg-success-tint text-success', text: 'text-success', tone: 'success' },
  partial: { icon: Check, ring: 'bg-warning-tint text-warning', text: 'text-warning', tone: 'warning' },
  incorrect: { icon: X, ring: 'bg-danger-tint text-danger', text: 'text-danger', tone: 'danger' },
  none: { icon: Minus, ring: 'bg-danger-tint text-danger', text: 'text-danger', tone: 'danger' },
};

export default function ResultsPage() {
  const { questionResults, currentQuestion, lastAnswerData } = useGame();
  const yourPointsTarget = questionResults?.yourPoints ?? 0;
  const yourScoreTarget = questionResults?.yourScore ?? 0;
  const shownPoints = useCountUp(yourPointsTarget);
  const shownScore = useCountUp(yourScoreTarget, 1100);

  if (!questionResults) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 p-6">
        <Spinner className="text-accent-text size-8" />
        <p className="text-body text-secondary">Loading results…</p>
      </div>
    );
  }

  const { answerReveal, yourPoints, yourScore, yourRank, playerCount } = questionResults;
  const answeredLabel = describeAnswer(
    lastAnswerData,
    currentQuestion?.type ?? '',
    currentQuestion?.config.options,
    currentQuestion?.config.unit,
  );
  // Both payloads carry the same question's map; merge in case one arrived without it.
  const urls: ImageUrls = { ...currentQuestion?.imageUrls, ...questionResults.imageUrls };
  const answeredImages = chosenImages(lastAnswerData, currentQuestion?.config, urls);
  const isCompleteness = answerReveal.type === 'completeness';
  const isFitb = answerReveal.type === 'fill_in_the_blank';
  // closest_guess (T7): full <=> points == pointsValue, partial <=> 0 < points < pointsValue
  // (the server truncates partial credit strictly below full, design §3.2).
  // A player who joined while the question was locked never got new_question, so
  // currentQuestion (and the full points) can be unknown here: fall back to the plain rule.
  const fullPoints = currentQuestion?.pointsValue;
  const isGuess = answerReveal.type === 'closest_guess' && fullPoints !== undefined;
  const isPartial = isGuess && yourPoints > 0 && yourPoints < fullPoints;
  const isCorrect = !isCompleteness && (isGuess ? yourPoints >= fullPoints : yourPoints > 0);
  const guessValue = typeof lastAnswerData?.value === 'number' ? lastAnswerData.value : null;
  // lastAnswerData is reset on every new_question, so null here means no answer was sent.
  const noAnswer = !lastAnswerData;

  const outcome: Outcome = isCompleteness
    ? 'recorded'
    : isCorrect
      ? 'correct'
      : isPartial
        ? 'partial'
        : noAnswer
          ? 'none'
          : 'incorrect';
  const look = OUTCOME[outcome];
  const Icon = look.icon;

  const fitbAccepted: string[] =
    isFitb && 'acceptedAnswers' in answerReveal ? answerReveal.acceptedAnswers : [];

  const hasReveal =
    Boolean(answeredLabel)
    || (isFitb && fitbAccepted.length > 0)
    || answerReveal.type === 'pin_drop'
    || answerReveal.type === 'closest_guess';

  // Share of the room at or below this player's rank (rank 1 of 10 = top 10%).
  const topPct = playerCount > 1 ? Math.max(1, Math.round((yourRank / playerCount) * 100)) : null;
  const ahead = Math.max(0, playerCount - yourRank);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-5 px-4 pt-4 pb-[max(1.5rem,env(safe-area-inset-bottom))] text-center">
      <div className="w-full max-w-md flex flex-col gap-4">

        {/* Result hero: big icon badge + headline + points gained */}
        <Card variant="question" tone={look.tone} className="px-5 pt-8 pb-6 flex flex-col items-center gap-3 animate-scale-in">
          <span
            className={cn(
              'flex h-20 w-20 items-center justify-center rounded-pill animate-pop',
              look.ring,
            )}
            aria-hidden="true"
          >
            <Icon className={cn('h-10 w-10', outcome === 'incorrect' && 'animate-shake')} strokeWidth={3} />
          </span>

          {/* Correct / Wrong / Recorded */}
          {isCompleteness ? (
            <p className="text-title text-accent-text">Answer recorded!</p>
          ) : isCorrect ? (
            <p className="text-title text-success">Correct!</p>
          ) : isPartial ? (
            <p className="text-title text-warning">Partial credit</p>
          ) : (
            <p className="text-title text-danger">Incorrect</p>
          )}
          {outcome === 'none' && (
            <Badge tone="neutral" size="md">No answer</Badge>
          )}

          {/* Points for this question */}
          <p className="text-stat tabular-nums text-primary animate-slide-up" style={staggerStyle(2)}>
            <span aria-hidden="true">+{formatPoints(shownPoints)}</span>
            <span className="sr-only">+{formatPoints(yourPoints)}</span>
            <span className="text-heading text-secondary"> pts</span>
          </p>
          {isGuess && fullPoints !== undefined && fullPoints > 0 && (
            <Progress
              value={yourPoints}
              max={fullPoints}
              tone={look.tone}
              size="sm"
              label="Points earned out of the maximum"
              className="max-w-48"
            />
          )}
        </Card>

        {/* What the player answered and the correct answer */}
        {hasReveal && (
          <Card variant="glass" className="px-5 py-4 flex flex-col gap-2 text-left animate-slide-up" style={staggerStyle(3)}>
            {answeredLabel && (
              <p className="text-body text-secondary break-words">
                You answered: <span className="text-primary font-semibold">{answeredLabel}</span>
              </p>
            )}
            {answeredImages}

            {/* Correct answer for FITB ACCURACY */}
            {isFitb && fitbAccepted.length > 0 && (
              <p className="text-body text-secondary break-words">
                Correct: <span className="text-success font-semibold">{fitbAccepted.join(' / ')}</span>
              </p>
            )}

            {/* Correct answer for the T7 types (ACCURACY only; completeness reveals nothing) */}
            {answerReveal.type === 'pin_drop' && (
              <p className="text-body text-secondary">
                Target: <span className="text-success font-semibold tabular-nums">({answerReveal.target.x}, {answerReveal.target.y})</span>
              </p>
            )}
            {answerReveal.type === 'closest_guess' && (
              <p className="text-body text-secondary break-words">
                Correct: <span className="text-success font-semibold tabular-nums">
                  {formatNum(answerReveal.correct)}{answerReveal.unit ? ` ${answerReveal.unit}` : ''}
                </span>
                {guessValue !== null && (
                  <> — off by <span className="tabular-nums">{formatNum(Math.abs(guessValue - answerReveal.correct))}</span></>
                )}
              </p>
            )}
          </Card>
        )}

        {/* Running total and rank */}
        <div className="grid grid-cols-2 gap-3 animate-slide-up" style={staggerStyle(4)}>
          <Card variant="glass-subtle" className="px-4 py-4 flex flex-col items-center gap-1">
            <p className="text-label uppercase text-muted" aria-hidden="true">Total</p>
            <p className="text-heading tabular-nums text-primary">
              <span aria-hidden="true">{formatPoints(shownScore)}</span>
              <span className="sr-only">Total: {formatPoints(yourScore)} pts</span>
            </p>
            <p className="text-caption text-muted" aria-hidden="true">pts</p>
          </Card>
          <Card variant="glass-subtle" className="px-4 py-4 flex flex-col items-center gap-1">
            <p className="text-label uppercase text-muted">Rank</p>
            <p className="text-heading tabular-nums text-primary">
              #{yourRank}
            </p>
            <p className="text-caption text-muted tabular-nums">of {playerCount}</p>
          </Card>
        </div>

        {topPct !== null && (
          <div className="flex flex-wrap items-center justify-center gap-2 animate-fade-in" style={staggerStyle(5)}>
            {yourRank === 1 ? (
              <Badge tone="warning" size="md">
                <Trophy className="h-4 w-4" aria-hidden="true" />
                In the lead
              </Badge>
            ) : (
              <Badge tone="accent" size="md" className="tabular-nums">Top {topPct}%</Badge>
            )}
            {ahead > 0 && (
              <Badge tone="neutral" size="md" className="tabular-nums">
                Ahead of {ahead} {ahead === 1 ? 'player' : 'players'}
              </Badge>
            )}
          </div>
        )}
      </div>

      <p className="text-caption text-muted flex items-center gap-2">
        <span className="h-2 w-2 rounded-pill bg-accent animate-pulse-soft" aria-hidden="true" />
        Waiting for next question…
      </p>
    </div>
  );
}
