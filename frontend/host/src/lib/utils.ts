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

/** T7 number display (design §5.8): at most two decimals, trailing zeros removed, no -0. */
export function formatNum(v: number): string {
  return Number(v.toFixed(2)).toString();
}
