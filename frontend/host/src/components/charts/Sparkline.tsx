import { useState } from 'react';
import { ChartStyles, ChartTooltip, MARK, SrTable, fmt, tok, useWidth } from './shared';

// Small trend line, e.g. average score after each question or players per finished session.
// 2px line, 10% area wash, end dot with its value; hover or arrow keys move a crosshair.

export interface SparklineProps {
  values: number[];
  /** Label for each point (tooltip and table), e.g. "Q3". Default "#1", "#2", ... */
  labels?: string[];
  /** What the values are, e.g. "Average score". */
  title: string;
  /** Height in px. Default 64. */
  height?: number;
  className?: string;
}

const PAD = 6;
const END_LABEL = 64;

export function Sparkline({ values, labels, title, height = 64, className }: SparklineProps) {
  const [wrapRef, width] = useWidth<HTMLDivElement>(240);
  const [active, setActive] = useState<number | null>(null);

  if (values.length === 0) return <p className={`text-base text-muted ${className ?? ''}`}>No data</p>;

  const name = (i: number) => labels?.[i] ?? `#${i + 1}`;
  const lo = Math.min(0, ...values);
  const hi = Math.max(...values, lo + 1);
  const plotW = Math.max(40, width - PAD - END_LABEL);
  const x = (i: number) => PAD + (values.length === 1 ? plotW / 2 : (i / (values.length - 1)) * plotW);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (height - PAD * 2);
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join('');
  const area = `${line}L${x(values.length - 1)},${y(lo)}L${x(0)},${y(lo)}Z`;
  const last = values.length - 1;

  function nearest(clientX: number, el: SVGSVGElement) {
    const px = clientX - el.getBoundingClientRect().left;
    const step = values.length > 1 ? plotW / (values.length - 1) : 1;
    setActive(Math.min(last, Math.max(0, Math.round((px - PAD) / step))));
  }

  return (
    <figure className={className} aria-label={title}>
      <ChartStyles />
      <div ref={wrapRef} className="relative w-full">
        <svg
          width={width}
          height={height}
          tabIndex={0}
          role="img"
          aria-label={`${title}: ${values.length} points, latest ${fmt(values[last])}. Use arrow keys to read each point.`}
          className="block overflow-visible rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onPointerMove={(e) => nearest(e.clientX, e.currentTarget)}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(last)}
          onBlur={() => setActive(null)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') { e.preventDefault(); setActive((a) => Math.max(0, (a ?? last) - 1)); }
            if (e.key === 'ArrowRight') { e.preventDefault(); setActive((a) => Math.min(last, (a ?? last) + 1)); }
          }}
        >
          <path d={area} className="bz-fade" style={{ fill: tok(MARK.primary, 0.1) }} />
          <path d={line} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className="bz-fade" style={{ fill: 'none', stroke: tok(MARK.primary) }} />
          {active !== null && (
            <line x1={x(active)} x2={x(active)} y1={0} y2={height} strokeWidth={1} style={{ stroke: tok('line-strong') }} />
          )}
          {[active ?? last].map((i) => (
            <circle key={i} cx={x(i)} cy={y(values[i])} r={5} strokeWidth={2} style={{ fill: tok(MARK.primary), stroke: tok('surface') }} />
          ))}
          <text x={x(last) + 10} y={y(values[last])} dy="0.35em" fontSize={18} fontWeight={700} style={{ fill: tok('primary') }}>
            {fmt(values[last])}
          </text>
        </svg>
        {active !== null && (
          <ChartTooltip x={x(active)} y={y(values[active]) - 4}>
            <div className="text-base font-bold text-primary">{fmt(values[active])}</div>
            <div className="text-sm text-secondary">{name(active)}</div>
          </ChartTooltip>
        )}
      </div>
      <SrTable caption={title} headers={['Point', 'Value']} rows={values.map((v, i) => [name(i), fmt(v)])} />
    </figure>
  );
}
