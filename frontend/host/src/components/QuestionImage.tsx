import { useState } from 'react';
import { ImageOff } from 'lucide-react';
import { cn } from '../lib/utils';
import type { ImageUrls } from '../types/game';

// T8 images on the game screens and the editor (docs/plans/t8-images.md §10). Images are
// framed on a surface and never recolored (T9 §3.10). An id with no URL in the map (its row
// was removed) or an image that fails to load shows "Image unavailable", never a broken icon.

/** The URL for an image id from a payload's map, or undefined when it has none. */
export function urlFor(urls: ImageUrls | undefined, id: number | null | undefined): string | undefined {
  return id == null ? undefined : urls?.[String(id)];
}

interface Props {
  /** The referenced image id; nothing is rendered when it is null or undefined. */
  id: number | null | undefined;
  urls: ImageUrls | undefined;
  alt: string;
  /** Size limits, e.g. `max-h-64 w-full`; the image keeps its aspect ratio inside. */
  className?: string;
  /** The live question loads at once; everything else lazily. */
  eager?: boolean;
}

export function QuestionImage({ id, urls, alt, className, eager = false }: Props) {
  const url = urlFor(urls, id);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  if (id == null) return null;
  if (!url || failedUrl === url) {
    return (
      <div
        role="img"
        aria-label="Image unavailable"
        className={cn(
          'flex min-h-16 items-center justify-center gap-2 rounded-xl border border-line bg-surface px-3 py-4 text-sm text-muted',
          className,
        )}
      >
        <ImageOff size={16} aria-hidden="true" className="shrink-0" />
        Image unavailable
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      onError={() => setFailedUrl(url)}
      className={cn('rounded-xl border border-line bg-surface object-contain', className)}
    />
  );
}
