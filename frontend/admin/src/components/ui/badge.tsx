import { cn } from '../../lib/utils';

// Small label for question type, grading mode, status and counts.
// Every tone pairs a tint fill with its own text color (checked by check-theme at 4.5:1).
export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'outline' | 'solid';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  size?: 'sm' | 'md' | 'lg';
}

const TONE: Record<BadgeTone, string> = {
  neutral: 'border border-line bg-raised text-secondary',
  accent: 'bg-accent-hint text-accent-text',
  success: 'bg-success-tint text-success',
  warning: 'bg-warning-tint text-warning',
  danger: 'bg-danger-tint text-danger',
  outline: 'border border-line-strong text-secondary',
  solid: 'bg-accent text-accent-fg',
};

const SIZE = {
  sm: 'h-6 px-2.5 text-xs gap-1',
  md: 'h-7 px-3 text-sm gap-1.5',
  lg: 'h-9 px-4 text-base gap-2',
};

export function Badge({ className, tone = 'neutral', size = 'sm', children, ...props }: BadgeProps) {
  return (
    <span
      className={cn('inline-flex shrink-0 items-center whitespace-nowrap rounded-pill font-semibold leading-none', TONE[tone], SIZE[size], className)}
      {...props}
    >
      {children}
    </span>
  );
}
