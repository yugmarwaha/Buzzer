// Shared Tailwind preset: maps the CSS variables in theme.css to class names.
// Plain object on purpose: no imports, because frontend/ has no node_modules.
// It extends the default theme (never replaces it); see docs/plans/ui-themes.md sections 3.1 and 12.
// Color keys must stay flat 'name': 'rgb(var(--name) / <alpha-value>)' entries: check-theme.mjs
// parses them with a regex. Non-color keys (type, radius, shadow, motion) are not parity-checked.

const SANS = [
  'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI Variable Text"',
  '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif',
];
const DISPLAY = [
  '"SF Pro Display"', 'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont',
  '"Segoe UI Variable Display"', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif',
];
const MONO = [
  'ui-monospace', '"SF Mono"', 'SFMono-Regular', '"Cascadia Mono"', '"Roboto Mono"', 'Menlo', 'Consolas', 'monospace',
];

// One type-scale step: [size, { lineHeight, letterSpacing, fontWeight }], typed as a tuple for Tailwind.
type TypeStep = [string, { lineHeight: string; letterSpacing: string; fontWeight?: string }];
const step = (size: string, lineHeight: string, letterSpacing: string, fontWeight?: string): TypeStep =>
  fontWeight ? [size, { lineHeight, letterSpacing, fontWeight }] : [size, { lineHeight, letterSpacing }];

// Entrance animations wait var(--i) steps, so .stagger lists and staggerStyle(n) work.
const STAGGER = 'calc(var(--i, 0) * var(--stagger-step))';

export default {
  theme: {
    extend: {
      fontFamily: {
        sans: SANS,
        display: DISPLAY,
        mono: MONO,
      },
      // Type scale. Each entry sets size, line height, tracking and weight together.
      // clamp() sizes grow with the viewport so Host screens read from the back of a room.
      fontSize: {
        'display': step('clamp(2.75rem, 1.6rem + 4.2vw, 5.5rem)', '1.02', '-0.035em', '800'),
        'title': step('clamp(2rem, 1.3rem + 2.4vw, 3.5rem)', '1.08', '-0.028em', '700'),
        'stage': step('clamp(2rem, 1rem + 3.2vw, 4.5rem)', '1.15', '-0.022em', '700'),
        'heading': step('clamp(1.375rem, 1.15rem + 0.8vw, 1.875rem)', '1.2', '-0.018em', '650'),
        'subheading': step('1.125rem', '1.4', '-0.01em', '600'),
        'body-lg': step('1.125rem', '1.6', '-0.005em'),
        'body': step('1rem', '1.6', '0'),
        'caption': step('0.8125rem', '1.45', '0.005em', '500'),
        'label': step('0.75rem', '1.3', '0.08em', '600'),
        'stat': step('clamp(2.25rem, 1.5rem + 2.5vw, 4rem)', '1', '-0.03em', '800'),
        'timer': step('clamp(1.5rem, 1rem + 1.6vw, 2.75rem)', '1', '-0.02em', '700'),
        'code': step('clamp(2.5rem, 1.5rem + 4vw, 5rem)', '1', '0.14em', '700'),
      },
      // Radius scale. lg (controls) and xl (cards) are larger than Tailwind's defaults on purpose.
      borderRadius: {
        sm: '0.375rem',
        md: '0.5rem',
        lg: '0.75rem',
        xl: '1rem',
        '2xl': '1.25rem',
        '3xl': '1.75rem',
        pill: '9999px',
      },
      boxShadow: {
        card: 'var(--shadow-card)', // legacy single level; none in dark
        soft: 'var(--shadow-soft)',
        raised: 'var(--shadow-raised)',
        float: 'var(--shadow-float)',
        overlay: 'var(--shadow-overlay)',
        glow: 'var(--shadow-glow)',
        button: 'var(--shadow-button)',
      },
      transitionDuration: {
        instant: 'var(--dur-instant)',
        fast: 'var(--dur-fast)',
        base: 'var(--dur-base)',
        slow: 'var(--dur-slow)',
        slower: 'var(--dur-slower)',
      },
      transitionTimingFunction: {
        smooth: 'var(--ease-out)',
        swap: 'var(--ease-in-out)',
        spring: 'var(--ease-spring)',
      },
      // Keyframes are defined in theme.css (always present), not here.
      animation: {
        'fade-in': `fade-in var(--dur-base) var(--ease-out) ${STAGGER} both`,
        'slide-up': `slide-up var(--dur-slow) var(--ease-out) ${STAGGER} both`,
        'slide-down': `slide-down var(--dur-slow) var(--ease-out) ${STAGGER} both`,
        'scale-in': `scale-in var(--dur-base) var(--ease-out) ${STAGGER} both`,
        'pop': `pop var(--dur-slow) var(--ease-out) ${STAGGER} both`,
        'pulse-soft': 'pulse-soft 1s var(--ease-in-out) infinite',
        'shimmer': 'shimmer 1.6s linear infinite',
        'tick': 'tick var(--dur-base) var(--ease-out) both',
        'shake': 'shake 400ms var(--ease-in-out) both',
      },
      colors: {
        'accent': 'rgb(var(--accent) / <alpha-value>)',
        'accent-hover': 'rgb(var(--accent-hover) / <alpha-value>)',
        'accent-fg': 'rgb(var(--accent-fg) / <alpha-value>)',
        'accent-hint': 'rgb(var(--accent-hint) / <alpha-value>)',
        'accent-text': 'rgb(var(--accent-text) / <alpha-value>)',
        'success': 'rgb(var(--success) / <alpha-value>)',
        'success-tint': 'rgb(var(--success-tint) / <alpha-value>)',
        'warning': 'rgb(var(--warning) / <alpha-value>)',
        'warning-fg': 'rgb(var(--warning-fg) / <alpha-value>)',
        'warning-tint': 'rgb(var(--warning-tint) / <alpha-value>)',
        'danger': 'rgb(var(--danger) / <alpha-value>)',
        'danger-fg': 'rgb(var(--danger-fg) / <alpha-value>)',
        'danger-hover': 'rgb(var(--danger-hover) / <alpha-value>)',
        'danger-tint': 'rgb(var(--danger-tint) / <alpha-value>)',
        'ring': 'rgb(var(--ring) / <alpha-value>)',
        'timer-1': 'rgb(var(--timer-1) / <alpha-value>)',
        'timer-2': 'rgb(var(--timer-2) / <alpha-value>)',
        'timer-4': 'rgb(var(--timer-4) / <alpha-value>)',
        'timer-track': 'rgb(var(--timer-track) / <alpha-value>)',
      },
      backgroundColor: {
        'page': 'rgb(var(--page) / <alpha-value>)',
        'page-2': 'rgb(var(--page-2) / <alpha-value>)',
        'surface': 'rgb(var(--surface) / <alpha-value>)',
        'raised': 'rgb(var(--raised) / <alpha-value>)',
        'track': 'rgb(var(--track) / <alpha-value>)',
        'glass': 'rgb(var(--glass) / <alpha-value>)',
        'blob-1': 'rgb(var(--blob-1) / <alpha-value>)',
        'blob-2': 'rgb(var(--blob-2) / <alpha-value>)',
        'blob-3': 'rgb(var(--blob-3) / <alpha-value>)',
      },
      textColor: {
        'primary': 'rgb(var(--primary) / <alpha-value>)',
        'secondary': 'rgb(var(--secondary) / <alpha-value>)',
        'muted': 'rgb(var(--muted) / <alpha-value>)',
      },
      borderColor: {
        'line': 'rgb(var(--line) / <alpha-value>)',
        'line-strong': 'rgb(var(--line-strong) / <alpha-value>)',
        'glass-edge': 'rgb(var(--glass-edge) / <alpha-value>)',
        'glass-highlight': 'rgb(var(--glass-highlight) / <alpha-value>)',
      },
      divideColor: {
        'line': 'rgb(var(--line) / <alpha-value>)',
        'line-strong': 'rgb(var(--line-strong) / <alpha-value>)',
      },
      ringOffsetColor: {
        'page': 'rgb(var(--page) / <alpha-value>)',
      },
      placeholderColor: {
        'muted': 'rgb(var(--muted) / <alpha-value>)',
      },
    },
  },
};
