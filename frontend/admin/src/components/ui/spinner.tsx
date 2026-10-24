import { cn } from '../../lib/utils';

interface SpinnerProps {
  className?: string;
  /** Screen-reader text. Leave empty when the parent already says it is busy (Button sets aria-busy). */
  label?: string;
}

/** A small ring that turns in the current text color. It keeps turning under reduced motion. */
export function Spinner({ className, label }: SpinnerProps) {
  return (
    <span role={label ? 'status' : undefined} className="inline-flex">
      <span
        aria-hidden="true"
        className={cn('inline-block size-[1.1em] animate-spin rounded-full border-2 border-current border-r-transparent', className)}
      />
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
