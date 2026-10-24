import { Check, Lock } from 'lucide-react';
import { Spinner } from '../../components/ui/spinner';

// The confirmation moment: a ring settles in, then the check pops inside it.
export default function FeedbackPage() {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col items-center justify-center px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
      <div className="glass-strong flex w-full animate-scale-in flex-col items-center gap-5 rounded-3xl px-6 py-10 text-center">
        <span className="flex size-28 animate-scale-in items-center justify-center rounded-full border-4 border-success bg-success-tint">
          <Check
            className="size-16 animate-pop text-success [--i:2]"
            strokeWidth={3}
            aria-hidden="true"
          />
        </span>
        <div className="flex flex-col items-center gap-2">
          <p className="animate-slide-up text-title text-primary [--i:3]">
            Answer locked in!
          </p>
          <p className="flex animate-fade-in items-center gap-1.5 text-body text-secondary [--i:4]">
            <Lock className="size-4 shrink-0" aria-hidden="true" />
            You can't change it now.
          </p>
        </div>
        <p className="glass-subtle flex animate-fade-in items-center gap-2 rounded-pill px-4 py-2 text-body text-muted [--i:6]">
          <Spinner className="text-accent-text" />
          Waiting for host to reveal results…
        </p>
      </div>
    </div>
  );
}
