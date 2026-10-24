import { forwardRef } from 'react';
import { cn } from '../../lib/utils';
import { Spinner } from './spinner';

// Variants: primary (main action), secondary (filled neutral), outline (no fill), ghost (text
// only), danger (destructive), glass (on a glass surface or the page backdrop).
// 'default' and 'destructive' are the older names for primary and danger and still work.
export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'glass' | 'default' | 'destructive';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'xl';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, keeps the button's width and colors, and blocks clicks. */
  loading?: boolean;
}

const VARIANT: Record<ButtonVariant, { look: string; disabled: string }> = {
  primary: {
    look: 'bg-accent text-accent-fg shadow-button hover:bg-accent-hover hover:shadow-glow',
    disabled: 'disabled:bg-raised disabled:text-muted disabled:shadow-none',
  },
  secondary: {
    look: 'border border-line-strong bg-surface text-primary shadow-soft hover:bg-raised',
    disabled: 'disabled:border-line disabled:bg-raised disabled:text-muted disabled:shadow-none',
  },
  outline: {
    look: 'border border-line-strong text-primary hover:bg-raised',
    disabled: 'disabled:border-line disabled:text-muted',
  },
  ghost: {
    look: 'text-secondary hover:bg-raised hover:text-primary',
    disabled: 'disabled:text-muted',
  },
  danger: {
    look: 'bg-danger text-danger-fg shadow-button hover:bg-danger-hover',
    disabled: 'disabled:bg-raised disabled:text-muted disabled:shadow-none',
  },
  glass: {
    look: 'glass-subtle text-primary hover:bg-surface',
    disabled: 'disabled:bg-raised disabled:text-muted disabled:shadow-none',
  },
  default: { look: '', disabled: '' },
  destructive: { look: '', disabled: '' },
};
VARIANT.default = VARIANT.primary;
VARIANT.destructive = VARIANT.danger;

// Player: every size is at least 44px tall, so it stays thumb-friendly.
const SIZE: Record<ButtonSize, string> = {
  sm: 'min-h-11 px-4 py-2 text-sm',
  md: 'min-h-12 px-5 py-3 text-base',
  lg: 'min-h-14 px-6 py-4 text-lg',
  xl: 'min-h-16 px-8 py-4 text-xl rounded-xl',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'default', size = 'md', loading = false, disabled, children, ...props },
  ref,
) {
  const v = VARIANT[variant];
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        'relative inline-flex items-center justify-center gap-2 rounded-lg font-semibold',
        'transition-[background-color,border-color,color,box-shadow,transform] duration-fast ease-smooth',
        'motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0 motion-safe:active:scale-[0.98]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
        'disabled:pointer-events-none',
        loading ? 'cursor-wait' : cn('disabled:cursor-not-allowed', v.disabled),
        v.look,
        SIZE[size],
        className
      )}
      {...props}
    >
      {loading ? (
        <>
          <span className="invisible inline-flex items-center gap-2">{children}</span>
          <span className="absolute inset-0 flex items-center justify-center">
            <Spinner />
          </span>
        </>
      ) : (
        children
      )}
    </button>
  );
});
