import type { CSSProperties } from 'react';
import { Check, Circle, Minus, X } from 'lucide-react';
import type { ImageUrls, PlayerAnswer, PlayerAnswerReveal, QuestionConfig, QuestionSummaryItem } from '../types/game';
import { cn, formatNum, optionAnswerLabel, optionDisplay } from '../lib/utils';
import { QuestionImage } from './QuestionImage';
import { Badge } from './ui/badge';
import { Card, type CardTone } from './ui/card';

/** The fields a recap row needs — shared by the live game-over screen and Past Quizzes. */
export type RecapItem = Pick<
  QuestionSummaryItem,
  'prompt' | 'type' | 'config' | 'pointsAwarded' | 'maxPoints' | 'playerAnswer' | 'answerReveal'
>;

function describePlayerAnswer(
  playerAnswer: PlayerAnswer | null,
  type: string,
  options: string[] | undefined,
  unit?: string,
): string {
  if (!playerAnswer) return 'No answer';
  if (type === 'multiple_choice') {
    const idx = playerAnswer.selectedIndex;
    if (typeof idx === 'number' && options && options[idx] !== undefined) {
      return optionAnswerLabel(options, idx);
    }
    return '?';
  }
  if (type === 'true_false') {
    return playerAnswer.selectedValue ? 'True' : 'False';
  }
  if (type === 'fill_in_the_blank') {
    return typeof playerAnswer.text === 'string' ? playerAnswer.text : '—';
  }
  if (type === 'multi_select') {
    const indices = playerAnswer.selectedIndices;
    if (!indices || indices.length === 0) return 'No selection';
    if (options) return indices.map(i => optionAnswerLabel(options, i)).join(', ');
    return indices.map(i => String.fromCharCode(65 + i)).join(', ');
  }
  if (type === 'pin_drop') {
    const { x, y } = playerAnswer;
    return typeof x === 'number' && typeof y === 'number' ? `(${x}, ${y})` : '—';
  }
  if (type === 'closest_guess') {
    const v = playerAnswer.value;
    return typeof v === 'number' ? `${formatNum(v)}${unit ? ` ${unit}` : ''}` : '—';
  }
  return '—';
}

function describeCorrectAnswer(
  answerReveal: PlayerAnswerReveal,
  options: string[] | undefined,
): string {
  if (answerReveal.type === 'multiple_choice') {
    const indices = answerReveal.correctIndices;
    if (options) {
      return indices.map(i => optionAnswerLabel(options, i)).join(', ');
    }
    return indices.map(i => String.fromCharCode(65 + i)).join(', ');
  }
  if (answerReveal.type === 'true_false') {
    return answerReveal.correctValue ? 'True' : 'False';
  }
  if (answerReveal.type === 'fill_in_the_blank') {
    const label = answerReveal.acceptedAnswers.join(' / ');
    return answerReveal.editDistance > 0 ? `${label} (±${answerReveal.editDistance})` : label;
  }
  if (answerReveal.type === 'multi_select') {
    const correct = answerReveal.answerPoints
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => p > 0)
      .map(({ i }) => options ? optionAnswerLabel(options, i) : String.fromCharCode(65 + i));
    return correct.join(', ');
  }
  if (answerReveal.type === 'pin_drop') {
    return `(${answerReveal.target.x}, ${answerReveal.target.y})`;
  }
  if (answerReveal.type === 'closest_guess') {
    return `${formatNum(answerReveal.correct)}${answerReveal.unit ? ` ${answerReveal.unit}` : ''}`;
  }
  return '';
}

/** Option indices the player chose, and the correct ones (multiple choice, multi-select). */
function chosenIndices(answer: PlayerAnswer | null): number[] {
  if (!answer) return [];
  if (typeof answer.selectedIndex === 'number') return [answer.selectedIndex];
  return answer.selectedIndices ?? [];
}

function correctIndices(reveal: PlayerAnswerReveal): number[] {
  if (reveal.type === 'multiple_choice') return reveal.correctIndices;
  if (reveal.type === 'multi_select') {
    return reveal.answerPoints.flatMap((p, i) => (p > 0 ? [i] : []));
  }
  return [];
}

/** T8: thumbnails of the listed options' images; nothing when none of them has one. */
function OptionThumbs({ indices, config, urls }: { indices: number[]; config: QuestionConfig; urls?: ImageUrls }) {
  const withImage = indices.filter((i) => config.optionImageIds?.[i] != null);
  if (withImage.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {withImage.map((i) => (
        <QuestionImage
          key={i}
          id={config.optionImageIds?.[i]}
          urls={urls}
          alt={`${optionDisplay('', i)} image`}
          className="size-16 min-h-0 p-0 text-xs"
        />
      ))}
    </div>
  );
}

type RowStatus = 'none' | 'recorded' | 'correct' | 'partial' | 'incorrect';

function rowStatus(item: RecapItem): RowStatus {
  if (!item.playerAnswer) return 'none';
  if (item.answerReveal.type === 'completeness') return 'recorded';
  if (item.pointsAwarded <= 0) return 'incorrect';
  return item.pointsAwarded < item.maxPoints ? 'partial' : 'correct';
}

// Full class names spelled out so the Tailwind scan sees them.
const ROW_TONE: Record<Exclude<RowStatus, 'none'>, CardTone> = {
  recorded: 'accent',
  correct: 'success',
  partial: 'warning',
  incorrect: 'danger',
};
const BAR_FILL: Record<RowStatus, string> = {
  none: '',
  recorded: 'bg-accent',
  correct: 'bg-success',
  partial: 'bg-warning',
  incorrect: 'bg-danger',
};

export function QuestionRow({
  item,
  index,
  style,
  urls,
}: {
  item: RecapItem;
  index: number;
  style?: CSSProperties;
  /** T8: the payload's image map (game over `imageUrls`, Past Quizzes `image_urls`). */
  urls?: ImageUrls;
}) {
  const options = item.config.options;
  const isCompleteness = item.answerReveal.type === 'completeness';
  const noAnswer = !item.playerAnswer;
  // Any positive points show as correct (✓), including partial credit. The backend's own
  // `isCorrect` (full points only) is unchanged and still drives the host's correct counts.
  // Below full points, the correct answer is still shown so partial answers aren't hidden.
  const isCorrect = !isCompleteness && !noAnswer && item.pointsAwarded > 0;
  const showCorrectAnswer = !isCompleteness && item.pointsAwarded < item.maxPoints;
  const status = rowStatus(item);

  const playerAnswerLabel = describePlayerAnswer(item.playerAnswer, item.type, options, item.config.unit);
  const correctAnswerLabel = !isCompleteness
    ? describeCorrectAnswer(item.answerReveal, options)
    : '';

  return (
    <Card
      variant={status === 'none' ? 'glass' : 'question'}
      tone={status === 'none' ? undefined : ROW_TONE[status]}
      edge="left"
      className="pl-5 pr-4 py-4 animate-slide-up"
      style={style}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Badge tone="neutral" size="sm" className="tabular-nums">Q{index + 1}</Badge>
          <p className="mt-2 text-body font-medium leading-snug text-primary break-words">{item.prompt}</p>
          <QuestionImage
            id={item.config.promptImageId}
            urls={urls}
            alt="Question image"
            className="mt-2 max-h-40 w-auto max-w-full"
          />
        </div>

        <div className="shrink-0 text-right">
          <p className={cn(
            'text-subheading tabular-nums',
            item.pointsAwarded > 0 ? 'text-success' : 'text-muted',
          )}>
            +{item.pointsAwarded.toLocaleString()}
          </p>
          <p className="text-caption text-muted tabular-nums">
            pts{item.maxPoints > 0 && <span aria-hidden="true"> / {item.maxPoints.toLocaleString()}</span>}
          </p>
        </div>
      </div>

      <div className="mt-3 border-t border-line pt-3">
        <p className="text-caption text-muted mb-1">Your answer</p>
        <div className="flex items-center gap-2">
          {noAnswer ? (
            <Minus className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          ) : isCompleteness ? (
            <Circle className="h-3 w-3 shrink-0 fill-current text-accent-text" aria-hidden="true" />
          ) : isCorrect ? (
            <Check className="h-4 w-4 shrink-0 text-success" strokeWidth={3} aria-hidden="true" />
          ) : (
            <X className="h-4 w-4 shrink-0 text-danger" strokeWidth={3} aria-hidden="true" />
          )}
          {!noAnswer && !isCompleteness && (
            <span className="sr-only">{isCorrect ? 'Correct:' : 'Incorrect:'}</span>
          )}
          <span className={cn(
            'text-body font-semibold break-words min-w-0',
            noAnswer ? 'text-muted' :
            isCompleteness ? 'text-accent-text' :
            isCorrect ? 'text-success' : 'text-danger',
          )}>
            {playerAnswerLabel}
          </span>
        </div>
        <OptionThumbs indices={chosenIndices(item.playerAnswer)} config={item.config} urls={urls} />

        {showCorrectAnswer && correctAnswerLabel && (
          <p className="mt-2 text-caption text-muted break-words">
            Correct: <span className="text-body font-semibold text-success">{correctAnswerLabel}</span>
          </p>
        )}
        {showCorrectAnswer && (
          <OptionThumbs indices={correctIndices(item.answerReveal)} config={item.config} urls={urls} />
        )}
      </div>
    </Card>
  );
}

/** A question an abandoned game never reached: nothing about it is shown. */
export function NotPlayedRow({ index }: { index: number }) {
  return (
    <div className="rounded-2xl border-2 border-dashed border-line-strong px-5 py-4">
      <p className="text-body text-muted">Question {index + 1}: Not played</p>
    </div>
  );
}

/**
 * Per-question bar strip: one column per question, height = points / max points, color by
 * result (success full, warning partial, danger zero, accent completeness, empty track for no
 * answer). Status is never color alone: the recap rows below carry icons and text, and each
 * column has an accessible label and a hover title.
 */
export function RecapStrip({ items }: { items: RecapItem[] }) {
  if (items.length === 0) return null;
  const earned = items.reduce((sum, it) => sum + it.pointsAwarded, 0);
  const possible = items.reduce((sum, it) => sum + it.maxPoints, 0);
  const scored = items.filter(it => it.pointsAwarded > 0).length;
  const showNumbers = items.length <= 12;

  return (
    <figure className="glass rounded-2xl px-4 pt-4 pb-3" aria-label="Points per question">
      <figcaption className="flex items-baseline justify-between gap-3">
        <span className="text-caption text-secondary">Points per question</span>
        <span className="text-caption text-muted tabular-nums">
          {scored} of {items.length} scored
          {possible > 0 && <>, {Math.round((earned / possible) * 100)}% of points</>}
        </span>
      </figcaption>
      <ol className="mt-3 flex h-20 items-end gap-0.5 stagger" role="list">
        {items.map((it, i) => {
          const status = rowStatus(it);
          const pct = it.maxPoints > 0 ? Math.max(0, Math.min(1, it.pointsAwarded / it.maxPoints)) : 0;
          // Completeness answers often carry points too; keep a visible stub for zero-point ones.
          const height = status === 'none' ? 0 : Math.max(pct * 100, status === 'incorrect' ? 8 : 12);
          const label = `Question ${i + 1}: ${it.pointsAwarded.toLocaleString()} of ${it.maxPoints.toLocaleString()} points${status === 'none' ? ', no answer' : ''}`;
          return (
            <li
              key={i}
              className="relative flex h-full min-w-0 flex-1 items-end overflow-hidden rounded-t-sm bg-track"
              title={label}
              aria-label={label}
            >
              {height > 0 && (
                <span
                  className={cn('block w-full rounded-t-sm animate-slide-up', BAR_FILL[status])}
                  style={{ height: `${height}%` }}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
      {showNumbers && (
        <div className="mt-1 flex gap-0.5" aria-hidden="true">
          {items.map((_, i) => (
            <span key={i} className="min-w-0 flex-1 text-center text-caption text-muted tabular-nums">{i + 1}</span>
          ))}
        </div>
      )}
    </figure>
  );
}
