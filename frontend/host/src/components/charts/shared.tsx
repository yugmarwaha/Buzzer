import { useLayoutEffect, useState, type ReactNode } from 'react';

// Shared helpers for the Host charts. Colors come only from theme tokens (frontend/theme.css):
// `tok('accent-text')` gives `rgb(var(--accent-text))`, which the browser resolves per theme,
// so the charts follow light/dark with no redraw. Motion lives in CHART_CSS below so these files
// work without any utility from the shared Tailwind preset.

/** CSS color for a theme token, with optional alpha (0..1). */
export function tok(name: string, alpha?: number): string {
  return alpha === undefined ? `rgb(var(--${name}))` : `rgb(var(--${name}) / ${alpha})`;
}

/** Mark colors used by every chart. Each one holds 3:1 against surface, raised and page in both themes. */
export const MARK = {
  primary: 'accent-text', // the main data hue
  correct: 'success',
  partial: 'warning',
  wrong: 'danger',
  neutral: 'line-strong', // a de-emphasized bar (wrong option, miss) that still holds 3:1
  participation: 'warning',
} as const;

/** Keyframes and the reduced-motion switch. Rendered once per chart; duplicates are harmless. */
const CHART_CSS = `
@keyframes bz-chart-grow-y { from { transform: scaleY(0); } }
@keyframes bz-chart-grow-x { from { transform: scaleX(0); } }
@keyframes bz-chart-fade { from { opacity: 0; } }
@keyframes bz-chart-sweep { from { stroke-dashoffset: var(--bz-sweep-from); } }
.bz-grow-y { transform-box: fill-box; transform-origin: 50% 100%; animation: bz-chart-grow-y 700ms var(--ease-out, ease-out) both; }
.bz-grow-x { transform-origin: 0 50%; animation: bz-chart-grow-x 700ms var(--ease-out, ease-out) both; }
.bz-fade { animation: bz-chart-fade 400ms var(--ease-out, ease-out) both; }
.bz-sweep { animation: bz-chart-sweep 900ms var(--ease-out, ease-out) both; }
.bz-mark { transition: opacity 150ms ease, filter 150ms ease; }
.bz-hit { outline: none; cursor: default; }
.bz-hit:focus-visible .bz-focus { opacity: 1; }
.bz-focus { opacity: 0; transition: opacity 150ms ease; }
@media (prefers-reduced-motion: reduce) {
  .bz-grow-y, .bz-grow-x, .bz-fade, .bz-sweep { animation: none !important; }
  .bz-mark, .bz-focus { transition: none !important; }
}
`;

export function ChartStyles() {
  return <style>{CHART_CSS}</style>;
}

/** Stagger delay for the n-th mark, as an inline style. */
export function stagger(n: number, stepMs = 55): { animationDelay: string } {
  return { animationDelay: `${n * stepMs}ms` };
}

/** Width of an element in CSS px, kept current with a ResizeObserver.
 *  Returns a callback ref, so it follows the element even when it mounts later. */
export function useWidth<T extends HTMLElement>(fallback = 640): [(el: T | null) => void, number] {
  const [el, setEl] = useState<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => {
      const w = Math.floor(el.clientWidth);
      if (w > 0) setWidth(w);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, width];
}

export function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}

export function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/** Path for a bar with a rounded data end and a square baseline.
 *  `dir` is where the data end points: 'up' for columns, 'right' for horizontal bars. */
export function barPath(x: number, y: number, w: number, h: number, r: number, dir: 'up' | 'right'): string {
  if (w <= 0 || h <= 0) return '';
  if (dir === 'up') {
    const rr = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
  }
  const rr = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
}

/** Clean y-axis ticks from 0 to at least `max`, about `target` steps. */
export function niceTicks(max: number, target = 4): number[] {
  const top = Math.max(1, max);
  const raw = top / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const s = Math.max(1, step);
  const ticks: number[] = [];
  for (let v = 0; v < top + s; v += s) {
    ticks.push(v);
    if (v >= top) break;
  }
  return ticks;
}

/** Floating readout: value first (strong), label second. Absolutely placed inside a relative parent. */
export function ChartTooltip({ x, y, children, align = 'center' }: {
  x: number;
  y: number;
  children: ReactNode;
  align?: 'center' | 'left' | 'right';
}) {
  const shift = align === 'center' ? '-50%' : align === 'left' ? '0%' : '-100%';
  return (
    <div
      role="presentation"
      className="pointer-events-none absolute z-10 rounded-lg border border-line bg-surface px-3 py-2 shadow-raised bz-fade"
      style={{ left: x, top: y, transform: `translate(${shift}, calc(-100% - 10px))`, whiteSpace: 'nowrap' }}
    >
      {children}
    </div>
  );
}

/** Visually hidden data table, the screen-reader view of a chart. */
export function SrTable({ caption, headers, rows }: { caption: string; headers: string[]; rows: (string | number)[][] }) {
  return (
    // The wrapper is the visually hidden box. A table ignores the 1px width of `sr-only` and would
    // stick out of the page and cause horizontal scroll.
    <div className="sr-only">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>{headers.map((h) => <th key={h} scope="col">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => (j === 0 ? <th key={j} scope="row">{c}</th> : <td key={j}>{c}</td>))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Legend swatch: a small rounded rect in the mark color (text stays in text tokens). */
export function Swatch({ color }: { color: string }) {
  return <span aria-hidden="true" className="inline-block h-3 w-3 shrink-0 rounded-sm" style={{ backgroundColor: tok(color) }} />;
}
