# components/charts

## Purpose
Host-screen charts in React + inline SVG (no chart library). Presentation only: props are plain
arrays and numbers; the pages adapt game state into them. Import from `components/charts` (barrel `index.ts`).

## Contents
- `ScoreHistogram` — final scores in about 8 equal-width bins (also exports `buildScoreBins`). Rounded columns that grow in staggered, value at each tip, empty bins as a baseline stub plus a muted "0", average and high-score marker lines, hover/focus tooltip, hidden data table.
- `AnswerBars` — horizontal per-option bars. Letter badge shown once, then the option text, count and percent. The correct option has the success fill, a check icon and the word "Correct". Zero rows keep a stub; the "no answers" message shows when the total is 0.
- `QuestionAccuracyChart` — one column per question (percent correct, or percent answered for participation). "!" over accuracy below `threshold`. Hover shows a tooltip; click/Enter/Space pins a details panel. Also exports `questionPercent`.
- `DonutStat` — ring meter with the percent in the center and an animated sweep.
- `BandBar` — one stacked bar for closest_guess: full / partial / miss (+ optional no answer), counts in the legend and tooltip.
- `Sparkline` — small trend line with an end value; hover or arrow keys move a crosshair.
- `shared.tsx` — `tok()` token colors, `MARK` color roles, the keyframes `<style>`, `useWidth`, tooltip, hidden table, swatch.

## Props

| Component | Props |
|---|---|
| `ScoreHistogram` | `scores: number[]`, `maxPossibleScore: number`, `height?`, `title?`, `className?` |
| `AnswerBars` | `bars: { key, letter?, label, count, correct?, media? }[]` (`media`: T8 option image node shown between the letter and the text), `total: number`, `size?: 'lg' \| 'md'`, `wrongTone?: 'neutral' \| 'danger'`, `emptyMessage?`, `title?`, `className?` |
| `QuestionAccuracyChart` | `questions: { id, number, prompt, kind: 'accuracy' \| 'participation', answered, total, correct? }[]`, `height?`, `threshold?: number \| null` (default 50), `title?`, `className?` |
| `DonutStat` | `value`, `total`, `label`, `size?` (160), `tone?: 'primary' \| 'correct' \| 'participation'`, `detail?`, `className?` |
| `BandBar` | `full`, `partial`, `miss`, `unanswered?`, `height?` (24), `title?`, `className?` |
| `Sparkline` | `values: number[]`, `labels?: string[]`, `title`, `height?` (64), `className?` |

## Example

```tsx
import { ScoreHistogram, AnswerBars, DonutStat } from '../../components/charts';

<ScoreHistogram scores={gameOver.scores} maxPossibleScore={gameOver.maxPossibleScore} />
<AnswerBars
  total={results.totalAnswered}
  bars={options.map((opt, i) => ({
    key: String(i), letter: 'ABCDEFGH'[i], label: opt,
    count: results.answerDistribution[String(i)] ?? 0,
    correct: reveal.type === 'multiple_choice' ? reveal.correctIndices.includes(i) : null,
  }))}
/>
<DonutStat value={item.totalAnswered} total={item.totalPlayers} label="Response rate" />
```

## Rules and gotchas
- Colors only through `tok('<token>')` (gives `rgb(var(--token))`) or token classes. No hex, `rgb(` literals or named colors: `npm run check:colors -- --strict` must pass.
- Mark roles (`MARK`): `accent-text` for data, `success`/`warning`/`danger` for correct/partial/miss, `line-strong` for wrong or neutral bars. Each holds 3:1 against surface, raised and page in both themes. Text stays in `primary`/`secondary`/`muted`, never in a mark color.
- Motion is CSS only (`bz-grow-y`, `bz-grow-x`, `bz-sweep`, `bz-fade` in `shared.tsx`). `prefers-reduced-motion: reduce` turns all of it off.
- SVG charts measure their width with `useWidth` (ResizeObserver) and draw in real px, so the fonts stay at 16–20px on a projector.
- Each chart includes a visually hidden table (wrapped in an `sr-only` `div`: a table ignores the 1px width of `sr-only` and would cause horizontal scroll) or sr-only text. Keyboard: bars and segments are focusable (Tab) and show the same tooltip as hover.
