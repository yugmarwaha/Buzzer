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
