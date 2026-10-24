import type { ReactNode } from 'react';
import { Check } from 'lucide-react';
import { ChartStyles, MARK, pct, stagger, tok } from './shared';

// Horizontal answer distribution for multiple_choice, multi_select and true_false.
// Each row shows the option letter ONCE (as a badge), then the option text, the bar, the count
// and the percent. The correct option gets the success fill plus a check icon and the word
// "Correct", so the state never depends on color alone.

export interface AnswerBarDatum {
  /** Stable key, e.g. the option index or 'true'. */
  key: string;
  /** Badge letter (A, B, ...); null or omitted for True/False rows. */
  letter?: string | null;
  /** Option text, without the letter. */
  label: string;
  count: number;
  /** true = correct, false = wrong, null/undefined = no right answer (participation). */
  correct?: boolean | null;
  /** T8: the option's image, shown between the letter and the text. */
  media?: ReactNode;
}

export interface AnswerBarsProps {
  bars: AnswerBarDatum[];
  /** Denominator for the percent; usually totalAnswered. */
  total: number;
  /** 'lg' for the projector results screen, 'md' for game-over cards. Default 'lg'. */
  size?: 'lg' | 'md';
  /** Fill for wrong options: 'neutral' (default) or 'danger'. */
  wrongTone?: 'neutral' | 'danger';
  /** Shown when total is 0. */
  emptyMessage?: string;
  /** Accessible name for the list. */
  title?: string;
  className?: string;
}

function fillFor(correct: boolean | null | undefined, wrongTone: 'neutral' | 'danger'): string {
  if (correct === true) return MARK.correct;
  if (correct === false) return wrongTone === 'danger' ? MARK.wrong : MARK.neutral;
  return MARK.primary;
}

export function AnswerBars({ bars, total, size = 'lg', wrongTone = 'neutral', emptyMessage = 'No answers yet', title = 'Answer distribution', className }: AnswerBarsProps) {
  const lg = size === 'lg';
  const empty = total <= 0 || bars.every((b) => b.count === 0);
  const hasCorrect = bars.some((b) => b.correct === true);

  return (
    <div className={className}>
      <ChartStyles />
      {empty && (
        <p className={`mb-3 text-center text-muted ${lg ? 'text-xl' : 'text-base'}`} role="status">
          {emptyMessage}
        </p>
      )}
      <ul aria-label={title} className={lg ? 'space-y-5' : 'space-y-3'}>
        {bars.map((b, i) => {
          const p = pct(b.count, total);
          const isCorrect = b.correct === true;
          const sr = `${b.letter ? `${b.letter}. ` : ''}${b.label}: ${b.count} ${b.count === 1 ? 'answer' : 'answers'}, ${p} percent${isCorrect ? ', correct answer' : ''}`;
          return (
            <li key={b.key} className="flex items-center gap-3">
              <span className="sr-only">{sr}</span>
              {b.letter && (
                <span
                  aria-hidden="true"
                  className={`flex shrink-0 items-center justify-center rounded-lg border font-bold font-mono ${
                    isCorrect ? 'border-line-strong text-primary bg-success-tint' : 'border-line text-secondary bg-raised'
                  } ${lg ? 'h-10 w-10 text-xl' : 'h-8 w-8 text-base'}`}
                >
                  {b.letter}
                </span>
              )}
              {b.media && <div className="shrink-0">{b.media}</div>}
              <div className="min-w-0 flex-1" aria-hidden="true">
                <div className="mb-1.5 flex items-baseline justify-between gap-3">
                  <span className={`min-w-0 break-words font-semibold leading-snug text-primary ${lg ? 'text-xl' : 'text-base'}`}>
                    {b.label}
                    {isCorrect && (
                      <span className={`ml-2 inline-flex items-center gap-1 align-middle font-semibold text-success ${lg ? 'text-lg' : 'text-sm'}`}>
                        <Check className={lg ? 'h-5 w-5' : 'h-4 w-4'} strokeWidth={3} aria-hidden="true" />
                        Correct
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 whitespace-nowrap" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    <span className={`font-bold text-primary ${lg ? 'text-2xl' : 'text-lg'}`}>{b.count}</span>
                    <span className={`ml-2 text-muted ${lg ? 'text-lg' : 'text-sm'}`}>{p}%</span>
                  </span>
                </div>
                <div className={`relative w-full overflow-hidden rounded-md bg-track ${lg ? 'h-6' : 'h-4'}`}>
                  {b.count > 0 ? (
                    <div
                      className="bz-grow-x h-full rounded-r-md"
                      style={{
                        width: `${Math.max(p, 1.5)}%`,
                        backgroundColor: tok(fillFor(b.correct, wrongTone)),
                        ...stagger(i, 80),
                      }}
                    />
                  ) : (
                    // Zero: a thin stub at the baseline so the row reads as "0", not "missing".
                    <div className="h-full w-1 rounded-r-sm" style={{ backgroundColor: tok(MARK.neutral) }} />
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      {hasCorrect && <span className="sr-only">The correct answer is marked with a check.</span>}
    </div>
  );
}
