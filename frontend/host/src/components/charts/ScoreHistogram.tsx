import { useMemo, useState } from 'react';
import { ChartStyles, ChartTooltip, MARK, SrTable, barPath, fmt, niceTicks, stagger, tok, useWidth } from './shared';

// Histogram of final player scores (GameOverPage). Bins follow the page's old bucket rule
// (about 8 equal-width bins from 0 to maxPossibleScore), so the axis reads in whole points.

export interface ScoreBin {
  label: string;
  start: number;
  end: number;
  count: number;
}

const TARGET_BINS = 8;

function chooseWidth(ceiling: number): number {
  const n = ceiling + 1;
  let best = 1;
  let bestDiff = Infinity;
  for (let w = 1; w * w <= n; w++) {
    if (n % w !== 0) continue;
    for (const cand of [w, n / w]) {
      const bins = n / cand;
      if (bins < 2) continue;
      const diff = Math.abs(bins - TARGET_BINS);
      if (diff < bestDiff) { bestDiff = diff; best = cand; }
    }
  }
  if (bestDiff > TARGET_BINS) best = Math.max(1, Math.ceil(n / TARGET_BINS));
  return best;
}

/** Equal-width score bins from 0 to maxPossibleScore (inclusive). */
export function buildScoreBins(scores: number[], maxPossibleScore: number): ScoreBin[] {
  const ceiling = Math.max(maxPossibleScore, 1, ...scores);
  const width = chooseWidth(ceiling);
  const count = Math.ceil((ceiling + 1) / width);
  const bins: ScoreBin[] = Array.from({ length: count }, (_, i) => {
    const start = i * width;
    const end = Math.min(start + width - 1, ceiling);
    return { label: start === end ? fmt(start) : `${fmt(start)}–${fmt(end)}`, start, end, count: 0 };
  });
  for (const s of scores) bins[Math.min(Math.max(0, Math.floor(s / width)), count - 1)].count++;
  return bins;
}

export interface ScoreHistogramProps {
  /** Final score of every player. */
  scores: number[];
  /** Upper end of the x axis. */
  maxPossibleScore: number;
  /** Plot height in px (axes excluded). Default 260. */
  height?: number;
  /** Accessible title; also the table caption. */
  title?: string;
  className?: string;
}

const AXIS_FONT = 18;
const VALUE_FONT = 20;
const LEFT = 44;
const RIGHT = 12;
const TOP = 64; // room for the average / high-score marker labels (two rows)
const BOTTOM = 40;
const GAP = 2;
const MAX_BAR = 72;

export function ScoreHistogram({ scores, maxPossibleScore, height = 260, title = 'Score distribution', className }: ScoreHistogramProps) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);

  const bins = useMemo(() => buildScoreBins(scores, maxPossibleScore), [scores, maxPossibleScore]);
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const high = scores.length ? Math.max(...scores) : null;
  const maxCount = Math.max(1, ...bins.map((b) => b.count));
  const ticks = niceTicks(maxCount, 4);
  const yMax = ticks[ticks.length - 1];

  const plotW = Math.max(120, width - LEFT - RIGHT);
  const slot = plotW / bins.length;
  const barW = Math.min(MAX_BAR, Math.max(4, slot - GAP * 4));
  const y = (v: number) => TOP + height - (v / yMax) * height;
  // A score sits where its bar sits: bin width W covers whole scores [iW, iW+W-1], so the
  // centre of a width-1 bar is at slot s + 0.5, and in general at (s + 0.5) / W slots.
  const binWidth = bins[0].end - bins[0].start + 1;
  const xOfScore = (s: number) =>
    LEFT + Math.min(Math.max((s + 0.5) / binWidth, 0), bins.length) * slot;

  // Thin the x labels so each one has about 72px.
  const labelEvery = Math.max(1, Math.ceil(72 / slot));
  const svgH = TOP + height + BOTTOM;

  const markers: { key: string; label: string; value: number; color: string; row: number }[] = [];
  const sameSpot = avg !== null && high !== null && Math.abs(xOfScore(avg) - xOfScore(high)) < 10;
  if (sameSpot && avg !== null && high !== null) {
    // Average and high score are the same spot: one line, one label, so they never overprint.
    markers.push({ key: 'both', label: avg === high ? `Avg & High ${fmt(high)}` : `Avg ${fmt(avg)} · High ${fmt(high)}`, value: high, color: 'line-strong', row: 0 });
  } else {
    if (avg !== null) markers.push({ key: 'avg', label: `Avg ${fmt(avg)}`, value: avg, color: 'line-strong', row: 0 });
    if (high !== null) markers.push({ key: 'high', label: `High ${fmt(high)}`, value: high, color: 'secondary', row: 1 });
  }

  const activeBin = active !== null ? bins[active] : null;

  return (
    <figure className={className} aria-label={title}>
      <ChartStyles />
      <div ref={wrapRef} className="relative w-full" onPointerLeave={() => setActive(null)}>
        <svg width={width} height={svgH} role="group" aria-label={`${title}, ${scores.length} ${scores.length === 1 ? 'player' : 'players'}`} className="block overflow-visible">
          {/* Gridlines and y ticks */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={LEFT + plotW} y1={y(t)} y2={y(t)} strokeWidth={1} style={{ stroke: tok(t === 0 ? 'line-strong' : 'line') }} />
              <text x={LEFT - 10} y={y(t)} dy="0.35em" textAnchor="end" fontSize={AXIS_FONT} style={{ fill: tok('muted'), fontVariantNumeric: 'tabular-nums' }}>
                {t}
              </text>
            </g>
          ))}

          {/* Average and high-score markers: drawn behind the bars as thin dashed lines that start
              below the labels, so a line never crosses the label text or a bar. */}
          {markers.map((m) => {
            const mx = xOfScore(m.value);
            const ly = 18 + m.row * 26;
            const anchor = mx < LEFT + 60 ? 'start' : mx > LEFT + plotW - 60 ? 'end' : 'middle';
            return (
              <g key={m.key} aria-hidden="true" className="bz-fade" style={stagger(bins.length)}>
                <line x1={mx} x2={mx} y1={TOP - 6} y2={TOP + height} strokeWidth={1.5} strokeDasharray="5 5" strokeLinecap="round" style={{ stroke: tok(m.color) }} />
                <text
                  x={mx}
                  y={ly}
                  textAnchor={anchor}
                  fontSize={AXIS_FONT}
                  fontWeight={600}
                  strokeWidth={5}
                  strokeLinejoin="round"
                  style={{ fill: tok(m.key === 'avg' ? 'secondary' : 'primary'), stroke: tok('surface'), paintOrder: 'stroke' }}
                >
                  {m.label}
                </text>
              </g>
            );
          })}

          {/* Bars */}
          {bins.map((b, i) => {
            const cx = LEFT + slot * i + slot / 2;
            const x = cx - barW / 2;
            const isActive = active === i;
            const tip = `${b.count} ${b.count === 1 ? 'player' : 'players'} scored ${b.label}`;
            return (
              <g
                key={b.label}
                className="bz-hit"
                tabIndex={0}
                role="img"
                aria-label={tip}
                onPointerEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
              >
                {/* Hit area: the whole slot, taller than the bar */}
                <rect x={LEFT + slot * i} y={TOP} width={slot} height={height} style={{ fill: 'transparent' }} />
                {b.count === 0 ? (
                  // Empty bin: a baseline stub and a muted "0", so "nobody here" is visible
                  // without looking like a tall bar.
                  <>
                    <rect x={x} y={y(0) - 4} width={barW} height={4} rx={2} style={{ fill: tok(MARK.neutral) }} />
                    <text x={cx} y={y(0) - 12} textAnchor="middle" fontSize={AXIS_FONT} style={{ fill: tok('muted') }}>
                      0
                    </text>
                  </>
                ) : (
                  <path
                    d={barPath(x, y(b.count), barW, y(0) - y(b.count), 4, 'up')}
                    className="bz-mark bz-grow-y"
                    style={{ fill: tok(MARK.primary), opacity: active !== null && !isActive ? 0.55 : 1, ...stagger(i) }}
                  />
                )}
                {/* Focus ring */}
                <rect
                  className="bz-focus"
                  x={x - 4}
                  y={TOP - 4}
                  width={barW + 8}
                  height={height + 8}
                  rx={8}
                  strokeWidth={2}
                  style={{ fill: 'none', stroke: tok('ring') }}
                />
                {/* Value label at the bar tip */}
                {b.count > 0 && (
                  <text
                    x={cx}
                    y={y(b.count) - 8}
                    textAnchor="middle"
                    fontSize={VALUE_FONT}
                    fontWeight={700}
                    className="bz-fade"
                    style={{ fill: tok('primary'), ...stagger(i) }}
                  >
                    {b.count}
                  </text>
                )}
                {i % labelEvery === 0 && (
                  <text x={cx} y={TOP + height + 28} textAnchor="middle" fontSize={AXIS_FONT} style={{ fill: tok('muted'), fontVariantNumeric: 'tabular-nums' }}>
                    {b.label}
                  </text>
                )}
              </g>
            );
          })}

        </svg>

        {activeBin && active !== null && (
          <ChartTooltip x={LEFT + slot * active + slot / 2} y={activeBin.count > 0 ? y(activeBin.count) - 30 : y(0) - 10}>
            <div className="text-xl font-bold text-primary">
              {activeBin.count} {activeBin.count === 1 ? 'player' : 'players'}
            </div>
            <div className="text-sm text-secondary">{activeBin.label} pts</div>
          </ChartTooltip>
        )}
      </div>
      <figcaption className="mt-1 text-center text-lg text-muted">Score (points)</figcaption>
      <SrTable
        caption={`${title}. Average ${avg !== null ? fmt(avg) : 'none'}, high score ${high !== null ? fmt(high) : 'none'}.`}
        headers={['Score range', 'Players']}
        rows={bins.map((b) => [b.label, b.count])}
      />
    </figure>
  );
}
