import { ChartStyles, MARK, pct, tok } from './shared';

// Ring meter for one share (response rate, completion, accuracy). The percent sits in the
// center; the label and the "value of total" line sit under the ring in text tokens.

export interface DonutStatProps {
  /** Part, e.g. players who answered. */
  value: number;
  /** Whole, e.g. players in the game. */
  total: number;
  /** Short label under the ring, e.g. "Response rate". */
  label: string;
  /** Ring diameter in px. Default 160. */
  size?: number;
  /** Fill color: 'primary' (default), 'correct' or 'participation'. */
  tone?: 'primary' | 'correct' | 'participation';
  /** Line under the label. Default "{value} of {total}". */
  detail?: string;
  className?: string;
}

export function DonutStat({ value, total, label, size = 160, tone = 'primary', detail, className }: DonutStatProps) {
  const p = pct(value, total);
  const stroke = Math.max(10, Math.round(size * 0.1));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(100, Math.max(0, p)) / 100) * c;
  const color = MARK[tone];

  return (
    <figure className={`inline-flex flex-col items-center gap-2 ${className ?? ''}`}>
      <ChartStyles />
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="block -rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} style={{ fill: 'none', stroke: tok('line') }} />
          {filled > 0 && (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              strokeWidth={stroke}
              strokeLinecap={p >= 100 ? 'butt' : 'round'}
              strokeDasharray={`${filled} ${c}`}
              className="bz-sweep"
              style={{ fill: 'none', stroke: tok(color), strokeDashoffset: 0, ['--bz-sweep-from' as string]: `${filled}` }}
            />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-extrabold text-primary" style={{ fontSize: Math.round(size * 0.26), lineHeight: 1 }}>
            {p}%
          </span>
        </div>
      </div>
      <figcaption className="text-center">
        <span className="block text-lg font-semibold text-secondary">{label}</span>
        <span className="block text-base text-muted" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {detail ?? `${value.toLocaleString()} of ${total.toLocaleString()}`}
        </span>
        <span className="sr-only">{`${label}: ${p} percent`}</span>
      </figcaption>
    </figure>
  );
}
