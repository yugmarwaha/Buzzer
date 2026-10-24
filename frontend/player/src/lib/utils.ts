import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Teach tailwind-merge the shared preset's custom names (frontend/tailwind.preset.ts), so that
// cn('text-primary', 'text-heading') keeps both (size vs color) and shadow-*/rounded-pill
// override each other instead of stacking. Keep this list in step with the preset.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['display', 'title', 'stage', 'heading', 'subheading', 'body-lg', 'body', 'caption', 'label', 'stat', 'timer', 'code'] }],
      shadow: [{ shadow: ['card', 'soft', 'raised', 'float', 'overlay', 'glow', 'button'] }],
      rounded: [{ rounded: ['pill'] }],
      duration: [{ duration: ['instant', 'fast', 'base', 'slow', 'slower'] }],
      ease: [{ ease: ['smooth', 'swap', 'spring'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** A, B, C… for option index i; numbers after Z. */
export function optionLetter(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1);
}

/** T8 §5 display label: the option text, or "Option A" for an image-only option. */
export function optionDisplay(text: string | undefined | null, i: number): string {
  return text && text.trim() ? text : `Option ${optionLetter(i)}`;
}

/** "A — text" for an answer line; just "Option A" for an image-only option. */
export function optionAnswerLabel(options: string[] | undefined, i: number): string {
  const text = options?.[i];
  return text && text.trim() ? `${optionLetter(i)} — ${text}` : optionDisplay(text, i);
}

/** T7 number display (design §5.8): at most two decimals, trailing zeros removed, no -0. */
export function formatNum(v: number): string {
  return Number(v.toFixed(2)).toString();
}

const GUESS_PATTERN = /^-?(\d+([.,]\d*)?|[.,]\d+)$/;
const GUESS_LIMIT = 1e12;

/**
 * Closest Guess input → number, or null (design §5.7). Optional leading minus, digits, at
 * most one decimal separator (`.` or `,`); no exponent, no thousands separators, not empty
 * or a bare `-`. Same bounds as the server's validate_answer: finite, |v| ≤ 1e12.
 */
export function parseGuess(text: string): number | null {
  const t = text.trim();
  if (!GUESS_PATTERN.test(t)) return null;
  const v = Number(t.replace(',', '.'));
  return Number.isFinite(v) && Math.abs(v) <= GUESS_LIMIT ? v : null;
}

/** Returns true if the token is missing, malformed, or past its exp claim. */
export function isTokenExpired(token: string | null): boolean {
  if (!token) return true;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return Date.now() / 1000 >= (payload.exp ?? 0);
  } catch {
    return true;
  }
}
