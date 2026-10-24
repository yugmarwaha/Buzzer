import type { CSSProperties } from 'react';

/**
 * Delays an entrance animation (animate-fade-in, -slide-up, -slide-down, -scale-in, -pop) by
 * `index` stagger steps (--stagger-step in theme.css). Example:
 *   <li className="animate-slide-up" style={staggerStyle(i)}>
 * A list with the .stagger class numbers its first 12 children without this helper.
 */
export function staggerStyle(index: number): CSSProperties {
  return { '--i': Math.max(0, Math.min(index, 20)) } as CSSProperties;
}
