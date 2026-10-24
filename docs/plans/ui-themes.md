# UI Look-and-Feel and Theming (T9)

Status: IMPLEMENTATION UNDER WAY (Phases 0-3 done in code; after-screenshots partly done; rendered accessibility audit done; see section 11). Design: rev 4, Goldfish rounds 1–3 addressed (section 10); no further rounds planned. Not committed. Owner: Yug Marwaha.
Branch: `yug/ui-themes`.
T3 step 4 accepts `docs/plans/<slug>.md` or `docs/design-<feature>.md`; this file is the former, and
the T10 document links to this path.

Items marked **[PROPOSED]** were confirmed by the user and are the working plan; an implementer
may change them only after asking the user. Everything else is agreed or verified against the
code or by a throwaway experiment.

## 1. Problem

The three React apps (`frontend/host`, `frontend/player`, `frontend/admin`) each hardcode one dark
look: `index.css` sets `body { background: #0f172a; color: #f8fafc }` and components use about 720
raw Tailwind palette classes (`bg-slate-800`, `text-indigo-400`, `bg-red-600`, ...). There is no
light theme, no toggle, no shared vocabulary, and no checks for contrast or focus visibility.

T9 requires, for all three apps:

1. A semantic color-token layer (CSS variables mapped into each app's Tailwind theme). Components
   use tokens; raw palette utilities and hex values are gone outside the token definitions.
2. Light and dark themes that fully cover every page, including the new T7/T8 surfaces (question
   editors, canvas question, image management), which are being built in parallel.
3. A visible toggle in each app. First visit follows `prefers-color-scheme`; the choice persists.
4. WCAG 2.1 AA: 4.5:1 for body text in both themes, visible focus states on interactive elements.
5. Context fit: Player thumb-friendly on mobile, Host readable from the back of a classroom,
   Admin dense but scannable.
6. Before/after screenshots of key screens in both themes committed to `docs/ui/`.

R9 (9 points) also grades whether the design looks deliberate (consistent spacing, typography,
hierarchy), not merely recolored; see section 3.8.

## 2. Decisions already made

- **Direction:** a clean classroom tool. Strong readability, restrained purposeful motion, no
  generic "AI-generated" styling.
- **Order:** design and ship dark first, using today's UI as the baseline; then design and verify
  light separately. Dark palette = option **B (teal)**; see `docs/ui/palette-B-teal.html`
  (dark panel). The user plans to adjust some UI details of B later, so values below are
  provisional until the implementation visual check.
- **One shared vocabulary:** one `frontend/theme.css`, one token vocabulary, one palette for
  Host, Player and Admin.
- **Toggle:** two choices only (light / dark). With no saved choice, follow the OS. One shared
  localStorage key, `buzzer-theme`, values `"light"` or `"dark"`. Under nginx (`:8080`) all three
  apps share one origin so the choice is shared; in dev the three ports are separate origins and
  each remembers its own choice (accepted).
- **Toggle placement:** Admin: sidebar footer. Host: a corner away from the End game button
  (top-right) and QR panel (bottom-right) on game screens. Player: always visible on every screen
  including live questions (no hiding); if the "Host disconnected" banner is showing, the banner
  sits above the toggle. Verify at 375px width.
- **Player question screen:** keep current behavior. T9 does not add the question prompt there.
- **Host question screen:** keep current behavior: locking answers stops the timer and hides the
  prompt. Long prompts: responsive text size, allow scrolling, never shrink so far that it is hard
  to read across a classroom (floor in 3.8).
- **Player options:** replace the eight saturated per-option background colors with
  high-contrast neutral cards with a clear letter label and a distinct selected / locked state
  that does not rely on color alone (card spec in 3.8).
- **Merge safety with T7/T8:** plan and build the shared infrastructure now; defer broad component
  migration until the T7/T8 changes merge. The final result must still cover the new screens.
- **Timer clock (optional, last):** replace the horizontal timer bar with a circular dial whose
  colored arc shrinks anticlockwise as time runs out. Not required by T9 or R9; built only after
  everything else is done, at the user's discretion. If skipped, the themed bar stays.
- **Subagents:** may be used for independent tasks (Opus 5.5 for shared infrastructure, Sonnet
  5.5 for per-app mechanical migration). No git worktrees.

## 3. Technical plan

### 3.0 Facts about the current build (verified)

- Tailwind `^3.4.11`; `tailwind-merge ^2.5.2` and `lucide-react` are already dependencies of all
  three apps (so toggle icons need no new dependency).
- Each app is `"type": "module"`, with `build` = `tsc -b && vite build`. Each `tsconfig.node.json`
  is `composite: true` with `include: ["vite.config.ts", "tailwind.config.ts"]` and no `outDir`,
  so `tsc -b` **emits** `tailwind.config.js`, `vite.config.js` and `.d.ts` files beside the
  sources. They are gitignored, but they regenerate on every build, and Tailwind resolves `.js`
  before `.ts`, so a stale generated `.js` can silently shadow edits to the `.ts` config in dev.
- nginx serves the locally built `frontend/*/dist` folders (bind mounts in `docker-compose.yml`);
  there is no Docker build of the frontends. nginx sets no Content-Security-Policy header, so an
  inline script is allowed.
- CI's only frontend job is `frontend-typecheck` (`npx tsc --noEmit` per app, Node 20). It does
  not run `vite build`. The repo root `package.json` holds the cross-app npm scripts;
  `frontend/` has no `package.json` of its own. Local Node is v20.
- Player's `index.html` viewport meta contains `maximum-scale=1.0, user-scalable=no`, which blocks
  pinch-zoom (a WCAG 1.4.4 problem); see decision O5.
- Experiment (throwaway copy of the Host config, not in the repo): with `tsconfig.node.json`
  `include` extended to `../tailwind.preset.ts` and nothing else, `tsc -b` succeeds but also emits
  `frontend/tailwind.preset.js` beside the source; adding only `outDir` fails with TS6059; adding
  `outDir` **and** `"rootDir": ".."` succeeds and emits everything under `node_modules/.tmp`,
  nothing beside the sources. The preset must import nothing (`frontend/` has no `node_modules`)
  and `@types/node` is not installed, so `vite.config.ts` cannot use `path`/`__dirname`.
- The Host QR code is `QRCodeSVG` inside a `bg-white` wrapper in `GameLayout.tsx` and
  `LobbyPage.tsx`; it must stay black on white to scan in both themes. The Host word cloud
  (`ResultsPage.tsx`) uses Tailwind classes only, with no JS-computed colors.

### 3.1 Token ownership and Tailwind mapping

- `frontend/theme.css` (new, outside any app root) is the **source of truth** for token values.
  It defines them as CSS custom properties holding **space-separated RGB channels** (for example
  `--page: 12 20 22`), not hex. Reason: 38 existing classes use opacity modifiers such as
  `bg-slate-800/60`; Tailwind only supports modifiers when colors are declared as
  `rgb(var(--x) / <alpha-value>)`. Hex values in this document are informational; the check
  scripts convert from the channels.
- Dark values live in **one combined rule**, `:root, :root[data-theme="dark"] { ... }`. Light
  values will live in `:root[data-theme="light"] { ... }` (Phase 2). Variable names are
  `--<token>` with the token name exactly as in 3.2 (for example `--line-strong`, `--accent-text`,
  `--timer-track`). `theme.css` also sets `color-scheme` per theme, a global
  `:focus-visible` outline using the `ring` token, and a `prefers-reduced-motion` block (3.4).
- `frontend/tailwind.preset.ts` (new) is a Tailwind preset used via `presets: [...]` in each
  app's `tailwind.config.ts`. It **extends** the theme (`theme.extend`), it does not replace it,
  so existing slate/indigo classes keep working until the Phase 1 migration finishes. Where each
  token lives (this table is the contract the name-parity check reads):

  | Tailwind key | Tokens |
  |---|---|
  | `colors` (works for bg, text, border, ring, fill) | `accent`, `accent-hover`, `accent-fg`, `accent-hint`, `accent-text`, `success`, `warning`, `danger`, `info`, each `*-fg` and `*-tint`, `danger-hover`, `ring`, `timer-1`..`timer-4`, `timer-track`, `medal-1`..`medal-3` |
  | `backgroundColor` | `page`, `surface`, `raised` (not `line`, see 3.2) |
  | `textColor` | `primary`, `secondary`, `muted` |
  | `borderColor`, `divideColor` | `line`, `line-strong` |
  | `ringOffsetColor` | `page` |
  | `placeholderColor` | `muted` |

  The preset **stays in extend mode permanently**. Overriding `theme.colors` was
  considered and rejected: Tailwind silently emits nothing for unknown classes (no build error),
  so a raw class merged later would render unstyled, and override also removes `white`, `black`,
  `transparent` and `current` (needed by the QR wrapper and `bg-transparent`). Enforcement is
  `check-raw-colors.mjs` (3.6), which the author runs before every merge request.
- **Type-checking and emit (verified by experiment, see 3.0):** each `tsconfig.node.json` gets
  `"../tailwind.preset.ts"` added to `include`, `"rootDir": ".."`, and `"outDir":
  "./node_modules/.tmp/node-out"`. This keeps `tsc -b` green and stops it emitting JS beside the
  sources. The preset is a plain object with no imports. CI's plain `tsc --noEmit` probably does
  not check `tsconfig.node.json` at all (it follows `references` only under `-b`), so `npm run
  build` is the real check. Also gitignore `frontend/tailwind.preset.js` and `.d.ts` as a
  fallback.
- **Stale generated files on every machine:** teammates' existing `tailwind.config.js` and
  `vite.config.js` (generated before this change) would shadow the new config after they pull,
  and the dev server would then silently lack the tokens. Verified in a throwaway copy: Tailwind
  picks the stale `.js` by default and picks the `.ts` when given an explicit path. Decision
  (O6): **name the config explicitly** instead of deleting files. Each app's `postcss.config.js`
  (tracked) sets `tailwindcss: { config: './tailwind.config.ts' }`, and the `dev`, `build` and
  `preview` npm scripts pass `--config vite.config.ts` to Vite. Stale files are then ignored. The
  Vite half is documented behavior but untested; Phase 0 tests it first, and if it fails the
  fallback is a small `frontend/scripts/clean-generated-configs.mjs` run from `predev`/`prebuild`.
  Starting Vite without the npm scripts (for example bare `npx vite`) would still pick a stale
  file; `frontend/CLAUDE.md` says to use `npm run dev`.
- `tailwind-merge`'s `cn()` must treat the new names correctly (for example `text-primary` as a
  text color, not a font size; `border-line` as a color, not a width). Phase 0 verifies this with
  a quick check of `cn('text-primary text-lg', 'text-secondary')` style cases per app; if any name
  is misclassified, extend `cn`'s `twMerge` config in each app's `lib/utils.ts`.
- Each app imports the CSS from `src/main.tsx` (`import '../../theme.css'`) before its own
  `index.css`. Each app's `vite.config.ts` sets `server.fs.allow` to
  `['..', '.']` (relative paths, resolved against the app root; setting it replaces Vite's default
  list, so the app root is listed too; no `path` import because `@types/node` is absent). Production builds are unaffected. Verify on
  all three dev servers and all three production builds, then open each under nginx
  (`/host/`, `/player/`, `/admin/`).
- No shared npm package is created and no new dependencies are added.

### 3.2 Token vocabulary (names are the contract)

Tailwind class forms use the Tailwind extension key that matches the property, so `text-primary`
is a text color only.

| Group | Tokens | Used as |
|---|---|---|
| Surfaces | `page`, `surface`, `raised` | `bg-page`, `bg-surface`, `bg-raised` |
| Lines | `line`, `line-strong` | `border-line`, `border-line-strong`, `divide-*` |
| Text | `primary`, `secondary`, `muted` | `text-primary`, `text-secondary`, `text-muted` |
| Accent | `accent`, `accent-hover`, `accent-fg`, `accent-hint`, `accent-text` | button fill, hover, text on accent, selected-card tint, accent-colored text/links |
| Status fills | `success`, `warning`, `danger`, `info`, plus a foreground for each: `success-fg`, `warning-fg`, `danger-fg`, `info-fg`, and `danger-hover` | status text, chips, status buttons |
| Status tints | `success-tint`, `warning-tint`, `danger-tint`, `info-tint` | soft alert/notice backgrounds (replacing `bg-red-900/30`-style classes); text on them is the matching status color |
| Medals | `medal-1`, `medal-2`, `medal-3` | podium and rank colors on the game-over screens |
| Focus | `ring` | focus-visible outline and `ring-*` |
| Timer | `timer-1`, `timer-2`, `timer-3`, `timer-4`, `timer-track` | timer fill steps and track |

Rules: `line` is a subtle separator, allowed only where the element's fill or text carries the
meaning. Inputs, outline buttons and option cards use `line-strong` (>= 3:1 against the surface
they sit on). `raised`, `line` and `timer-track` are deliberately **distinct** values, and a bordered element
sitting on `raised` uses `line-strong` (`line` on `raised` is only 1.2:1). `line` is **not** a background token (text on it
measures 4.43:1 for `muted`); hover steps use `raised`. `accent` also works as text because it
sits under `colors`, but it is only 3.4:1 on `page`, so text must use `accent-text`;
`check-raw-colors` flags `text-accent` (but not `text-accent-text`, `-fg`, `-hint`). Text never
uses an opacity modifier and translucent fills (`bg-surface/60`) are only allowed over `page`;
`check-raw-colors` flags `text-*/NN` and any `bg-*/NN` other than `bg-page/NN`, `bg-surface/NN`
and `bg-black/NN` scrims, and `check-theme` tests text against the composited color. A white-on-fill pairing is never assumed:
status fills pair with their own `*-fg` token.

### 3.3 Dark palette (option B, provisional, revised after Goldfish)

| Token | Value | Token | Value |
|---|---|---|---|
| `page` | `#0c1416` | `accent` | `#0f766e` |
| `surface` | `#14201f` | `accent-hover` | `#115e59` |
| `raised` | `#1f302f` | `accent-fg` | `#ffffff` |
| `line` | `#2a3f3e` | `accent-hint` | `#10302d` |
| `line-strong` | `#6b8c89` | `accent-text` | `#5eead4` |
| `primary` | `#f0fdfa` | `success` / `success-fg` | `#4ade80` / `#052e16` |
| `secondary` | `#ccdcdb` | `warning` / `warning-fg` | `#fbbf24` / `#422006` |
| `muted` | `#8fa8a6` | `danger` / `danger-fg` | `#f87171` / `#450a0a` |
| `ring` | `#5eead4` | `info` / `info-fg` | `#60a5fa` / `#082f49` |
| `timer-track` | `#233635` | `timer-1..4` | `#4ade80`, `#fbbf24`, `#fb923c`, `#f87171` |
| `danger-hover` | `#fca5a5` (`danger-fg` on it 8.5) | `medal-1` / `medal-2` / `medal-3` | `#fbbf24` / `#ccdcdb` / `#fb923c` |
| `success-tint` | `#12301f` | `warning-tint` | `#33290f` |
| `danger-tint` | `#3a1c1f` | `info-tint` | `#14283d` |

Contrast figures recomputed after the Goldfish review (the first draft's `line-strong` value
`#3a5251` was only 2.2:1 on `page` and 2.0:1 on `surface`, below the 3:1 it claimed; it is now
`#6b8c89`, which measures 5.1 on `page`, 4.6 on `surface`, 3.8 on `raised`). Other measured
pairs: `success-fg`/`success` 8.6, `warning-fg`/`warning` 8.7, `info-fg`/`info` 5.5,
`danger-fg`/`danger` 5.8, `muted` on `raised` 5.5, `accent-text` on `accent-hint` 9.6,
`timer-1` on `timer-track` 7.3, `timer-2` 7.6, `timer-3` 5.6, `timer-4` 4.6. A second Goldfish
recomputation matched these figures. The pair matrix the check script enforces
(for each theme that exists):

- >= 4.5: `primary`, `secondary`, `muted`, `accent-text`, `success`, `warning`, `danger`, `info`
  on each of `page`, `surface`, `raised`; `primary`/`muted` on `accent-hint`; each `*-fg` on its
  fill (including `accent-fg` on `accent` and `accent-hover`, `danger-fg` on `danger-hover`).
- >= 3: `ring` on `page`/`surface`/`raised`; `line-strong` on `page`/`surface`/`raised`/
  `accent-hint`; each `timer-N` on `timer-track`; `accent-text` on `accent-hint` (selected-option
  border, 9.6); `accent` on `page` and `surface` (3.4 / 3.05). The selected-card badge (`accent-fg` on
  `accent`, with the letter and a check icon) is identified by its content, so its 2.6:1 edge
  against `accent-hint` is accepted. An `accent` fill is never placed
  directly on `raised` without its text (2.5:1 there), because its label, not its edge, identifies
  it.
- Tints: each status color as text on its own `*-tint` >= 4.5 (measured 8.2 / 8.6 / 5.6 / 5.9);
  `primary` on each tint >= 4.5 (about 14).
- Medal colors are decorative badges and always paired with a rank number, so they are checked
  only against `surface` at >= 3.
- Composite check: `primary`/`secondary` text on `surface` at 60% over `page`.

The seconds label is always shown, so color is never the only timer signal. The light theme
must define its own timer values (yellow on a light track fails at 1.0:1).

### 3.4 Theme initialization, persistence and toggle (Phase 2)

- Each app's `index.html` gets a short inline script in `<head>` that runs before React: read
  `localStorage["buzzer-theme"]` inside try/catch; if it is `"light"` or `"dark"` use it,
  otherwise use `matchMedia("(prefers-color-scheme: light)")`; set
  `document.documentElement.dataset.theme`. While no choice has been saved it also listens for
  OS preference changes and updates `data-theme`; saving a choice stops that. This prevents a
  flash of the wrong theme and makes "follows the OS" literally true. If storage is unavailable
  the in-memory choice still stops the OS listener for the session. A `storage` event listener
  syncs the theme to other open tabs of the same origin. The script also updates
  `<meta name="theme-color">` using two fixed hex values written in the script (marked
  `theme-exempt`; `check-theme.mjs` verifies they equal `page` in each theme, so they cannot
  drift). `viewport-fit=cover` is **not** used, so browsers keep content inside the safe area
  and no `env(safe-area-inset-*)` handling is needed. The script exposes
  `window.buzzerTheme = { set(theme) }`: `set` writes `data-theme`, tries to persist
  `buzzer-theme`, stops the OS listener, and updates `theme-color`. The `storage` handler calls
  the same code path, so another tab's choice also stops this tab's OS listener. The three
  copies are identical and `check-theme.mjs` compares them. Because it needs light rules to exist, **the
  script and the toggle ship together in Phase 2** (not earlier), so Phase 0 changes no behavior
  and a light-OS user is never given an unstyled "light" attribute.
- A `ThemeToggle` component (new file in each app's `src/components/ui/`, three copies like the
  existing duplicated `ui` primitives) reads `data-theme` (and watches it with a `MutationObserver`, so its label stays correct after
  an OS or cross-tab change) and calls `window.buzzerTheme.set(...)` to flip it; it never touches
  storage itself. It is a real `<button>` with an accessible name that states the action
  ("Switch to light theme"), a `lucide-react` Sun/Moon icon, and a 48px square touch target. It is a new stateful
  component inside `components/ui/` (as `TimerBar` already is); `components/CLAUDE.md` is
  updated to describe it.
- The global rules in `theme.css` (`color-scheme`, `:focus-visible`, reduced motion) change
  native scrollbars, focus rings and motion, so they land in Phase 1 with the body-color swap;
  Phase 0 `theme.css` contains only the token variables.
- Mount points: Admin: footer of the sidebar in `admin/src/App.tsx` (also fixed top-right on the
  Admin login page, which has no sidebar). Host: one toggle rendered in `host/src/App.tsx` inside the router: fixed top-right on every
  route except the game routes (`/game/:code`), where it is fixed top-left (the End game button
  is top-right there). This also covers the quiz editor route (`/quizzes/:gameId/edit`) without
  editing `QuestionEditorPage`, which T7 is changing.
  Player: one toggle rendered in `player/src/App.tsx` inside the router, so every route
  (join, name, sign-in, past quizzes, game screens, connection-error screens) has it; the game
  banner offset below is applied by `GameLayout`. Final Host positions are confirmed visually.
- **[PROPOSED] Player layout rules.** Today the Player question screen puts the timer's seconds
  label at the top-right edge, so a fixed top-right toggle would overlap it. Reserve a 60px top
  strip (`pt-[3.75rem]`: 8px gap + 48px toggle + 4px) on every Player screen so the toggle never
  overlaps content. The toggle is a 48px square at `top: 0.5rem; right: 0.5rem`, `z-40`. The
  "Host disconnected" banner (`z-50`, currently `fixed top-0`) becomes a fixed single line of
  fixed height `2.25rem` (36px; text truncates, never wraps at 375px). While it shows,
  `GameLayout` sets `--banner-h: 2.25rem` on `<html>` (and removes it when hidden); the toggle's
  `top` and the reserved strip both add `var(--banner-h, 0px)`, so the banner is above the toggle
  and content is not covered. The top corner is the hardest thumb zone,
  which is intentional: it keeps the toggle away from the answer buttons so it is not hit by
  accident during a timed question. Verify at 375x667 and 375x812, with the on-screen keyboard
  open on the fill-in-the-blank screen.
- Motion: `theme.css` (Phase 1) includes `@media (prefers-reduced-motion: reduce)` with
  `*, *::before, *::after { transition: none !important; animation: none !important }` (except `.animate-spin`, so loading spinners keep working) and
  `button:active { transform: none !important }`; `!important` is needed because utility classes
  (`transition-*`, `active:scale-95`) otherwise win. The timer keeps updating (it conveys
  information) but without easing.
- Focus: `theme.css` (Phase 1) sets `*:focus-visible { outline: 2px solid rgb(var(--ring));
  outline-offset: 2px }`, and Phase 1 removes every `focus:outline-none` that has no replacement
  (they would otherwise beat the global rule). Replace per-component `focus:ring-*` / `focus:ring-offset-slate-900` (about 18 rules,
  plus any `outline-none` without a replacement) with `focus-visible` styles that use `ring` and
  a `page`-colored offset, so rings look right in both themes.

### 3.5 Migration of existing components

Raw-class counts today (palette utilities): host about 386, player about 215, admin about 123.
Colors used: `slate` 491, `indigo` 64, `red` 52, `green` 41, `amber` 21, a few others.

Mapping rule (applied mechanically, then reviewed visually):

| Current | Token |
|---|---|
| `bg-slate-900`, page/background hex | `bg-page` |
| `bg-slate-800`, `bg-slate-800/60` | `bg-surface`, `bg-surface/60` |
| `bg-slate-700`, `bg-slate-600` (fills, hover) | `bg-raised`; hover steps use `bg-raised` on `bg-surface` parents (no `bg-line`, see 3.2) |
| `border-slate-700` | `border-line`; `border-slate-600` and input/outline borders: `border-line-strong` |
| `text-slate-50/100/200`, `text-white` on dark | `text-primary` |
| `text-slate-300` | `text-secondary` |
| `text-slate-400/500/600` | `text-muted` (`slate-500` is below 4.5:1 on the dark page, so it must not survive) |
| `bg-indigo-600`, `hover:bg-indigo-500` | `bg-accent`, `hover:bg-accent-hover` |
| `text-indigo-300/400` | `text-accent-text` |
| `red-*`, `green-*`, `amber-*`, `yellow-*`, `blue-*` solid fills | `danger`, `success`, `warning`, `info` with their `*-fg` text; status-colored text uses the same token as text |
| tinted status boxes (`bg-red-900/30`, `border-red-500/50` etc.) | `bg-danger-tint` (and the other tints) with `text-danger`/`border-danger` |
| `text-white` on an accent fill | `text-accent-fg` |
| `placeholder-*`, scrims/overlays (`bg-black/50`), shadows, gradients, SVG `fill-*`/`stroke-*`, native `accent-*` | decided per case: placeholders use `placeholder-muted`; scrims use `bg-page/80`; shadows are removed in dark and defined in light (3.8); gradients are removed; SVG strokes use the matching text token |

Not mechanical, reviewed by eye: Player `QuestionPage` (`OPTION_COLORS`, `true_false`
buttons), Host/Player `GameOverPage` (podium colors map to `medal-1..3`), the Host word cloud
(its colored classes map to the status/accent tokens; the COMPLETENESS label `amber-400`
becomes `text-warning`), `TimerBar` thresholds. `theme-exempt` is allowed only for the QR
wrapper and the `theme-color` hex values.
Dynamically built class names (template literals such as the `TimerBar` bar color or
`OPTION_COLORS`) cannot be caught by a regex; the rule is **no color class may be assembled from
fragments** (`bg-${x}`); each colored branch uses a complete token class.

`transparent` and `current` are not palette colors and stay allowed. The QR code is exempt from
theming: its white wrapper (`bg-white` around `QRCodeSVG`) stays white
and is marked with a `theme-exempt` comment.

### 3.6 Verification tooling

Plain Node 20 ES modules, no dependencies, run from the repo root. `frontend/` has no
`package.json`, so two scripts are added to the **root** `package.json`: `check:theme` and
`check:colors`.

- `frontend/scripts/check-theme.mjs` (new): reads `theme.css` (channels), computes the pair
  matrix in 3.3 for each theme block present, and fails below threshold. It extracts the token
  names from `tailwind.preset.ts` with a regex (it does not execute TypeScript) and fails if the
  names differ from `theme.css`. It fails if the three inline scripts differ (when present) or if their `theme-color` hex
  values differ from `page` in each theme.
- `frontend/scripts/check-raw-colors.mjs` (new): scans the source text of `*/src/**/*.{ts,tsx,css}`
  and `*/index.html` for raw palette utilities (any Tailwind prefix followed by a palette name
  and shade, or `white`/`black`), hex/rgb/hsl literals, `fillStyle`/`strokeStyle` string
  literals, the opacity and `text-accent` rules in 3.2, except inside `theme.css`, inside
  comments, for `transparent`/`current`, and on lines containing `theme-exempt` (QR wrapper). In Phase 0 it runs in **report
  mode** (prints counts per file, exit code 0). From Phase 1 it fails, except for files listed
  in `frontend/scripts/raw-colors-allow.txt`: the allowlist holds T7/T8 files not yet converted
  (so the check can be green before T7/T8 merges) and must be empty before release.
  *(Superseded in section 11: the allowlist is now empty.)*
- Neither script is added to CI by this plan (T2 forbids changing the pipeline to bypass checks,
  and adding a stage needs teammate approval). They are run manually and before submission.
- Rendered-page accessibility audit (Phase 3, both themes, key screens): run axe via
  `npx @axe-core/cli` (not added as a dependency) or Lighthouse in the browser pane; the
  results are noted in the T10 evidence.
- Manual checks: all screens at 375px (Player), 1920px (Host), 1440px (Admin); Tab through each
  screen to see focus rings; reduced-motion on; nginx build at `http://localhost:8080` once.

### 3.7 Timer clock (optional, final) — done

Only after Phases 0-3 are done and verified. The dial replaces each app's `TimerBar` with the
same props (`totalSeconds`, `initialSeconds`, `paused`) so `QuestionPage` files do not change.
Remaining time is an arc that shrinks anticlockwise from 12 o'clock; the seconds number is inside
the dial. Color steps use percentages of the time limit (green above 75%, then `timer-2`,
`timer-3`, `timer-4`), not absolute seconds, so short questions do not flash through colors. The
sweep animates smoothly from the existing end-time logic; under reduced motion it updates once
per second. Sizes: small (about 56px) on Player, large on Host. Arc color against its track must
be >= 3:1 in both themes (same check as 3.6). Two copies, one per app, as today. Until the dial
exists, the bar uses `timer-1`, `timer-2`, `timer-4` at its existing thresholds (>60%, >30%).

Implemented: Host 120px and Player 56px dials. Because the unused `timer-3` token was removed earlier, the dial uses three steps (`timer-1` above 75%, `timer-2` above 40%, `timer-4` below), not four. The number pulses in the last 5 seconds.

### 3.8 Design foundations (R9 "deliberate design") **[PROPOSED]**

Concrete rules so Phase 3 is a checklist, not taste:

- **Type:** system sans stack (unchanged). Host question prompt: `clamp(2rem, 3.2vw, 3.75rem)`,
  weight 700, floor 32px; scrolls inside a max-height container when still too long. Host
  secondary text (counts, labels) >= 20px. Player body >= 16px, option text 18-20px, option
  letter badge 28px. Admin table text 14px minimum, headings 18/24px. Labels in uppercase only
  for small metadata (question number, status); everything else sentence case.
- **Spacing:** 4px grid (Tailwind default scale), page padding 16px (Player), 32px (Host),
  24px (Admin); card padding 16-24px; consistent 12/16/24 vertical rhythm between sections.
- **Radius:** 8px controls and chips, 12px cards and option cards, 9999px only for pills and
  the timer track. No mixed radii on one screen.
- **Elevation:** dark theme: flat surfaces separated by `line` borders, no shadows. Light theme:
  1px `line` border plus at most one soft shadow level on floating panels (QR panel, dialogs).
- **Color use:** one accent; status colors only for status (correct/locked/wrong/info); no
  gradients, glows or glassmorphism, no decorative emoji (keep lucide icons).
- **Motion:** 150ms ease-out for color/position changes, 200ms for the selected-option change;
  no looping or decorative animation; everything respects `prefers-reduced-motion`.
- **Player option card:** `surface` fill, 2px `line-strong` border, 12px radius, min height
  56px, letter badge on `raised`. Selected: `accent-text` border (9.6 on `accent-hint`),
  `accent-hint` fill, badge in `accent` with `accent-fg`, plus a check icon (so selection is not color-only). Locked/submitted:
  card keeps full text contrast with a lock icon and "Answer submitted" text, not just reduced
  opacity.
- **Small screens:** the option list scrolls vertically as it does today (up to eight options at
  375x667); no layout change, no two-column layout.
- **State inventory (Phase 3 checklist):** every screen is reviewed in loading, empty, error,
  disabled, hover, pressed, focus, modal/confirm, banner and read-only states, in each theme.
- **Touch and density:** Player tap targets >= 48px; Admin row height about 40px with 14px
  text; Host buttons large (>= 56px height) for the projector.

### 3.9 Screenshot and evidence plan

- Viewports: Host 1920x1080, Player 375x812, Admin 1440x900, captured with the browser pane.
- Key screens (before and after, each in dark and, once it exists, light): Host: login,
  courses, quizzes tab, lobby, question, results, game over. Player: join, name, lobby,
  question (multiple choice), feedback, game over, past quizzes. Admin: login, users, user
  detail, courses, guests. Plus the T7/T8 screens after they merge.
- Names: `before-<app>-<screen>.png`, `after-<app>-<screen>-<theme>.png`. Live-game screens are
  produced with `scripts/seed_demo.py` and `scripts/simulate_players.py --room <CODE>`.
- Prerequisites: Docker stack up, `python scripts/seed_demo.py` and `scripts/simulate_players.py`;
  demo and admin accounts come from the README (credentials are not repeated in this document).
- Only five "before" images exist today (host login, host question, player join, player
  question, admin users). The missing "before" shots **must be captured before any Phase 1
  migration touches those screens**, so they are a Phase 0 step (about 14 of the roughly 19 key screens are missing).

### 3.10 T7/T8 surfaces (canvas question, question editors, image management)

They are converted after the T7/T8 merge (see section 8) using the same rules: token classes only.
Two cases need extra care. A **canvas** draws with JavaScript colors, not Tailwind classes, so it
must read token values at draw time (`getComputedStyle(document.documentElement)
.getPropertyValue('--primary')` converted to `rgb(...)`) and redraw when `data-theme` changes
(observe the attribute). **Images** are never recolored; their frames and placeholders use
`surface`/`line`, and transparent images sit on `surface` so they remain legible in both themes.
If the merge slips, the fallback is to finish and verify the theme for every existing screen,
then convert the T7/T8 screens in a final pass.

## 4. Alternatives considered

| Alternative | Why it lost |
|---|---|
| Three separate token files, one per app | Fast but the apps would drift; "one shared vocabulary" was a stated goal. Shared file costs a `fs.allow` line and a tsconfig `include` per app. |
| Tailwind `dark:` variants in components | Doubles every color class and leaves raw palette names in components; tokens give both themes with one class. |
| Hex values in CSS variables | Breaks existing `/60` opacity modifiers unless channels are used. |
| Preset overrides `theme.colors` (now or after migration) | Silent failure: unknown classes emit nothing instead of erroring, and `white`/`black`/`transparent`/`current` disappear; extend mode plus the check script is safer. |
| Three-state toggle (light / dark / system) | More UI for little gain; "no saved choice follows the OS" already gives system behavior. |
| A shared npm workspace or TSX package for `ThemeToggle`/`TimerBar` | Adds build and install changes and cuts against the repo's deliberate "each app is independent" structure (`frontend/CLAUDE.md`). |
| Plain `.js` preset | TypeScript's strict mode errors on untyped JS imports; a `.ts` preset listed in `tsconfig.node.json` is simpler. |
| Palette options A (indigo), C (amber), D (blue) | B (teal) chosen by the user; others kept as previews in `docs/ui/`. |
| Migrate all ~720 classes now | Would conflict with the active T7/T8 work in the same files; deferred until that merge. |
| Hide the toggle on the Player question screen | Rejected by the user: players may change theme at any time. |

## 5. Risks and how each is handled

- **Shared files outside app roots break type-check or build:** `tsconfig.node.json` `include`,
  `fs.allow`, and a verification pass over `tsc --noEmit` (the CI job), `npm run build` and
  nginx in Phase 0 (3.1).
- **Stale generated `.js` configs shadow the `.ts` config:** caused by `tsc -b` emitting; fixed
  with `rootDir` + `outDir` (verified), and ignored on every machine by naming the config
  files explicitly (3.1, O6).
- **Raw classes merged after the theme render unstyled without any error:** extend mode plus
  `check-raw-colors` run before each merge; the author converts T7/T8 screens after his merge.
- **Token drift across apps:** one `theme.css` and one preset; `check-theme.mjs` verifies names.
- **Merge conflicts with T7/T8:** Phase 0 touches only new files plus small config edits
  (`tailwind.config.ts`, `vite.config.ts`, `tsconfig.node.json`, `main.tsx`); wide edits wait
  until the T7/T8 merge. New T7/T8 UI keeps arriving meanwhile; the theme will most likely merge to
  `main` first, and the T7/T8 screens are converted afterwards (not yet final, see O3).
- **Phase 2 (the main deliverable) must not wait on the T7/T8 merge:** the merge date is unknown, so
  Phase 1 starts on files his branch does not obviously touch, the check script has an allowlist
  so it can be green before he merges (3.6), the light palette is designed in parallel,
  and his screens are converted after he merges (O3).
- **Missed raw colors:** `check-raw-colors.mjs` (with the T7/T8 allowlist until he merges).
- **Contrast regressions:** `check-theme.mjs` enforces the matrix (3.3).
- **Toggle with no light theme:** inline script and toggle ship with the light palette (3.4).
- **Banner/toggle overlap on Player at 375px:** reserved strip, fixed banner height, safe-area
  insets, manual check (3.4).
- **Design drift (R9):** the 3.8 rules are the Phase 3 checklist.

## 6. Detailed implementation steps

Each phase ends with `tsc --noEmit` and `npm run build` for all three apps plus a manual look;
nothing is committed without the user's approval, in small commits whose messages explain why.

**Phase 0: infrastructure (no visual or behavior change)**
1. Delete the local stale `frontend/*/tailwind.config.js`, `vite.config.js` and `.d.ts`; set
   `rootDir`/`outDir`/`include` in each `tsconfig.node.json`; add the gitignore entries; rebuild
   and confirm nothing is emitted beside the sources; name the Tailwind and Vite config files
   explicitly (postcss config, npm scripts), testing the Vite `--config` part first.
2. Capture the missing "before" screenshots (3.9) into `docs/ui/`, from the current branch
   state (no frontend changes yet) via the browser pane.
3. Create `frontend/theme.css` (token variables only, dark values) and
   `frontend/tailwind.preset.ts` (plain object, extend mode, no imports). Do **not** change
   `index.css` body colors, add global rules, or touch any component yet.
4. In each app: add `presets` to `tailwind.config.ts`; add `"../tailwind.preset.ts"` to
   `tsconfig.node.json` `include`; add `server.fs.allow` to `vite.config.ts`; import
   `../../theme.css` in `main.tsx`.
5. Create `check-theme.mjs` and `check-raw-colors.mjs` and the two root npm scripts.
6. Verify: `cn()` handling of new names; three dev servers load the token CSS; `tsc --noEmit`
   per app; three production builds; nginx build under `/host/`, `/player/`, `/admin/` looks
   identical to before.
7. Phase 0 is **not** merged on its own: the full theme is completed first (decision O2).

**Phase 1: migration (after the T7/T8 merge; may begin earlier on untouched files)**
8. Add the global rules to `theme.css` (`color-scheme`, `:focus-visible`, reduced motion), swap
   `index.css` body colors to tokens, remove unreplaced `focus:outline-none`; remove `maximum-scale=1.0` and `user-scalable=no`
   from Player's viewport meta (O5); migrate each app's `components/ui/*` (button, card,
   input, TimerBar) first, adding `focus-visible` styles.
9. Migrate pages per app using the 3.5 mapping, including the new T7/T8 screens; apply the 3.8
   rules to the Player option cards and the Host prompt sizing. The three apps are independent
   and can run in parallel.
10. Run `check-raw-colors.mjs` until clean (it becomes a failing check here); take dark "after"
    screenshots into `docs/ui/`.

**Phase 2: light theme and toggle**
11. Design the light palette (previews in `docs/ui/palette-*.html` hold draft light values) and
    add `[data-theme="light"]` to `theme.css`; extend `check-theme.mjs` and pass. This step can
    start in parallel with Phase 1.
12. Add the inline script to each `index.html` and `ThemeToggle` to each app; mount per 3.4;
    Player banner offset; check 375px.
13. Verify both themes on every existing screen of every app, keyboard focus, reduced motion;
    light "after" screenshots into `docs/ui/`. T7/T8 screens are verified after they are
    converted (3.10). The light palette is accepted when it passes the same 3.3 matrix plus the
    rendered-page audit in 3.6.

**Phase 3: polish and evidence**
14. Apply the 3.8 checklist across all screens (type, spacing, radius, elevation, motion).
15. Run the rendered-page accessibility audit (3.6) on key screens in both themes.
16. Take before/after screenshots; update the
    affected context `CLAUDE.md` files in the same change: `frontend/CLAUDE.md` (it says
    "no shared package", now stale because of `theme.css`, the preset and `scripts/`), a new
    `frontend/scripts/CLAUDE.md`, and each app's `src/`, `components/`, `lib/`, `pages/` docs.

**Phase 4: optional timer clock (3.7)**, only if the user decides to do it.

## 7. Files touched

New: `frontend/theme.css`, `frontend/tailwind.preset.ts`, `frontend/scripts/check-theme.mjs`,
`frontend/scripts/check-raw-colors.mjs`, `frontend/scripts/raw-colors-allow.txt`,
`frontend/scripts/CLAUDE.md`, `{host,player,admin}/src/components/ui/ThemeToggle.tsx`,
`docs/ui/before-*.png` (missing ones), `docs/ui/after-*.png`.

Phase 0 edits: root `package.json` (two scripts), `.gitignore`, and per app: `postcss.config.js`,
`package.json` (npm scripts), `tailwind.config.ts`, `tsconfig.node.json`, `vite.config.ts`, `src/main.tsx`.

Phase 1-3 edits: every file with raw palette classes today, plus per-app `index.html` and
`index.css`. Host: `components/ui/{button,card,input,TimerBar}.tsx`; pages `LoginPage`,
`CoursesPage`, `QuestionEditorPage`; `course/{CourseLayout,QuizzesPage,RoomsPage,RosterPage,
SessionsPage}`; `game/{GameLayout,LobbyPage,QuestionPage,ResultsPage,GameOverPage}`. Player:
`components/QuestionRecap.tsx`, `components/ui/{button,card,input,TimerBar}.tsx`; pages
`JoinPage`, `NamePage`, `SignInPage`, `LoginPage`, `PastQuizzesPage`, `PastQuizDetailPage`;
`game/{GameLayout,LobbyPage,QuestionPage,FeedbackPage,ResultsPage,GameOverPage}`. Admin:
`App.tsx` (sidebar, toggle), `components/ui/{button,card,input}.tsx`; pages `LoginPage`,
`UsersPage`, `UserDetailPage`, `GuestsPage`, `CoursesPage`. Plus whatever T7/T8 add.

## 8. Decisions from the Goldfish follow-up, and what is still open

- **O1 (decided: yes).** Phase 0 changes nothing visible; `index.css` body colors move to Phase 1.
- **O2 (decided: no early merge of Phase 0 alone).** The full theme is finished first, so T7/T8
  code does not need token names yet. The theme as a whole may still merge before the T7/T8
  work (see O3).
- **O3 (partly decided).** The T7/T8 merge date is unknown. The theme should merge
  early. Phase 1 therefore begins on files the T7/T8 branch does not
  touch (check that branch's changed files first). The new T7/T8 screens are converted after that
  merge (not final). *Superseded in section 11: the parked screens were converted early.*
- **O4 (decided: yes, adjustable).** The 3.8 design foundations and the Player layout rules in
  3.4 (reserved 48px top strip, fixed-height banner) are accepted and may be changed later.
- **O5 (decided: remove the zoom block).** Phase 1 removes `maximum-scale=1.0` and
  `user-scalable=no` from Player's viewport meta (WCAG 1.4.4). Accepted cost: a player may
  zoom the game screen by accident.
- **O6 (decided: explicit config paths).** Stale generated configs are ignored by naming the
  config files explicitly; the cleanup script is only the fallback.
- **O7 (unknown for now).** The T7/T8 branch is local and not pushed, so the files it touches
  cannot be listed yet. *(Superseded in section 11: the allowlist is empty.)* Until then Phase 1 avoids files that T7/T8 will obviously edit (question
  editor, canvas question, image management, and the `QuestionPage` files) and starts with
  `components/ui/*` and clearly unrelated pages; re-check once his branch is visible.
- Still open: final B values after the user's UI adjustments and a preview on the real app; the
  light palette; exact Host toggle positions; whether to build the clock.

## 9. Definition of done

Both themes complete in all three apps (including T7/T8 screens); `check-theme.mjs` and
`check-raw-colors.mjs` pass; toggle works, follows the OS
until a choice is saved, persists across reloads and is shared across apps behind nginx; focus
rings visible in both themes; reduced motion respected; `tsc --noEmit` and `npm run build` pass
in all three apps; before/after screenshots for both themes in `docs/ui/`; `TODO.md` and context
`CLAUDE.md` files updated; nginx production build checked once.

## 10. Goldfish log

**Round 1** (on rev 1; the Goldfish found the build and contrast problems):
1. Shared files outside the app roots could break type-check or build → verified how the apps
   build (nginx serves local `dist`, CI runs `tsc --noEmit`), added the `tsconfig.node.json`
   changes and `fs.allow`.
2. Phase 2 depended on the T7/T8 merge with no plan → decisions O2/O3, parallel work, fallback.
3. Phase 0 "no visual change" contradicted the body-color swap → body swap moved to Phase 1.
4. Token set was incomplete → added `*-fg` tokens, `danger-hover`, distinct `raised`/`line`/track.
5. Contrast claims were wrong: `line-strong` was 2.2:1, not 3:1 → recomputed, new values.
6. Preset mechanics, `tailwind-merge`, and the check-script exemptions (QR code) were unspecified
   → written down in 3.1, 3.5 and 3.6; word cloud verified to have no JS colors.
7. Player toggle/banner layout, the R9 "deliberate design" plan, and the screenshot plan were
   thin → added the 48px strip, section 3.8 and section 3.9.
8. Inline-script edge cases, decided-vs-open contradictions, stale generated configs → added
   storage fallback, OS listener rules, the Phase 0 cleanup step.

**Round 2** (on rev 2):
1. Override mode fails silently and drops `white`/`black`/`transparent`/`current` → rejected
   override; the preset stays in extend mode and `check-raw-colors` is the enforcement (3.1).
2. The `tsconfig.node.json` fix was incomplete → tested in a throwaway copy: `include` +
   `rootDir` + `outDir` works and emits nothing beside the sources; the preset imports nothing;
   `fs.allow` uses relative paths because `@types/node` is absent (3.0, 3.1).
3. Stale generated configs on teammates' machines → explicit config paths (O6), cleanup script as fallback.
4. `timer-track` equalled `line`, and `line` had no `bg-` key → distinct `#233635`; `line` added
   to `backgroundColor`; bordered elements on `raised` use `line-strong`.
5. Phase 0 was not really "no change" (global focus, color-scheme, reduced motion) → those rules
   moved to Phase 1; Phase 0 `theme.css` holds variables only.
6. Contrast matrix holes: selected border 2.6:1, `accent` on `raised` 2.5:1, no `danger-hover`
   value → selected border uses `accent-text` (9.6), matrix extended, `danger-hover` `#fca5a5`.
   The Goldfish's recomputation of all other 3.3 figures matched.
7. Reduced-motion had no mechanism, `focus:outline-none` could beat the global rule → `!important`
   rules and removal of unreplaced `outline-none` (3.4).
8. Inline script gaps (storage failure, cross-tab, `viewport-fit`, `theme-color`) → specified.
9. T7/T8 canvas and image theming had no plan → section 3.10.
10. Player 375x667 fit and Player toggle mount points → list scrolls as today; one toggle in
    `player/src/App.tsx`.
11. `ThemeToggle` "only file with logic" was wrong (`TimerBar` has logic) → reworded.
12. No state inventory, screenshot prerequisites missing → added to 3.8 and 3.9.
13. File naming, and PROPOSED vs decided status → clarified in the header.
- Still open after round 2: O7 (the T7/T8 branch is local, unknown for now),
  the light palette, final B values, whether to build the clock.

**Round 3** (on rev 3; the Goldfish confirmed every recomputed contrast figure, found no missing
requirement, and said Phase 0 could start; the user chose to stop after this round):
1. `bg-line` failed its own text bar (`muted` on `line` 4.43:1) → `line` is no longer a
   background token; hover steps use `raised` (3.2, 3.5).
2. `text-accent` compiled but is only 3.4:1 → rule to use `accent-text`, and `check-raw-colors`
   flags `text-accent`.
3. "No text opacity" and "translucent fills only over `page`" had no enforcement → added to
   `check-raw-colors`.
4. Tinted status boxes (`bg-red-900/30`) had no token → `*-tint` tokens with measured contrast.
5. Player toggle geometry did not add up (52px toggle against a 48px strip, safe-area handling,
   unspecified banner height, 44px against the 48px rule) → 48px toggle, 60px strip, 36px
   banner with `--banner-h`, and `viewport-fit=cover` dropped so no safe-area handling is needed.
6. The inline script and `ThemeToggle` had no contract → `window.buzzerTheme.set`, the toggle
   observes `data-theme`, the `storage` path shares one code path.
7. `theme-color` conflicted with the hex scanner → two fixed hex values marked `theme-exempt`
   and verified against `page` by `check-theme`.
8. No toggle on the Host editor route → Host toggle moved to `host/src/App.tsx`.
9. The Phase 1 gate depended on the T7/T8 merge; O2 and O3 read as contradictory → allowlist file,
   clarified wording.
10. Podium, medal and word-cloud colors had no policy → `medal-1..3` tokens, mapping stated,
    `theme-exempt` limited to the QR wrapper and `theme-color`.
11. Token-to-key map and variable naming were incomplete → explicit table in 3.1, `--<token>`
    naming, one combined dark selector.
12. Smaller: reduced motion keeps `.animate-spin`; removal of the Player zoom block is a numbered
    Phase 1 step; `fillStyle`/`strokeStyle` literals scanned; selected-badge contrast accepted
    with reasons; stale `frontend/CLAUDE.md` and a new `frontend/scripts/CLAUDE.md` added to the
    Phase 3 doc updates; rendered-page accessibility audit added; light-palette acceptance
    criteria stated.
- Answers to the Goldfish's readiness questions: section 3.3 is authoritative over
  `docs/ui/palette-B-teal.html`; "before" shots come from the current branch state via the
  browser pane; the `cn()` check is a throwaway script that is not committed.
- No further Goldfish rounds are planned; remaining risks are tracked in section 5 and the open
  items in section 8.

## 11. Implementation log and deviations

**What was built, by phase**
- Phase 0: `frontend/theme.css` (RGB-channel variables), `frontend/tailwind.preset.ts`, `frontend/scripts/check-theme.mjs` and `check-raw-colors.mjs`, root `check:theme` / `check:colors` scripts.
- Phase 1: shared UI primitives and all pages in Host, Player and Admin moved to token classes; Player zoom block removed; explicit `vite.config.ts` / `tailwind.config.ts` in npm scripts and `postcss.config.js`; `tsconfig.node.json` `rootDir`/`outDir`; `server.fs.allow` for `theme.css`.
- Phase 2: dark (default) and light teal "option B" palettes; identical inline theme script in each `index.html` (key `buzzer-theme`, OS-following until a choice is saved, `storage` sync, `window.buzzerTheme.set`, `theme-color` update); `ThemeToggle` copy in each app (Admin sidebar footer plus login top-right; Host top-right, top-left on `/game/*`; Player fixed top-right with reserved strip and banner offset variable).
- Phase 3: polish per 3.8 (controls `rounded-lg`, cards `rounded-xl`, single `shadow-card` elevation, focus-visible ring recipe, lucide icons instead of decorative emoji, Player body at least 16px), docs and context files updated.

**Deviations from the plan**
- The `buzzer-theme` localStorage key is per origin: host, player and admin on separate ports do not share the choice.
- Review cleanup: tokens nothing used (`info`, `info-fg`, `info-tint`, `medal-1..3`, `timer-3`, `success-fg`) were removed from `theme.css`, the preset and `check-theme`; the sections above that list them describe the original plan.
- `check-theme` skips non-color tokens (`--shadow-*`) for contrast and the `boxShadow` key in name parity; `--shadow-card` was added (`none` in dark, soft in light).
- The T7/T8 screens (Host `QuestionEditorPage`, Host `game/QuestionPage`, Host `game/ResultsPage`, Player `game/QuestionPage`, Player `game/ResultsPage`, Player `QuestionRecap`) were converted early, after the audit below, instead of after the T7/T8 merge. `raw-colors-allow.txt` is therefore empty; later T7/T8 edits must use token classes.
- Host prompt text uses clamp-based sizing so it reads from the back of a classroom.
- Player answer options are neutral cards, not saturated per-option colors.
- Disabled buttons use a muted label on a raised background instead of opacity.
- Lobby QR rule: the Host lobby hides the corner QR panel so the QR is shown once.
- Single-socket fix: Host and Player `GameLayout` keep one socket per game (fixes options staying locked after the host advances).
- Player zoom block (`maximum-scale`, `user-scalable=no`) removed, as decided in O5.

**Audit findings that drove the late changes**
- Light mode was broken on the parked screens.
- Host results labels were clipped.
- The metadata line was about 11px.
- Player options used saturated colors.
- Disabled buttons were nearly invisible.
- The Host lobby showed the QR twice.

**Still open**
- After-screenshots for both themes in `docs/ui/` (T7 screens, Player results, light Player question).
- T10 write-up (Yug: sections 1, 3, 4).
- Optional timer dial (3.7).
- T8 screens (images) are not started; theme them after they merge. T7 is merged.

**Rendered accessibility audit (done)**
- Tool: axe-core 4.13.0 (pinned; run from a temporary local file, nothing added to the repo or `package.json`).
- Rule sets: WCAG 2.0 A/AA, 2.1 A/AA, 2.2 AA and axe best practices. Run in light and dark on every screen below, with CSS transitions turned off. (Chrome pauses color transitions in background tabs, so a normal run can read half-switched colors and report false contrast failures.)
- Screens: Host login, courses, quizzes, rooms, roster, sessions, quiz editor, and a live game (lobby, question, results, game over); Player join, sign-in, name, and a live game (lobby, question, feedback, results, game over); Admin login, users, user detail, courses, guests.
- Found and fixed:
  - No `<main>` landmark on Host, Player and the Admin login page (moderate). Content is now inside `<main>`.
  - Icon-only buttons with no accessible name (critical): Host quiz Export and Archive, the quiz editor's move up/down buttons, and Admin course Edit. They now have `aria-label`.
  - Quiz editor move buttons were smaller than 24px (serious). They are now 24px square.
  - QR codes had no text alternative (serious). They now have a `title`.
  - Game screens had no level-1 heading (moderate). Host question and results and every Player screen except game over now have a screen-reader-only `h1`.
- Result after the fixes: zero violations on every screen above in both themes. The quiz editor still lists 12 "needs review" items for color contrast; axe says they contain only non-text characters (icon glyphs), so they are not failures.
- Not covered: keyboard-only walkthrough, screen reader testing, 200% zoom, and screens that need special data (empty states, error states, the Player past-quiz pages, and the Host game-over breakdown for many players).

## 12. Visual refresh

Status: phase 1 (shared foundation) built; pages are restyled in phase 2 with these parts.
The user asked for clearer visibility, better sizes and type, motion, glassmorphism, better
question cards, radii and buttons. This section **supersedes** the 3.8 rules on radius,
elevation, gradients and glass ("no gradients, glows or glassmorphism"). Every other T9 rule
stays: token classes only, both themes, the three identical inline scripts and `ThemeToggle`
copies, `npm run check:theme` and `npm run check:colors -- --strict` must pass.

### 12.1 Files

| File | What it holds |
|---|---|
| `frontend/theme.css` | Color tokens (both themes), themed non-color tokens (`--shadow-*`, `--alpha-*`), static motion tokens (`--dur-*`, `--ease-*`, `--blur-*`, `--stagger-step`), the page backdrop, `.glass*`, keyframes, `.stagger`, `.lift`, `.skeleton`, reduced-motion and reduced-transparency rules. |
| `frontend/tailwind.preset.ts` | Font families, type scale, radius scale, shadows, durations, easings, `animate-*` names, color classes. |
| `*/src/lib/utils.ts` | `cn` now uses `extendTailwindMerge` so the custom names merge correctly. |
| `*/src/components/ui/` | `button`, `card`, `input` (restyled, old API kept), new `badge`, `progress`, `spinner`, `motion.ts`; `TimerBar` restyled. |
| `*/src/index.css` | `body` is transparent so the backdrop shows; the page color sits on `<html>`. |
| `*/tailwind.config.ts` | The local `fontFamily` override is removed; fonts come from the preset. |

### 12.2 Tokens

**Color changes** (both themes, all checked): `line` is stronger (dark `#2f4645`, light
`#c4d6d3`) so borders hold on white and on glass; `muted` and `secondary` are a little stronger;
light `page` is `#ecf4f3` (was `#f4faf9`) so white cards separate from the page, and the three
`theme-color` values follow it; light `accent-text` is `#0d625c` and light `success` `#147036`,
`warning` `#a14806` so they hold 4.5:1 on the tinted backdrop.

**New color tokens:** `page-2` (backdrop gradient end), `track` (empty chart bar and progress
track: an inset well in dark, a grey-teal in light; every status fill is >= 3:1 on it), `glass`
(glass tint), `glass-edge` (hairline border), `glass-highlight` (inner top highlight),
`blob-1..3` (backdrop accent blobs). Pages should not use `glass*` and `blob-*` as classes; use
the `.glass*` utilities. **Fix for the Host Game Over histogram:** the empty bar uses
`bg-surface`, which is white on near-white in light. Change it to `bg-track`.

**Alpha tokens** (per theme): `--alpha-glass`, `--alpha-glass-strong`, `--alpha-glass-subtle`,
`--alpha-glass-edge`, `--alpha-glass-highlight`, `--alpha-blob`.

### 12.3 Typography

System stacks only (no downloads): `font-sans` (system-ui, SF, Segoe UI Variable, Roboto),
`font-display` (SF Pro Display first), `font-mono` (SF Mono, Cascadia, Roboto Mono). Each size
class sets size, line height, tracking and (mostly) weight:

| Class | Size | Use |
|---|---|---|
| `text-display` | clamp 44-88px, 800 | Host hero (winner, big counts) |
| `text-title` | clamp 32-56px, 700 | Page titles on Host and Player |
| `text-stage` | clamp 32-72px, 700 | Host question prompt (floor 32px) |
| `text-heading` | clamp 22-30px, 650 | Section and card titles |
| `text-subheading` | 18px, 600 | Sub-sections |
| `text-body-lg` / `text-body` | 18 / 16px, line height 1.6 | Text |
| `text-caption` | 13px, 500 | Meta lines (Admin, Player) |
| `text-label` | 12px, 600, tracking 0.08em | Small uppercase metadata; add `uppercase` |
| `text-stat` | clamp 36-64px, 800 | Scores and counts; add `tabular-nums` |
| `text-timer` | clamp 24-44px, 700 | Timers; add `font-mono tabular-nums` |
| `text-code` | clamp 40-80px, tracking 0.14em | Room codes; add `font-mono` |

Rules: scores, counts and timers always use `tabular-nums`. Host secondary text >= 20px
(`text-xl`). Player body >= 16px. Old size classes (`text-xl`, ...) still work.

### 12.4 Radius and elevation

Radius (overrides Tailwind's values): `rounded-sm` 6px, `md` 8px, `lg` 12px (controls),
`xl` 16px (cards), `2xl` 20px (glass and question cards), `3xl` 28px (hero panels),
`rounded-pill` (badges, tracks). Elevation: `shadow-soft` (resting control), `shadow-raised`
(card), `shadow-float` (hover, popover, QR panel), `shadow-overlay` (dialog), `shadow-glow`
(primary hover), `shadow-button` (inner highlight on filled buttons). Dark shadows are deeper so
they read. `shadow-card` is kept for old code (none in dark).

### 12.5 Glass

1. Use `.glass` (default), `.glass-strong` (question cards, dialogs, sticky bars) or
   `.glass-subtle` (chips, secondary panels), plus a radius and padding:
   `<div className="glass rounded-2xl p-6">`, or `<Card variant="glass">`.
2. Do not add `bg-*` or `shadow-*` on a glass element. They replace the tint or the highlight.
3. Do not paint a full-screen opaque `bg-page` wrapper. It hides the backdrop
   (`body::before`: page to page-2 gradient plus three blobs), and glass then has nothing to blur.
4. Text on glass may use `text-primary`, `secondary`, `muted`, `accent-text` and the status
   colors. `check-theme` proves >= 4.5:1 for each, on each glass level and on the bare backdrop,
   in both themes, over the worst of 20 backdrop colors (both gradient ends, each blob at its
   peak, and each pair of blobs overlapping). Blur only averages these colors.
5. Fallbacks: without `backdrop-filter` support, or with `prefers-reduced-transparency: reduce`,
   glass becomes solid `surface`; with reduced transparency the blobs are also removed.

### 12.6 Motion

Durations `duration-instant` 90ms, `fast` 150ms, `base` 220ms, `slow` 360ms, `slower` 600ms;
easings `ease-smooth` (enter/settle), `ease-swap` (between two states), `ease-spring` (small
overshoot). Animations: `animate-fade-in`, `animate-slide-up`, `animate-slide-down`,
`animate-scale-in`, `animate-pop` (entrances, all with fill `both`), `animate-pulse-soft`
(timer under 30%, live dots), `animate-shimmer` / `.skeleton` (loading), `animate-tick` (a
number that changes: render it with `key={value}` so it replays), `animate-shake` (wrong answer).

Stagger: an entrance animation waits `var(--i) * --stagger-step` (55ms). Put `.stagger` on a
list to number its first 12 children, or use `staggerStyle(i)` from `components/ui/motion.ts`.
Hover lift: `.lift` on a clickable card (rises 2px, gains `shadow-float`, presses to 0.98).
Buttons lift 1px and press to 0.98 through `motion-safe:` variants.

Rules: motion explains a change (enter, select, result, time running out). Looping animation is
allowed only for the low-timer pulse, loading shimmer and spinners, and waiting indicators (the
live dot, the Player lobby waiting dots); every other animation runs once. Under `prefers-reduced-motion: reduce` every
animation and transition is off (spinners still turn), hover lift and press scale are off,
and every element shows its final state.

### 12.7 Primitives

- `Button`: variants `primary`, `secondary`, `outline`, `ghost`, `danger`, `glass` (`default` and
  `destructive` still work); sizes `sm`/`md`/`lg`/`xl` with a minimum height per app (Player
  sizes are all >= 44px); `loading` shows a spinner, keeps width and color, sets `aria-busy`
  and blocks clicks; `forwardRef`.
- `Card`: variants `default` (unchanged look), `raised`, `glass`, `glass-strong`, `glass-subtle`,
  `interactive` (lift), `question` (glass-strong with a 6px colored edge: `tone` accent,
  success, warning or danger; `edge` top or left). New `CardTitle`, `CardDescription`,
  `CardFooter`.
- `Input`: taller hit area, border turns `ring` color plus a 2px ring on focus,
  `aria-invalid="true"` turns the border `danger`; `forwardRef`. `fieldClassName` gives
  `<select>` and `<textarea>` the same look.
- `Badge`: tones `neutral`, `accent`, `success`, `warning`, `danger`, `outline`, `solid`; sizes
  `sm`/`md`/`lg`. Use for question type and grading mode.
- `Progress`: `value`, `max`, `tone`, `size`, required `label`; on `bg-track`.
- `Spinner`: current-color ring; `label` adds screen-reader text.
- `TimerBar` (Host, Player): pill track, larger mono number, which turns `danger` and pulses
  under 30%. Props unchanged.

### 12.8 Check changes

`check-theme.mjs`: non-color tokens are recognized by prefix (themed `shadow-`/`alpha-` must
exist in both themes; static `dur-`/`ease-`/`blur-`/`stagger-` live in one `:root` block);
new pairs `accent-text` on `accent-hint` (4.5, Badge) and `accent`/status on `track` (3); the
glass section above; the preset check now reads only the color keys of the 3.1 table and fails
if another key maps a color variable.
