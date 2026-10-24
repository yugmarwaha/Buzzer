import type { AnswerReveal, QuestionType } from '../types/game';
import { formatNum } from '../lib/utils';
import { Check } from 'lucide-react';
import { PinGrid, type GridPoint } from './PinGrid';
import { Badge } from './ui/badge';
import { BandBar } from './charts';

// Results summary for the T7 types (pin_drop, closest_guess), shared by the results screen
// (with the grid) and the game-over cards (text only). Design §4 and §5.6.

export function isNewType(type: QuestionType | string | undefined): boolean {
  return type === 'pin_drop' || type === 'closest_guess';
}

/** The `"x,y"` keys by count, ties broken by x then y ascending. */
export function topPins(distribution: Record<string, number>, n = 5): [GridPoint, number][] {
  return Object.entries(distribution)
    .map(([key, count]): [GridPoint, number] => {
      const [x, y] = key.split(',').map(Number);
      return [{ x, y }, count];
    })
    .sort(([a, ca], [b, cb]) => cb - ca || a.x - b.x || a.y - b.y)
    .slice(0, n);
}

interface Props {
  type: QuestionType;
  gradingType: 'ACCURACY' | 'COMPLETENESS';
  reveal: AnswerReveal;
  distribution: Record<string, number>;
  totalAnswered: number;
  totalPlayers: number;
  /** Show the Pin Drop grid (results screen) or text only (game-over card). */
  showGrid: boolean;
  /** T8: Pin Drop background image for the grid. */
  imageUrl?: string;
}

export function NewTypeResults({ type, gradingType, reveal, distribution, totalAnswered, totalPlayers, showGrid, imageUrl }: Props) {
  const accuracy = gradingType === 'ACCURACY';
  const answered = `${totalAnswered} of ${totalPlayers} answered`;
  // showGrid marks the projector results screen: larger type. Game-over cards are smaller.
  const big = showGrid;

  if (type === 'pin_drop') {
    const target = accuracy && 'type' in reveal && reveal.type === 'pin_drop' ? reveal.target : null;
    const correct = target ? distribution[`${target.x},${target.y}`] ?? 0 : 0;
    const pins = topPins(distribution);
    return (
      <div className={`w-full flex flex-col ${big ? 'items-center gap-5 md:flex-row md:justify-center md:gap-8' : 'items-start gap-3'}`}>
        {showGrid && (
          <div className="glass rounded-2xl p-4 w-full max-w-md animate-scale-in">
            <PinGrid maxSize={420} target={target} imageUrl={imageUrl} />
          </div>
        )}
        <div className={`flex flex-col gap-4 ${big ? 'items-center md:items-start' : 'items-start'}`}>
          <p className={`text-primary ${big ? 'text-2xl' : 'text-lg'}`}>
            {target ? (
              <>
                Target <span className="font-bold text-success tabular-nums">({target.x}, {target.y})</span>
                {' · '}
                <span className="tabular-nums">{correct} of {totalAnswered} correct</span>
              </>
            ) : (
              answered
            )}
          </p>
          {pins.length > 0 && (
            <ul aria-label="Most chosen points" className="flex flex-wrap gap-2 stagger">
              {pins.map(([p, count]) => {
                const isTarget = !!target && p.x === target.x && p.y === target.y;
                return (
                  <li key={`${p.x},${p.y}`}>
                    <Badge
                      tone={isTarget ? 'success' : 'neutral'}
                      size={big ? 'lg' : 'md'}
                      className="tabular-nums animate-pop"
                    >
                      ({p.x}, {p.y}): {count}
                      {isTarget && (
                        <>
                          <Check className={big ? 'size-5' : 'size-4'} strokeWidth={3} aria-hidden="true" />
                          <span className="sr-only">target</span>
                        </>
                      )}
                    </Badge>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    );
  }

  // closest_guess
  if (!accuracy || !('type' in reveal) || reveal.type !== 'closest_guess') {
    return <p className={`text-primary tabular-nums ${big ? 'text-2xl' : 'text-lg'}`}>{answered}</p>;
  }
  const unit = reveal.unit ? ` ${reveal.unit}` : '';
  const full = distribution['full'] ?? 0;
  const partial = distribution['partial'] ?? 0;
  const miss = distribution['miss'] ?? 0;
  return (
    <div className={`w-full flex flex-col gap-4 ${big ? 'glass rounded-2xl p-6 md:p-8 items-center animate-slide-up' : ''}`}>
      <div className={`flex flex-col gap-1 ${big ? 'items-center text-center' : ''}`}>
        <p className={`text-primary ${big ? 'text-heading' : 'text-lg'}`}>
          Correct: <span className="font-bold text-success tabular-nums">{formatNum(reveal.correct)}{unit}</span>
        </p>
        <p className={`text-secondary tabular-nums ${big ? 'text-xl' : 'text-base'}`}>
          Full within ±{formatNum(reveal.fullTolerance)} · partial within ±{formatNum(reveal.partialTolerance)}
        </p>
      </div>
      <BandBar
        full={full}
        partial={partial}
        miss={miss}
        height={big ? 36 : 24}
        className="w-full"
      />
      <p className={`text-secondary tabular-nums ${big ? 'text-xl' : 'text-base'}`}>{answered}</p>
    </div>
  );
}
