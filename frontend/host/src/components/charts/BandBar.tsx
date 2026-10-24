import { useState } from 'react';
import { ChartStyles, ChartTooltip, MARK, Swatch, pct, stagger, tok, useWidth } from './shared';

// One stacked bar for closest_guess results: full / partial / miss, plus an optional
// "no answer" segment. Counts live in the legend (always visible) and in the tooltip, so
// no number has to fit inside a narrow segment.

export interface BandBarProps {
  full: number;
  partial: number;
  miss: number;
  /** Players who did not answer; drawn as a muted segment when > 0. */
  unanswered?: number;
  /** Bar thickness in px. Default 24. */
  height?: number;
  title?: string;
  className?: string;
}

interface Segment { key: string; label: string; count: number; color: string }

const GAP = 2;

export function BandBar({ full, partial, miss, unanswered = 0, height = 24, title = 'Closest guess results', className }: BandBarProps) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<string | null>(null);

  const segments: Segment[] = [
    { key: 'full', label: 'Full', count: full, color: MARK.correct },
    { key: 'partial', label: 'Partial', count: partial, color: MARK.partial },
    { key: 'miss', label: 'Miss', count: miss, color: MARK.wrong },
  ];
  if (unanswered > 0) segments.push({ key: 'none', label: 'No answer', count: unanswered, color: MARK.neutral });
  const total = segments.reduce((a, s) => a + s.count, 0);
  const visible = segments.filter((s) => s.count > 0);
  const usable = Math.max(0, width - GAP * Math.max(0, visible.length - 1));

  let cursor = 0;
  const placed = visible.map((s) => {
    const w = total > 0 ? (s.count / total) * usable : 0;
    const out = { ...s, x: cursor, w };
    cursor += w + GAP;
    return out;
  });
  const act = placed.find((s) => s.key === active) ?? null;

  return (
    <figure className={className} aria-label={title}>
      <ChartStyles />
      <div ref={wrapRef} className="relative w-full" onPointerLeave={() => setActive(null)}>
        {total === 0 ? (
          <div className="flex items-center justify-center rounded-md bg-track text-base text-muted" style={{ height: Math.max(height, 32) }}>
            No answers yet
          </div>
        ) : (
          <svg width={width} height={height} role="group" aria-label={title} className="block overflow-visible">
            {placed.map((s, i) => {
              const first = i === 0;
              const last = i === placed.length - 1;
              return (
                <g
                  key={s.key}
                  className="bz-hit"
                  tabIndex={0}
                  role="img"
                  aria-label={`${s.label}: ${s.count} (${pct(s.count, total)} percent)`}
                  onPointerEnter={() => setActive(s.key)}
                  onFocus={() => setActive(s.key)}
                  onBlur={() => setActive(null)}
                >
                  <rect
                    x={s.x}
                    y={0}
                    width={Math.max(1, s.w)}
                    height={height}
                    rx={first || last ? 4 : 0}
                    className="bz-mark bz-grow-x"
                    style={{
                      fill: tok(s.color),
                      opacity: active && active !== s.key ? 0.55 : 1,
                      transformBox: 'fill-box',
                      transformOrigin: '0 50%',
                      ...stagger(i, 120),
                    }}
                  />
                  <rect className="bz-focus" x={s.x - 3} y={-3} width={s.w + 6} height={height + 6} rx={6} strokeWidth={2} style={{ fill: 'none', stroke: tok('ring') }} />
                </g>
              );
            })}
          </svg>
        )}
        {act && (
          <ChartTooltip x={act.x + act.w / 2} y={-2}>
            <div className="text-lg font-bold text-primary">{act.count} · {pct(act.count, total)}%</div>
            <div className="text-sm text-secondary">{act.label}</div>
          </ChartTooltip>
        )}
      </div>
      <figcaption className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-lg text-secondary" style={{ fontVariantNumeric: 'tabular-nums' }}>
        {segments.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-2">
            <Swatch color={s.color} />
            <span>{s.label}</span>
            <span className="font-bold text-primary">{s.count}</span>
            <span className="text-muted">({pct(s.count, total)}%)</span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
