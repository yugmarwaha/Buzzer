import { useEffect, useRef, useState } from 'react';

// Pin Drop grid (T7). The host app has a copy of this file; both must follow the same
// pixel <-> grid conversion (docs/plans/t7-new-question-types.md §6).

export interface GridPoint { x: number; y: number }

const MIN = -5;
const MAX = 5;
const STEPS = MAX - MIN; // 10 intervals
const MARGIN = 16; // CSS px around the grid so edge points are tappable

function clamp(v: number): number {
  return Math.min(MAX, Math.max(MIN, v));
}

/** CSS-pixel position inside a canvas of side `size` → nearest grid point. */
export function pointFromPixel(px: number, py: number, size: number): GridPoint {
  const g = (size - 2 * MARGIN) / STEPS;
  // `|| 0` turns Math.round's -0 into 0.
  return {
    x: clamp(Math.round((px - MARGIN) / g + MIN)) || 0,
    y: clamp(Math.round(MAX - (py - MARGIN) / g)) || 0,
  };
}

/** Theme tokens (frontend/theme.css) resolved to canvas colors for the current theme.
 *  Canvas can't use CSS variables, so read the channel triplets from the root element's computed
 *  style: one style read per draw, no inline styles written (T9 spec docs/plans/ui-themes.md §3.10). */
function tokenColors<K extends string>(names: Record<K, string>): Record<K, string> {
  const style = getComputedStyle(document.documentElement);
  const out = {} as Record<K, string>;
  for (const key of Object.keys(names) as K[]) {
    const channels = style.getPropertyValue(`--${names[key]}`).trim().split(/\s+/).join(', ');
    out[key] = `rgb(${channels})`;
  }
  return out;
}

/** Grid point → CSS-pixel position inside a canvas of side `size`. */
export function pixelFromPoint(p: GridPoint, size: number): [number, number] {
  const g = (size - 2 * MARGIN) / STEPS;
  return [MARGIN + (p.x - MIN) * g, MARGIN + (MAX - p.y) * g];
}

interface PinGridProps {
  /** Largest side in CSS px; the grid shrinks to fit narrower containers. */
  maxSize: number;
  selected?: GridPoint | null;
  target?: GridPoint | null;
  /** When given, taps select the nearest grid point. */
  onSelect?: (p: GridPoint) => void;
  /** T8: background image, drawn under the grid lines; a plain grid if it fails to load. */
  imageUrl?: string;
}

export function PinGrid({ maxSize, selected = null, target = null, onSelect, imageUrl }: PinGridProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState(0);
  // Bumped when the light/dark theme changes, so the canvas redraws with the new tokens.
  const [themeVersion, setThemeVersion] = useState(0);
  const [image, setImage] = useState<HTMLImageElement | null>(null);

  // Load the background; redraw once it arrives. A failed load leaves the plain grid.
  useEffect(() => {
    setImage(null);
    if (!imageUrl) return;
    let live = true;
    const img = new Image();
    img.onload = () => { if (live) setImage(img); };
    img.src = imageUrl;
    return () => { live = false; };
  }, [imageUrl]);

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeVersion((v) => v + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  // Resize/rotation: re-measure and redraw; points are kept in grid coordinates.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize(Math.floor(Math.min(el.clientWidth, maxSize)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [maxSize]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || size <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); // draw in CSS px
    const color = tokenColors({
      surface: 'surface',
      line: 'line',
      axis: 'line-strong',
      label: 'secondary',
      target: 'success',
      pin: 'accent',
      pinEdge: 'accent-fg',
    });
    // Background and frame are drawn here, not with CSS: a CSS border would skew the tap math.
    ctx.fillStyle = color.surface;
    ctx.fillRect(0, 0, size, size);
    ctx.strokeStyle = color.line;
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, size - 1, size - 1);

    // T8 (T7 spec §8): the image fits the grid square, centered, aspect ratio kept. Natural
    // sizes include EXIF rotation, unlike the stored width/height.
    if (image && image.naturalWidth > 0 && image.naturalHeight > 0) {
      const side = size - 2 * MARGIN;
      const w = image.naturalWidth;
      const h = image.naturalHeight;
      const s = Math.min(side / w, side / h);
      ctx.drawImage(image, MARGIN + (side - w * s) / 2, MARGIN + (side - h * s) / 2, w * s, h * s);
    }

    const g = (size - 2 * MARGIN) / STEPS;
    const lo = MARGIN;
    const hi = size - MARGIN;
    for (let i = 0; i <= STEPS; i++) {
      const pos = MARGIN + i * g;
      const isAxis = MIN + i === 0;
      const line = () => {
        ctx.beginPath();
        ctx.moveTo(pos, lo);
        ctx.lineTo(pos, hi);
        ctx.moveTo(lo, pos);
        ctx.lineTo(hi, pos);
        ctx.stroke();
      };
      // The axes get a surface-colored halo, so they stay visible over a background image
      // in either theme (a light image in dark mode hid them). Over the plain grid the halo
      // matches the background.
      if (isAxis) {
        ctx.strokeStyle = color.surface;
        ctx.lineWidth = 5;
        line();
      }
      ctx.strokeStyle = isAxis ? color.axis : color.line;
      ctx.lineWidth = isAxis ? 2 : 1;
      line();
    }

    // Axis labels: −5 and 5 at the ends of each axis, 0 at the origin. Each has the same
    // surface-colored halo as the axes.
    ctx.font = `${Math.max(10, Math.round(g * 0.32))}px sans-serif`;
    ctx.fillStyle = color.label;
    ctx.strokeStyle = color.surface;
    ctx.lineWidth = 3;
    ctx.lineJoin = 'round';
    const label = (text: string, x: number, y: number) => {
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
    };
    const [ox, oy] = pixelFromPoint({ x: 0, y: 0 }, size);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    label('−5', lo, oy + 3);
    label('5', hi, oy + 3);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    label('5', ox + 4, lo + 2);
    label('−5', ox + 4, hi - 2);
    ctx.textBaseline = 'top';
    label('0', ox + 4, oy + 3);

    if (target) {
      const [tx, ty] = pixelFromPoint(target, size);
      ctx.strokeStyle = color.target;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(tx, ty, g * 0.45, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (selected) {
      const [sx, sy] = pixelFromPoint(selected, size);
      ctx.fillStyle = color.pin;
      ctx.strokeStyle = color.pinEdge;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(sx, sy, g * 0.3, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }, [size, selected?.x, selected?.y, target?.x, target?.y, themeVersion, image]);

  function handlePointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!onSelect) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onSelect(pointFromPixel(e.clientX - rect.left, e.clientY - rect.top, rect.width));
  }

  return (
    <div ref={wrapRef} className="w-full flex justify-center">
      <canvas
        ref={canvasRef}
        onPointerDown={handlePointerDown}
        className="rounded-xl"
        style={{
          width: size,
          height: size,
          touchAction: onSelect ? 'none' : 'auto',
          cursor: onSelect ? 'pointer' : 'default',
        }}
        aria-label={onSelect ? 'Coordinate grid: tap to choose a point' : 'Coordinate grid'}
      />
    </div>
  );
}
