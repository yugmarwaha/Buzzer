import { cn } from '../../lib/utils';

export interface ProgressProps {
  value: number;
  max?: number;
  tone?: 'accent' | 'success' | 'warning' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  /** Accessible name, for example "Answers received". */
  label: string;
  className?: string;
}

const TONE = { accent: 'bg-accent', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' };
const SIZE = { sm: 'h-1.5', md: 'h-2.5', lg: 'h-4' };

/** A horizontal meter on the shared `track` color. Width changes ease; reduced motion jumps. */
export function Progress({ value, max = 100, tone = 'accent', size = 'md', label, className }: ProgressProps) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={cn('w-full overflow-hidden rounded-pill bg-track', SIZE[size], className)}
    >
      <div className={cn('h-full rounded-pill transition-[width] duration-slow ease-smooth', TONE[tone])} style={{ width: `${pct}%` }} />
    </div>
  );
}
