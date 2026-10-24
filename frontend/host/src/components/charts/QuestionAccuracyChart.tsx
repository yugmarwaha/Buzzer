import { useState } from 'react';
import { ChartStyles, ChartTooltip, MARK, SrTable, Swatch, barPath, stagger, tok, useWidth } from './shared';

// One column per question: percent correct (ACCURACY) or percent answered (COMPLETENESS),
// so the host sees at a glance which questions were hard. Hover shows a tooltip; click,
// Enter or Space pins the details panel under the chart.

export interface QuestionAccuracyDatum {
  /** Stable key, e.g. questionId. */
  id: string | number;
  /** 1-based question number, shown as "Q1". */
  number: number;
  prompt: string;
  kind: 'accuracy' | 'participation';
  /** Players who answered. */
  answered: number;
  /** Players in the game. */
  total: number;
  /** Correct answers (accuracy questions only). */
  correct?: number;
}

export interface QuestionAccuracyChartProps {
  questions: QuestionAccuracyDatum[];
  /** Plot height in px. Default 200. */
  height?: number;
  /** Draws a labelled reference line here (percent). Default 50; null hides it. */
  threshold?: number | null;
  title?: string;
  className?: string;
}

/** Percent shown for one question: correct / answered, or answered / total. */
export function questionPercent(q: QuestionAccuracyDatum): number {
  if (q.kind === 'accuracy') return q.answered > 0 ? Math.round(((q.correct ?? 0) / q.answered) * 100) : 0;
  return q.total > 0 ? Math.round((q.answered / q.total) * 100) : 0;
}

const AXIS_FONT = 16;
const LEFT = 52;
const RIGHT = 8;
const TOP = 30;
const BOTTOM = 34;
const MAX_BAR = 24;

export function QuestionAccuracyChart({ questions, height = 200, threshold = 50, title = 'Accuracy by question', className }: QuestionAccuracyChartProps) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);

  if (questions.length === 0) {
    return <p className={`text-center text-lg text-muted ${className ?? ''}`}>No questions to show</p>;
  }

  const plotW = Math.max(120, width - LEFT - RIGHT);
  const slot = plotW / questions.length;
  const barW = Math.min(MAX_BAR, Math.max(4, slot - 6));
  const y = (v: number) => TOP + height - (v / 100) * height;
  const labelEvery = Math.max(1, Math.ceil(40 / slot));
  const kinds = new Set(questions.map((q) => q.kind));
  const showLegend = kinds.size > 1;
  const colorOf = (q: QuestionAccuracyDatum) => (q.kind === 'accuracy' ? MARK.primary : MARK.participation);
  const shown = hover ?? pinned;
  const detail = pinned !== null ? questions[pinned] : null;

  return (
    <figure className={className} aria-label={title}>
      <ChartStyles />
      {showLegend && (
        <div className="mb-2 flex flex-wrap gap-4 text-base text-secondary">
          <span className="inline-flex items-center gap-2"><Swatch color={MARK.primary} />Percent correct</span>
          <span className="inline-flex items-center gap-2"><Swatch color={MARK.participation} />Percent answered (participation)</span>
        </div>
      )}
      <div ref={wrapRef} className="relative w-full" onPointerLeave={() => setHover(null)}>
        <svg width={width} height={TOP + height + BOTTOM} role="group" aria-label={title} className="block overflow-visible">
          {[0, 50, 100].map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={LEFT + plotW} y1={y(t)} y2={y(t)} strokeWidth={1} style={{ stroke: tok(t === 0 ? 'line-strong' : 'line') }} />
              <text x={LEFT - 10} y={y(t)} dy="0.35em" textAnchor="end" fontSize={AXIS_FONT} style={{ fill: tok('muted') }}>
                {t}%
              </text>
            </g>
          ))}
          {threshold !== null && threshold !== 50 && threshold > 0 && threshold < 100 && (
            <line x1={LEFT} x2={LEFT + plotW} y1={y(threshold)} y2={y(threshold)} strokeWidth={1} style={{ stroke: tok('line-strong') }} />
          )}

          {questions.map((q, i) => {
            const v = questionPercent(q);
            const cx = LEFT + slot * i + slot / 2;
            const x = cx - barW / 2;
            const low = threshold !== null && q.kind === 'accuracy' && q.answered > 0 && v < threshold;
            const dim = shown !== null && shown !== i;
            const label = `Question ${q.number}: ${v} percent ${q.kind === 'accuracy' ? 'correct' : 'answered'}${low ? ', below the threshold' : ''}`;
            const toggle = () => setPinned((p) => (p === i ? null : i));
            return (
              <g
                key={q.id}
                className="bz-hit"
                tabIndex={0}
                role="button"
                aria-label={label}
                aria-pressed={pinned === i}
                style={{ cursor: 'pointer' }}
                onPointerEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                onClick={toggle}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
                }}
              >
                <rect x={LEFT + slot * i} y={TOP - 20} width={slot} height={height + 20 + BOTTOM} style={{ fill: 'transparent' }} />
                <rect x={x} y={TOP} width={barW} height={height} rx={4} style={{ fill: tok('track') }} />
                {v > 0 ? (
                  <path
                    d={barPath(x, y(v), barW, y(0) - y(v), 4, 'up')}
                    className="bz-mark bz-grow-y"
                    style={{ fill: tok(colorOf(q)), opacity: dim ? 0.5 : 1, ...stagger(i, 40) }}
                  />
                ) : (
                  <rect x={x} y={y(0) - 3} width={barW} height={3} rx={1.5} style={{ fill: tok(MARK.neutral) }} />
                )}
                {pinned === i && (
                  <rect x={x - 4} y={TOP - 4} width={barW + 8} height={height + 8} rx={8} strokeWidth={2} style={{ fill: 'none', stroke: tok('line-strong') }} />
                )}
                <rect className="bz-focus" x={x - 4} y={TOP - 4} width={barW + 8} height={height + 8} rx={8} strokeWidth={2} style={{ fill: 'none', stroke: tok('ring') }} />
                {low && (
                  <text x={cx} y={TOP - 8} textAnchor="middle" fontSize={AXIS_FONT} fontWeight={700} style={{ fill: tok('primary') }}>
                    !
                  </text>
                )}
                {(i % labelEvery === 0 || i === questions.length - 1) && (
                  <text x={cx} y={TOP + height + 24} textAnchor="middle" fontSize={AXIS_FONT} fontWeight={pinned === i ? 700 : 400} style={{ fill: tok(pinned === i ? 'primary' : 'muted') }}>
                    Q{q.number}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        {hover !== null && (
          <ChartTooltip x={LEFT + slot * hover + slot / 2} y={y(questionPercent(questions[hover])) - 6}>
            <div className="text-lg font-bold text-primary">
              {questionPercent(questions[hover])}% {questions[hover].kind === 'accuracy' ? 'correct' : 'answered'}
            </div>
            <div className="text-sm text-secondary">Q{questions[hover].number} · {questions[hover].answered}/{questions[hover].total} answered</div>
          </ChartTooltip>
        )}
      </div>

      <div aria-live="polite" className="min-h-[1.5rem]">
        {detail ? (
          <div className="mt-2 rounded-lg border border-line bg-raised px-4 py-3">
            <p className="text-base font-semibold text-primary">
              Q{detail.number}. {detail.prompt}
            </p>
            <p className="mt-1 text-base text-secondary" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {detail.kind === 'accuracy'
                ? `${detail.correct ?? 0} correct of ${detail.answered} answered (${questionPercent(detail)}%) · ${detail.answered}/${detail.total} players answered`
                : `${detail.answered} of ${detail.total} players answered (${questionPercent(detail)}%) · participation`}
            </p>
          </div>
        ) : (
          <p className="mt-1 text-sm text-muted">Select a column to see the question.{threshold !== null ? ` "!" marks accuracy below ${threshold}%.` : ''}</p>
        )}
      </div>

      <SrTable
        caption={title}
        headers={['Question', 'Kind', 'Percent', 'Answered', 'Players']}
        rows={questions.map((q) => [`Q${q.number}`, q.kind, `${questionPercent(q)}%`, q.answered, q.total])}
      />
    </figure>
  );
}
