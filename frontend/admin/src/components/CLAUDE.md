# components

## Purpose
Shared, reusable UI primitives for the admin app. Currently just one subfolder, `ui/`, holding presentational components (`button`, `card`, `input`, `badge`, `progress`, `spinner`, `motion.ts`, `ThemeToggle`) (no business logic, no data fetching; `ThemeToggle` has only local theme state).

## Contents
- ui/button.tsx — `Button`: styled `<button>` wrapper with `variant` (`primary` | `secondary` | `outline` | `ghost` | `danger` | `glass`; `default` and `destructive` are older names) and `size` (`sm` to `xl`) props, plus a `loading` prop; merges extra classes via `cn`. `rounded-lg`, a `focus-visible` ring (`ring-ring`, offset `ring-offset-page`), and a disabled style that swaps to a muted label on `bg-raised` (outline/ghost: muted text) instead of using opacity.
- ui/card.tsx — `Card` (variants `default`, `raised`, `glass`, `interactive`, `question`), `CardHeader`, `CardContent`, `CardFooter`, `CardTitle`, `CardDescription`.
- ui/badge.tsx, ui/progress.tsx, ui/spinner.tsx — `Badge` (`tone`, `size`), `Progress` on `bg-track`, `Spinner`; ui/motion.ts — `staggerStyle(i)`.
- ui/input.tsx — `Input`: `rounded-lg`, `border-line-strong`, `bg-surface`, `placeholder-muted`, focus-visible ring; passes through all native input props.

## How it fits in
Every page under `frontend/admin/src/pages` imports button, card, and input (via `../components/ui/...`) as its basic building blocks for forms and lists. Each primitive imports `cn` from `../../lib/utils` to merge Tailwind classes with any caller-supplied `className`.

## Gotchas
- Only three form/layout primitives exist here (button, card, input), plus `ThemeToggle`. There's no Select, Textarea, or Checkbox component, even though pages render styled `<select>`, `<textarea>`, and checkbox inputs repeatedly by hand — anyone adding a new form should check `pages/` for the existing inline pattern before assuming a component exists here.
- All three components are style-only pass-throughs with no internal state; adding behavior (e.g. loading states, icons-only variants) currently happens ad hoc in the pages that use them, not here.

## Styling
Colors come from the shared theme tokens (`frontend/theme.css`, mapped in `tailwind.preset.ts`), not Tailwind palette classes; see `frontend/CLAUDE.md`.

`ui/ThemeToggle.tsx` — light/dark switch. It calls `window.buzzerTheme.set()` from the inline script in `frontend/admin/index.html`, which saves the choice in the `buzzer-theme` localStorage key (so it persists across reloads) and, until a choice is saved, follows the OS `prefers-color-scheme` setting. It watches `data-theme` to keep its label right. Mounted in the sidebar footer (`variant="row"`, icon plus label) and fixed top-right on the login page (`variant="icon"`, 48px square, from `App.tsx`). If the inline script did not run, it falls back to setting `data-theme` directly. Identical copy in each app's `components/ui`; keep the three in step. `npm run check:theme` runs the admin script and checks the OS default and persistence.
