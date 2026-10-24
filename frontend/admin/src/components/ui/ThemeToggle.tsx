import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

type Theme = 'light' | 'dark';

declare global {
  interface Window {
    /** Defined by the inline script in index.html; owns persistence and the OS-preference listener. */
    buzzerTheme?: { set(theme: Theme): void };
  }
}

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

interface ThemeToggleProps {
  /** `icon`: a square button for corners. `row`: icon plus label, for menus. */
  variant?: 'icon' | 'row';
  className?: string;
}

export function ThemeToggle({ variant = 'icon', className = '' }: ThemeToggleProps) {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  // Follow changes made elsewhere (OS preference, another tab) so the label stays correct.
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  const next: Theme = theme === 'light' ? 'dark' : 'light';
  const Icon = theme === 'light' ? Moon : Sun;

  function toggle() {
    if (window.buzzerTheme) window.buzzerTheme.set(next);
    else document.documentElement.dataset.theme = next;
  }

  const base = 'inline-flex items-center justify-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page';
  const look =
    variant === 'icon'
      ? 'h-12 w-12 rounded-lg border border-line-strong bg-surface text-primary hover:bg-raised'
      : 'w-full gap-3 rounded-lg px-4 py-2.5 text-sm font-medium text-secondary hover:bg-raised hover:text-primary';

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className={`${base} ${look} ${className}`}
    >
      <Icon size={variant === 'icon' ? 20 : 16} aria-hidden="true" />
      {variant === 'row' && <span>{next === 'light' ? 'Light theme' : 'Dark theme'}</span>}
    </button>
  );
}
