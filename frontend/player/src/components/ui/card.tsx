import { cn } from '../../lib/utils';

// Variants:
// - default: the original card (kept as it was, so existing pages do not change).
// - raised: solid surface with a soft drop shadow.
// - glass / glass-strong / glass-subtle: translucent, blurred surface (theme.css .glass*).
// - interactive: a clickable card that lifts on hover (.lift); add role/tabIndex or wrap a link.
// - question: a strong glass card with a colored edge (tone + edge) for question prompts.
export type CardVariant = 'default' | 'raised' | 'glass' | 'glass-strong' | 'glass-subtle' | 'interactive' | 'question';
export type CardTone = 'accent' | 'success' | 'warning' | 'danger';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  /** Color of the question card edge. */
  tone?: CardTone;
  /** Side of the question card edge. */
  edge?: 'top' | 'left';
}

const VARIANT: Record<CardVariant, string> = {
  default: 'rounded-xl border border-line bg-surface/60 shadow-card',
  raised: 'rounded-xl border border-line bg-surface shadow-raised',
  glass: 'rounded-2xl glass',
  'glass-strong': 'rounded-2xl glass-strong',
  'glass-subtle': 'rounded-2xl glass-subtle',
  interactive:
    'lift cursor-pointer rounded-xl border border-line bg-surface shadow-soft hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
  question: "relative overflow-hidden rounded-2xl glass-strong before:absolute before:content-['']",
};

const TONE: Record<CardTone, string> = {
  accent: 'before:bg-accent-text',
  success: 'before:bg-success',
  warning: 'before:bg-warning',
  danger: 'before:bg-danger',
};

const EDGE = {
  top: 'before:inset-x-0 before:top-0 before:h-1.5',
  left: 'before:inset-y-0 before:left-0 before:w-1.5',
};

export function Card({ className, children, variant = 'default', tone = 'accent', edge = 'top', ...props }: CardProps) {
  return (
    <div
      className={cn(VARIANT[variant], variant === 'question' && cn(TONE[tone], EDGE[edge]), className)}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 pt-5 pb-2', className)} {...props}>{children}</div>;
}

export function CardContent({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 pb-5', className)} {...props}>{children}</div>;
}

export function CardFooter({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex items-center gap-3 px-5 pb-5', className)} {...props}>{children}</div>;
}

export function CardTitle({ className, children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-heading text-primary', className)} {...props}>{children}</h3>;
}

export function CardDescription({ className, children, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('mt-1 text-body text-muted', className)} {...props}>{children}</p>;
}
