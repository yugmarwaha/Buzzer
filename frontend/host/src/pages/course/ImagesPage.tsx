import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ImageIcon, RefreshCw, Trash2, Upload } from 'lucide-react';
import { api, errorMessage } from '../../lib/api';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Spinner } from '../../components/ui/spinner';
import { staggerStyle } from '../../components/ui/motion';
import { IMAGE_ACCEPT, fileProblem, formatBytes } from '../../components/ImagePicker';
import type { ImageItem } from '../../types/host';

/** T8 Images tab: the course's current images (docs/plans/t8-images.md §6, §7, §10).
 *  Replace makes a new version and points every non-archived quiz at it; delete is refused
 *  (409, with the quizzes named) while any quiz still uses the image. */
export default function ImagesPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const uploadRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<ImageItem | null>(null);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<number | 'upload' | null>(null);
  // Guards against a slow response for a previous course overwriting the current one.
  const latestCourse = useRef(courseId);

  async function load() {
    const id = courseId;
    try {
      const list = await api.get<ImageItem[]>(`/host/courses/${courseId}/images`);
      if (latestCourse.current === id) setImages(list);
    } catch (err) {
      if (latestCourse.current === id) setError(errorMessage(err, 'Failed to load images'));
    } finally {
      if (latestCourse.current === id) setLoading(false);
    }
  }

  useEffect(() => {
    latestCourse.current = courseId;
    setImages([]); setError(''); setNotice(''); setLoading(true);
    void load();
  }, [courseId]);

  async function run(key: typeof busy, fn: () => Promise<unknown>, fallback: string, done: string) {
    setBusy(key);
    setError('');
    setNotice('');
    try {
      await fn();
      setNotice(done);
      await load();
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setBusy(null);
    }
  }

  function withFile(file: File | undefined, fn: (form: FormData) => Promise<unknown>, key: typeof busy, fallback: string, done: string) {
    if (!file) return;
    const problem = fileProblem(file);
    if (problem) { setError(problem); setNotice(''); return; }
    const form = new FormData();
    form.append('file', file);
    void run(key, () => fn(form), fallback, done);
  }

  function upload(file: File | undefined) {
    withFile(file, (form) => api.postForm(`/host/courses/${courseId}/images`, form), 'upload', 'Upload failed', 'Image uploaded.');
  }

  function replace(file: File | undefined) {
    const target = replaceTarget.current;
    if (!target) return;
    withFile(
      file,
      (form) => api.putForm(`/host/images/${target.id}`, form),
      target.id,
      'Replace failed',
      `Replaced ${target.filename}. Quizzes that are not archived now use the new image.`,
    );
  }

  async function remove(img: ImageItem) {
    if (!confirm(`Delete ${img.filename}?`)) return;
    await run(img.id, () => api.delete(`/host/images/${img.id}`), 'Delete failed', `Deleted ${img.filename}.`);
  }

  if (loading) {
    return (
      <div aria-busy="true">
        <span className="sr-only">Loading images</span>
        <div className="skeleton h-9 w-40 rounded-lg" aria-hidden="true" />
        <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-56 rounded-2xl" aria-hidden="true" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="text-heading text-primary">Images</h2>
          <Badge tone="neutral" size="md" className="tabular-nums" aria-label={`${images.length} images`}>
            {images.length}
          </Badge>
        </div>
        <input
          ref={uploadRef}
          type="file"
          accept={IMAGE_ACCEPT}
          className="hidden"
          onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }}
        />
        <input
          ref={replaceRef}
          type="file"
          accept={IMAGE_ACCEPT}
          className="hidden"
          onChange={(e) => { replace(e.target.files?.[0]); e.target.value = ''; }}
        />
        <Button onClick={() => uploadRef.current?.click()} disabled={busy === 'upload'}>
          {busy === 'upload' ? <Spinner /> : <Upload size={16} aria-hidden="true" />}
          {busy === 'upload' ? 'Uploading…' : 'Upload image'}
        </Button>
      </div>
      <p className="text-body text-secondary">
        PNG, JPEG, WebP or still GIF, up to 5 MB and 4096 px a side. Anyone with an image's link can
        view it, so don't upload private material.
      </p>

      {error && (
        <p role="alert" className="animate-slide-down rounded-lg bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}
      {notice && !error && (
        <p role="status" className="animate-slide-down rounded-lg bg-success-tint px-4 py-3 text-body text-success">
          {notice}
        </p>
      )}

      {images.length === 0 ? (
        <div className="flex animate-scale-in flex-col items-center gap-2 rounded-2xl glass-subtle px-6 py-12 text-center">
          <ImageIcon size={32} className="text-muted" aria-hidden="true" />
          <p className="text-body-lg font-semibold text-primary">No images yet.</p>
          <p className="text-body text-secondary">Upload one here or from the question editor.</p>
        </div>
      ) : (
        <ul className="stagger grid grid-cols-1 gap-4 min-[420px]:grid-cols-2 md:grid-cols-3">
          {images.map((img, i) => {
            const uses = img.use_count + img.archived_use_count;
            return (
              <li
                key={img.id}
                className="flex animate-slide-up flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card"
                style={staggerStyle(i)}
              >
                <a href={img.url} target="_blank" rel="noreferrer" className="block bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                  <img src={img.url} alt={img.filename} loading="lazy" className="aspect-[4/3] w-full object-contain" />
                </a>
                <div className="flex flex-1 flex-col gap-3 p-4">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-primary" title={img.filename}>{img.filename}</p>
                    <p className="text-caption text-muted tabular-nums">
                      {formatBytes(img.size_bytes)} · {img.width} × {img.height}
                    </p>
                  </div>
                  <p className="text-sm text-secondary tabular-nums">
                    {uses === 0
                      ? 'Not used'
                      : `Used by ${img.use_count} question${img.use_count !== 1 ? 's' : ''}` +
                        (img.archived_use_count > 0 ? ` + ${img.archived_use_count} in archived quizzes` : '')}
                  </p>
                  <div className="mt-auto flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy !== null}
                      loading={busy === img.id}
                      onClick={() => { replaceTarget.current = img; replaceRef.current?.click(); }}
                      aria-label={`Replace ${img.filename}`}
                    >
                      <RefreshCw size={14} aria-hidden="true" /> Replace
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy !== null}
                      onClick={() => void remove(img)}
                      aria-label={`Delete ${img.filename}`}
                      title={uses > 0 ? 'Remove it from every quiz first' : 'Delete image'}
                    >
                      <Trash2 size={14} aria-hidden="true" /> Delete
                    </Button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
