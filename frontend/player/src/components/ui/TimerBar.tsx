import { useEffect, useRef, useState } from 'react';

interface TimerBarProps {
  totalSeconds: number;
  paused?: boolean;
  /** Called once when the countdown reaches 0. */
  onExpire?: () => void;
}

const SIZE = 56;
const STROKE = 6;
const NUMBER_CLASS = 'text-base';
// Full class names (not built from fragments) so Tailwind keeps them and the color check can read them.
const ARC_CLASS = ['stroke-timer-1', 'stroke-timer-2', 'stroke-timer-4'] as const;

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function TimerBar({ totalSeconds, paused = false, onExpire }: TimerBarProps) {
  const [timeLeft, setTimeLeft] = useState(totalSeconds);
  const pausedRef = useRef(paused);
  const endTimeRef = useRef(Date.now() + totalSeconds * 1000);
  const frozenRef = useRef(totalSeconds);
  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;

  // Keep pausedRef in sync; on resume, reset endTime from the frozen value
  useEffect(() => {
    pausedRef.current = paused;
    if (!paused) {
      endTimeRef.current = Date.now() + frozenRef.current * 1000;
    }
  }, [paused]);

  useEffect(() => {
    if (totalSeconds <= 0) return;

    const id = setInterval(() => {
      if (pausedRef.current) {
        // Slide endTime forward so resume continues from the frozen value
        endTimeRef.current = Date.now() + frozenRef.current * 1000;
        return;
      }
      const remaining = Math.max(0, (endTimeRef.current - Date.now()) / 1000);
      frozenRef.current = remaining;
      setTimeLeft(remaining);
      if (remaining === 0) {
        onExpireRef.current?.();
        clearInterval(id);
      }
    }, 100);

    return () => clearInterval(id);
  }, []); // runs once on mount; parent uses key={questionId} to remount per question

  const reduce = prefersReducedMotion();
  // Under reduced motion the dial moves once per second, not smoothly.
  const shown = reduce ? Math.ceil(timeLeft) : timeLeft;
  const fraction = Math.max(0, Math.min(1, shown / totalSeconds));
  const displaySeconds = Math.ceil(timeLeft);

  // Color steps are percentages of the time limit, so a short question does not flash through colors.
  const stage = fraction > 0.75 ? 0 : fraction > 0.4 ? 1 : 2;
  const urgent = timeLeft > 0 && timeLeft <= 5 && !paused;
  const radius = (SIZE - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;

  return (
    <div
      role="timer"
      aria-live="off"
      aria-label="Time left"
      className="relative mx-auto flex shrink-0 items-center justify-center"
      style={{ width: SIZE, height: SIZE }}
    >
      {/* The arc starts at 12 o'clock and its end moves anticlockwise as time runs out. */}
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} className="-rotate-90" aria-hidden="true">
        <circle cx={SIZE / 2} cy={SIZE / 2} r={radius} fill="none" strokeWidth={STROKE} className="stroke-timer-track" />
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={radius}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          className={`${ARC_CLASS[stage]} transition-[stroke-dashoffset,stroke] duration-100 ease-linear motion-reduce:transition-none`}
        />
      </svg>
      <span
        className={`absolute font-mono font-bold tabular-nums ${NUMBER_CLASS} ${
          urgent ? 'animate-pulse-soft text-danger' : 'text-primary'
        }`}
      >
        {displaySeconds}
      </span>
      <span className="sr-only">{displaySeconds} seconds left</span>
    </div>
  );
}
