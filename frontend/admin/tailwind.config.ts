import type { Config } from 'tailwindcss';
import preset from '../tailwind.preset';

export default {
  presets: [preset],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Fonts, type scale, radius, shadows and motion come from the shared preset.
  plugins: [],
} satisfies Config;
