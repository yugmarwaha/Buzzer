import { useCallback, useEffect, useId, useState } from 'react';
import { History, ImageOff, ImagePlus, Upload, X } from 'lucide-react';
import { api, errorMessage } from '../lib/api';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
import { Spinner } from './ui/spinner';
import type { ImageItem, QuestionImages } from '../types/host';

// T8 image picker for the question editor (docs/plans/t8-images.md §10). It offers only the
// course's *current* images (a new reference must be current, §5) plus "upload new". A
// reference the question already holds to an older version is shown with an "Older version"
// note and stays valid until the host picks another image.

export const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif';
const MAX_IMAGE_BYTES = 5 * 1_048_576;

/** Size problem a file would get from the server, checked before uploading; or null. */
export function fileProblem(file: File): string | null {
  return file.size > MAX_IMAGE_BYTES ? `${file.name} is larger than 5 MiB` : null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1_048_576).toFixed(1)} MB`;
}

export interface ImageLibrary {
  images: ImageItem[];
  loading: boolean;
  error: string;
  /** Upload to the course; the new image joins the list and is returned. */
  upload: (file: File) => Promise<ImageItem>;
}

/** The course's current images, loaded once per editor page. */
export function useImageLibrary(courseId: number | null): ImageLibrary {
  const [images, setImages] = useState<ImageItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (courseId === null) return;
    let ignore = false;
    setLoading(true);
    api
      .get<ImageItem[]>(`/host/courses/${courseId}/images`)
      .then((list) => { if (!ignore) { setImages(list); setError(''); } })
      .catch((err) => { if (!ignore) setError(errorMessage(err, 'Could not load images')); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [courseId]);

  const upload = useCallback(
    async (file: File) => {
      const problem = fileProblem(file);
      if (problem) throw new Error(problem);
      const form = new FormData();
      form.append('file', file);
      const item = await api.postForm<ImageItem>(`/host/courses/${courseId}/images`, form);
      setImages((prev) => [item, ...prev]);
      return item;
    },
    [courseId],
  );

  return { images, loading, error, upload };
}

/** What a selected id shows: the library copy, the question's older version, or nothing. */
function resolve(id: number, library: ImageLibrary, known?: QuestionImages) {
  const item = library.images.find((img) => img.id === id);
  if (item) return { url: item.url, name: item.filename, older: false };
  const ref = known?.[String(id)];
  if (ref) return { url: ref.url, name: 'Image', older: !ref.current };
  return null;
}

/** Small framed thumbnail of a selected image (or "Image unavailable"). */
export function ImageThumb({
  id,
  library,
  known,
  className,
}: {
  id: number;
  library: ImageLibrary;
  known?: QuestionImages;
  className?: string;
}) {
  const found = resolve(id, library, known);
  if (!found) {
    return (
      <span
        role="img"
        aria-label="Image unavailable"
        className={cn('inline-flex items-center justify-center rounded-lg border border-line bg-surface text-muted', className)}
      >
        <ImageOff size={16} aria-hidden="true" />
      </span>
    );
  }
  return (
    <img
      src={found.url}
      alt={found.name}
      loading="lazy"
      className={cn('rounded-lg border border-line bg-surface object-contain', className)}
    />
  );
}

/** Panel: pick one of the course's current images, upload a new one, or remove. */
export function ImageChooser({
  library,
  value,
  onPick,
  onClose,
  label,
}: {
  library: ImageLibrary;
  value: number | null;
  onPick: (id: number | null) => void;
  onClose: () => void;
  label: string;
}) {
  const inputId = useId();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const item = await library.upload(file);
      onPick(item.id);
      onClose();
    } catch (err) {
      setError(errorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div role="group" aria-label={label} className="animate-fade-in space-y-3 rounded-xl border border-line bg-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-sm font-semibold text-secondary">{label}</p>
        <label
          htmlFor={inputId}
          className={cn(
            'inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-sm font-semibold text-primary',
            'transition-colors duration-fast ease-smooth hover:bg-raised',
            'focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-page',
            uploading && 'pointer-events-none text-muted',
          )}
        >
          {uploading ? <Spinner /> : <Upload size={14} aria-hidden="true" />}
          {uploading ? 'Uploading…' : 'Upload new'}
          <input
            id={inputId}
            type="file"
            accept={IMAGE_ACCEPT}
            className="sr-only"
            disabled={uploading}
            onChange={(e) => { void handleFile(e.target.files?.[0]); e.target.value = ''; }}
          />
        </label>
        {value !== null && (
          <Button type="button" variant="ghost" size="sm" onClick={() => { onPick(null); onClose(); }}>
            Remove image
          </Button>
        )}
        <button
          type="button"
          aria-label="Close image list"
          onClick={onClose}
          className="inline-flex size-9 items-center justify-center rounded-lg text-secondary transition-colors duration-fast hover:bg-surface hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {error && <p role="alert" className="text-sm font-semibold text-danger">{error}</p>}
      {library.error && <p className="text-sm text-danger">{library.error}</p>}
      {library.loading ? (
        <p className="flex items-center gap-2 text-sm text-secondary"><Spinner /> Loading images…</p>
      ) : library.images.length === 0 ? (
        <p className="text-sm text-muted">No images in this course yet. Upload one.</p>
      ) : (
        <ul className="grid max-h-72 grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-5">
          {library.images.map((img) => (
            <li key={img.id}>
              <button
                type="button"
                aria-pressed={img.id === value}
                title={img.filename}
                onClick={() => { onPick(img.id); onClose(); }}
                className={cn(
                  'flex w-full flex-col gap-1 rounded-lg border p-1 text-left transition-colors duration-fast ease-smooth',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  img.id === value ? 'border-accent bg-accent-hint' : 'border-line bg-surface hover:border-line-strong',
                )}
              >
                <img src={img.url} alt="" loading="lazy" className="aspect-square w-full rounded-md object-contain" />
                <span className="truncate text-caption text-secondary">{img.filename}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Full-width control: the chosen image (or "No image") and a button to change it. */
export function ImagePicker({
  label,
  value,
  onChange,
  library,
  known,
}: {
  label: string;
  value: number | null;
  onChange: (id: number | null) => void;
  library: ImageLibrary;
  known?: QuestionImages;
}) {
  const [open, setOpen] = useState(false);
  const found = value !== null ? resolve(value, library, known) : null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        {value !== null ? (
          <ImageThumb id={value} library={library} known={known} className="size-16" />
        ) : (
          <span className="inline-flex size-16 items-center justify-center rounded-lg border border-dashed border-line-strong text-muted">
            <ImagePlus size={20} aria-hidden="true" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-secondary">{label}</p>
          <p className="truncate text-caption text-muted">
            {value === null ? 'No image' : found ? found.name : 'Image unavailable'}
          </p>
          {found?.older && (
            <p className="mt-0.5 inline-flex items-center gap-1 text-caption font-semibold text-warning">
              <History size={12} aria-hidden="true" /> Older version: pick the current image to update it
            </p>
          )}
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <ImagePlus size={14} aria-hidden="true" /> {value === null ? 'Add image' : 'Change'}
        </Button>
      </div>
      {open && (
        <ImageChooser library={library} value={value} onPick={onChange} onClose={() => setOpen(false)} label={label} />
      )}
    </div>
  );
}
