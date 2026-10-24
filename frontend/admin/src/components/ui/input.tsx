import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

/** Shared field look. Use it on <select> and <textarea> too, so every field matches Input. */
export const fieldClassName = cn(
  'w-full rounded-lg border border-line-strong bg-surface text-primary placeholder-muted shadow-soft',
  'transition-[border-color,box-shadow] duration-fast ease-smooth',
  'focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
  'disabled:cursor-not-allowed disabled:bg-raised disabled:text-muted disabled:shadow-none',
  'aria-[invalid=true]:border-danger',
  'min-h-10 px-3 py-2 text-base'
);

type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(fieldClassName, className)} {...props} />;
});
