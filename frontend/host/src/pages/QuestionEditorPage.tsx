import { useEffect, useId, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Crosshair,
  Download,
  ImagePlus,
  Lock,
  Pencil,
  Plus,
  Target,
  Trash2,
  Trophy,
} from 'lucide-react';
import { api } from '../lib/api';
import { cn, formatNum, optionDisplay } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input, fieldClassName } from '../components/ui/input';
import { Card } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Spinner } from '../components/ui/spinner';
import { staggerStyle } from '../components/ui/motion';
import { PinGrid } from '../components/PinGrid';
import { isNewType } from '../components/NewTypeResults';
import { ImageChooser, ImagePicker, ImageThumb, useImageLibrary, type ImageLibrary } from '../components/ImagePicker';
import { QuestionImage } from '../components/QuestionImage';
import type { ImageUrls, QuestionType } from '../types/game';
import type { QuestionImages } from '../types/host';
type GradingType = 'ACCURACY' | 'COMPLETENESS';

interface Question {
  id: number;
  type: QuestionType;
  grading_type: GradingType;
  prompt: string;
  config: Record<string, unknown>;
  answer_data: Record<string, unknown>;
  time_limit_seconds: number;
  points_value: number;
  order_index: number;
  /** Answered in some session: can't be deleted (results are kept). */
  has_answers: boolean;
  /** T8: url and current flag of each image this question references. */
  images?: QuestionImages;
}

interface Game {
  id: number;
  course_id: number;
  title: string;
  archived_at: string | null;
  has_active_room: boolean;
}

// ---- helpers for building config/answer_data per type ----

/** rowId: client-only, stable identity of an option row (never sent), so an image upload
 *  that finishes later updates the row it started for. */
interface McOption { text: string; points: number; imageId?: number | null; rowId?: number }

let lastRowId = 0;
const newRowId = () => ++lastRowId;

/** options, plus optionImageIds aligned with them when any option has an image (T8 §5). */
function optionsConfig(options: McOption[]): Record<string, unknown> {
  const ids = options.map((o) => o.imageId ?? null);
  return ids.some((id) => id !== null)
    ? { options: options.map((o) => o.text), optionImageIds: ids }
    : { options: options.map((o) => o.text) };
}

function buildMcPayload(options: McOption[], _grading: GradingType) {
  return {
    config: optionsConfig(options),
    answer_data: { answer_points: options.map((o) => o.points) },
  };
}

function buildTfPayload(truePoints: number, falsePoints: number) {
  return {
    config: {},
    answer_data: { answer_points: { true: truePoints, false: falsePoints } },
  };
}

function buildMsPayload(options: McOption[]) {
  return {
    config: optionsConfig(options),
    answer_data: { answer_points: options.map((o) => o.points) },
  };
}

interface FibAnswer { text: string; points: number }

function buildFibPayload(answers: FibAnswer[], editDistance: number) {
  return {
    config: {},
    answer_data: {
      acceptedAnswers: answers.map((a) => a.text),
      answerPoints: answers.map((a) => a.points),
      editDistance,
    },
  };
}

// ---- QuestionForm subcomponent ----

interface FormState {
  type: QuestionType;
  grading: GradingType;
  prompt: string;
  timeLimitSeconds: number;
  pointsValue: number;
  // multiple_choice
  mcOptions: McOption[];
  // true_false
  tfTruePoints: number;
  tfFalsePoints: number;
  // fill_in_the_blank
  fibAnswers: FibAnswer[];
  fibEditDistance: number;
  // multi_select
  msOptions: McOption[];
  // pin_drop (T7)
  pinX: number;
  pinY: number;
  // closest_guess (T7): kept as text so an empty field stays empty (Number('') is 0)
  cgCorrect: string;
  cgFull: string;
  cgPartial: string;
  cgUnit: string;
  // T8 images (ids): prompt image on every type, Pin Drop canvas background
  promptImageId: number | null;
  pinImageId: number | null;
}

const GRID_VALUES = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5];
const GUESS_LIMIT = 1e12;
const UNIT_MAX_LEN = 20;

/** A non-empty numeric field within the server's closest_guess bounds, or null. */
function parseGuessField(text: string): number | null {
  if (!text.trim()) return null;
  const v = Number(text);
  return Number.isFinite(v) && Math.abs(v) <= GUESS_LIMIT ? v : null;
}

/** T8 §5 option rule, checked before Save: once any option has an image, every option needs
 *  text or an image. Multi-select drops options with neither, so only multiple choice can fail. */
function imageProblem(form: FormState): string | null {
  if (form.type !== 'multiple_choice') return null;
  if (!form.mcOptions.some((o) => o.imageId != null)) return null;
  const i = form.mcOptions.findIndex((o) => !o.text.trim() && o.imageId == null);
  return i >= 0 ? `${optionDisplay('', i)} needs text or an image` : null;
}

/** Client-side check of the T7 server rules (design §5.6); null when the form can be saved.
 *  The server stays authoritative — this only explains problems create would 422 on. */
function newTypeProblem(form: FormState): string | null {
  if (!form.prompt.trim()) return 'Enter the question prompt';
  if (form.type === 'closest_guess' && [...form.cgUnit.trim()].length > UNIT_MAX_LEN) {
    return `Unit must be at most ${UNIT_MAX_LEN} characters`;
  }
  if (form.grading !== 'ACCURACY') return null;
  if (form.type === 'closest_guess') {
    const correct = parseGuessField(form.cgCorrect);
    const full = parseGuessField(form.cgFull);
    const partial = parseGuessField(form.cgPartial);
    if (correct === null) return 'Enter the correct value (a number up to 1e12)';
    if (full === null) return 'Enter the full-credit tolerance';
    if (partial === null) return 'Enter the partial-credit tolerance';
    if (full < 0) return 'Full-credit tolerance must be 0 or more';
    if (partial < full) return 'Partial-credit tolerance must be at least the full-credit tolerance';
  }
  if (form.pointsValue <= 0) return 'Points must be greater than 0';
  return null;
}

const TIME_MIN = 2;
const TIME_MAX = 300;
const POINTS_MAX = 100000;

/** Client-side check of the server rules every type shares (backend/app/schemas/admin.py
 *  QuestionCreate); null when the form can be saved. Without it a bad value only shows the
 *  server's 422. */
function formProblem(form: FormState): string | null {
  const t = form.timeLimitSeconds;
  if (!Number.isInteger(t) || t < TIME_MIN || t > TIME_MAX) {
    return `Time limit must be a whole number from ${TIME_MIN} to ${TIME_MAX} seconds`;
  }
  const accuracy = form.grading === 'ACCURACY';
  if (form.type === 'multi_select' && keptMsOptions(form).length < 2) {
    return 'Multi-select needs at least 2 options with text or an image';
  }
  if (accuracy && form.type === 'multiple_choice' && form.mcOptions.some((o) => o.points < 0)) {
    return 'Option points must be 0 or more';
  }
  if (accuracy && form.type === 'fill_in_the_blank') {
    const kept = keptFibAnswers(form);
    if (kept.length === 0) return 'Enter at least one accepted answer';
    if (kept.some((a) => a.points < 0)) return 'Answer points must be 0 or more';
    if (!Number.isInteger(form.fibEditDistance) || form.fibEditDistance < 0) {
      return 'Edit distance must be a whole number, 0 or more';
    }
  }
  const points = formToPayload(form).points_value;
  if (!Number.isFinite(points) || points < 0) return 'Points must be 0 or more';
  if (points > POINTS_MAX) return `Points must be at most ${formatNum(POINTS_MAX)}`;
  return null;
}

const defaultForm = (): FormState => ({
  type: 'multiple_choice',
  grading: 'ACCURACY',
  prompt: '',
  timeLimitSeconds: 30,
  pointsValue: 1,
  mcOptions: [{ text: '', points: 1, rowId: newRowId() }, { text: '', points: 0, rowId: newRowId() }],
  tfTruePoints: 1,
  tfFalsePoints: 0,
  fibAnswers: [{ text: '', points: 1 }],
  fibEditDistance: 0,
  msOptions: [{ text: '', points: 1, rowId: newRowId() }, { text: '', points: 1, rowId: newRowId() }],
  pinX: 0,
  pinY: 0,
  cgCorrect: '',
  cgFull: '',
  cgPartial: '',
  cgUnit: '',
  promptImageId: null,
  pinImageId: null,
});

/** A config image id, or null when absent or not an id. */
function configId(v: unknown): number | null {
  return typeof v === 'number' && Number.isInteger(v) ? v : null;
}

function questionToForm(q: Question): FormState {
  const base: FormState = {
    ...defaultForm(),
    type: q.type,
    grading: q.grading_type,
    prompt: q.prompt,
    timeLimitSeconds: q.time_limit_seconds,
    pointsValue: q.points_value,
    promptImageId: configId(q.config['promptImageId']),
    pinImageId: configId(q.config['imageId']),
  };
  const optionImages = (q.config['optionImageIds'] as (number | null)[] | undefined) ?? [];
  if (q.type === 'multiple_choice') {
    const opts = (q.config['options'] as string[]) ?? [];
    const pts = (q.answer_data['answer_points'] as number[]) ?? [];
    base.mcOptions = opts.map((text, i) => ({ text, points: pts[i] ?? 0, imageId: configId(optionImages[i]), rowId: newRowId() }));
  } else if (q.type === 'true_false') {
    const ap = q.answer_data['answer_points'] as { true: number; false: number } | undefined;
    base.tfTruePoints = ap?.true ?? 1;
    base.tfFalsePoints = ap?.false ?? 0;
  } else if (q.type === 'fill_in_the_blank') {
    const accepted = (q.answer_data['acceptedAnswers'] as string[]) ?? [];
    const pts = (q.answer_data['answerPoints'] as number[]) ?? [];
    base.fibAnswers = accepted.length
      ? accepted.map((text, i) => ({ text, points: pts[i] ?? 1 }))
      : [{ text: '', points: 1 }];
    base.fibEditDistance = (q.answer_data['editDistance'] as number) ?? 0;
  } else if (q.type === 'multi_select') {
    const opts = (q.config['options'] as string[]) ?? [];
    const pts = (q.answer_data['answer_points'] as number[]) ?? [];
    base.msOptions = opts.length
      ? opts.map((text, i) => ({ text, points: pts[i] ?? 1, imageId: configId(optionImages[i]), rowId: newRowId() }))
      : [{ text: '', points: 1, rowId: newRowId() }, { text: '', points: 1, rowId: newRowId() }];
  } else if (q.type === 'pin_drop') {
    const target = q.answer_data['target'] as { x: number; y: number } | undefined;
    base.pinX = target?.x ?? 0;
    base.pinY = target?.y ?? 0;
  } else if (q.type === 'closest_guess') {
    const num = (k: string) => (typeof q.answer_data[k] === 'number' ? String(q.answer_data[k]) : '');
    base.cgCorrect = num('correct');
    base.cgFull = num('fullTolerance');
    base.cgPartial = num('partialTolerance');
    base.cgUnit = (q.config['unit'] as string | undefined) ?? '';
  }
  return base;
}

/** The multi-select options and FITB answers that are sent (empty rows are dropped). */
function keptMsOptions(form: FormState): McOption[] {
  // An empty option is dropped unless it has an image (T8 §10).
  return form.msOptions.filter((o) => o.text.trim() || o.imageId != null);
}
function keptFibAnswers(form: FormState): FibAnswer[] {
  return form.fibAnswers.filter((a) => a.text.trim());
}

function formToPayload(form: FormState) {
  let config: Record<string, unknown> = {};
  let answer_data: Record<string, unknown> = {};
  const msKept = keptMsOptions(form);
  const fibKept = keptFibAnswers(form);
  if (form.type === 'multiple_choice') {
    const p = buildMcPayload(form.mcOptions, form.grading);
    config = p.config;
    answer_data = p.answer_data;
  } else if (form.type === 'true_false') {
    const p = buildTfPayload(form.tfTruePoints, form.tfFalsePoints);
    config = p.config;
    answer_data = p.answer_data;
  } else if (form.type === 'multi_select') {
    const p = buildMsPayload(msKept);
    config = p.config;
    answer_data = p.answer_data;
  } else if (form.type === 'pin_drop') {
    // COMPLETENESS has no correct answer: answer_data must be {} (design §4).
    answer_data = form.grading === 'ACCURACY' ? { target: { x: form.pinX, y: form.pinY } } : {};
    if (form.pinImageId !== null) config = { imageId: form.pinImageId };
  } else if (form.type === 'closest_guess') {
    const unit = form.cgUnit.trim();
    config = unit ? { unit } : {};
    answer_data =
      form.grading === 'ACCURACY'
        ? {
            correct: Number(form.cgCorrect),
            fullTolerance: Number(form.cgFull),
            partialTolerance: Number(form.cgPartial),
          }
        : {};
  } else {
    const p = buildFibPayload(fibKept, form.fibEditDistance);
    config = p.config;
    answer_data = p.answer_data;
  }
  // T7 types: the author enters the full-credit points in both modes (design §2).
  // Points come from the same filtered rows that are sent, so a dropped row can't add to them.
  const points_value =
    form.grading === 'COMPLETENESS' || isNewType(form.type)
      ? form.pointsValue
      : form.type === 'multiple_choice'
      ? Math.max(0, ...form.mcOptions.map((o) => o.points))
      : form.type === 'true_false'
      ? Math.max(form.tfTruePoints, form.tfFalsePoints)
      : form.type === 'multi_select'
      ? msKept.reduce((sum, o) => sum + (o.points > 0 ? o.points : 0), 0)
      : Math.max(0, ...fibKept.map((a) => a.points));
  if (form.promptImageId !== null) config = { ...config, promptImageId: form.promptImageId };
  return {
    type: form.type,
    grading_type: form.grading,
    prompt: form.prompt,
    config,
    answer_data,
    time_limit_seconds: form.timeLimitSeconds,
    points_value,
  };
}

// ---- presentation helpers ----

const TYPE_NAME: Record<QuestionType, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True / False',
  fill_in_the_blank: 'Fill in the blank',
  multi_select: 'Multi-select',
  pin_drop: 'Pin drop',
  closest_guess: 'Closest guess',
};

const GRADING_NAME: Record<GradingType, string> = {
  ACCURACY: 'Accuracy',
  COMPLETENESS: 'Completeness',
};

/** A, B, C… for option rows; numbers after Z. */
function optionMarker(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1);
}

function signedPoints(pts: number): string {
  return `${pts > 0 ? '+' : pts < 0 ? '−' : ''}${formatNum(Math.abs(pts))} pts`;
}

const iconButton =
  'inline-flex size-9 items-center justify-center text-secondary transition-colors duration-fast ease-smooth hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-30';

function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-sm font-semibold text-secondary">
        {label}
      </label>
      {children}
      {hint && <p className="text-caption text-muted">{hint}</p>}
    </div>
  );
}

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4 rounded-xl border border-line p-4 sm:p-5">
      <header>
        <h3 className="text-subheading text-primary">{title}</h3>
        {description && <p className="mt-0.5 text-caption text-muted">{description}</p>}
      </header>
      {children}
    </section>
  );
}

/** Editable list of text + points rows (MC options, multi-select options, FITB answers). */
function AnswerRows({
  items,
  onChange,
  kind,
  lettered,
  showPoints,
  pointsHeader,
  pointsMin,
  minCount,
  newPoints,
  addLabel,
  images,
}: {
  items: McOption[];
  /** Gets an updater, so a late change (an image upload) applies to the latest rows. */
  onChange: (update: (prev: McOption[]) => McOption[]) => void;
  kind: 'Option' | 'Answer';
  lettered: boolean;
  showPoints: boolean;
  pointsHeader: string;
  pointsMin?: string;
  minCount: number;
  newPoints: number;
  addLabel: string;
  /** T8: per-option images (multiple choice and multi-select). */
  images?: { library: ImageLibrary; known?: QuestionImages };
}) {
  const canRemove = items.length > minCount;
  const [picking, setPicking] = useState<number | null>(null);
  function patch(i: number, next: Partial<McOption>) {
    onChange((prev) => prev.map((o, j) => (j === i ? { ...o, ...next } : o)));
  }
  /** Sets one row's image by its rowId: the upload may finish after other edits, a removal
   *  or a reorder, and must change only that row. */
  function setImage(rowId: number | undefined, i: number, imageId: number | null) {
    onChange((prev) =>
      prev.map((o, j) => ((rowId !== undefined ? o.rowId === rowId : j === i) ? { ...o, imageId } : o)),
    );
  }
  return (
    <div className="space-y-2">
      {showPoints && (
        <div className="flex gap-2 pl-11 text-caption text-muted" aria-hidden="true">
          <span className="flex-1">Answer text</span>
          {images && <span className="w-9" />}
          <span className="w-24 sm:w-28">{pointsHeader}</span>
          {canRemove && <span className="w-9" />}
        </div>
      )}
      <ul className="space-y-2">
        {items.map((item, i) => {
          const scores = showPoints && item.points > 0;
          const penalty = showPoints && item.points < 0;
          return (
            <li key={i} className="animate-fade-in space-y-2">
            <div className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className={cn(
                  'inline-flex size-9 shrink-0 items-center justify-center rounded-lg font-mono text-sm font-bold tabular-nums transition-colors duration-base ease-smooth',
                  scores ? 'bg-success-tint text-success' : penalty ? 'bg-danger-tint text-danger' : 'bg-raised text-secondary',
                )}
              >
                {lettered ? optionMarker(i) : i + 1}
              </span>
              <Input
                aria-label={`${kind} ${i + 1} text`}
                placeholder={`${kind} ${i + 1}`}
                value={item.text}
                onChange={(e) => patch(i, { text: e.target.value })}
                className="min-w-0 flex-1"
              />
              {images && (
                <button
                  type="button"
                  aria-label={item.imageId != null ? `Change image for ${kind.toLowerCase()} ${i + 1}` : `Add image to ${kind.toLowerCase()} ${i + 1}`}
                  aria-expanded={picking === i}
                  title={item.imageId != null ? 'Change image' : 'Add image'}
                  onClick={() => setPicking(picking === i ? null : i)}
                  className={cn(iconButton, 'shrink-0 overflow-hidden rounded-lg border border-line p-0')}
                >
                  {item.imageId != null ? (
                    <ImageThumb id={item.imageId} library={images.library} known={images.known} className="size-9 rounded-none border-0" />
                  ) : (
                    <ImagePlus size={16} aria-hidden="true" />
                  )}
                </button>
              )}
              {showPoints && (
                <Input
                  type="number"
                  aria-label={`${kind} ${i + 1} points`}
                  placeholder="pts"
                  value={item.points}
                  onChange={(e) => patch(i, { points: Number(e.target.value) })}
                  className="w-24 shrink-0 tabular-nums sm:w-28"
                  min={pointsMin}
                  step="any"
                />
              )}
              {canRemove && (
                <button
                  type="button"
                  aria-label={`Remove ${kind.toLowerCase()} ${i + 1}`}
                  onClick={() => { setPicking(null); onChange((prev) => prev.filter((_, j) => j !== i)); }}
                  className={cn(iconButton, 'shrink-0 rounded-lg')}
                >
                  <Trash2 size={16} aria-hidden="true" />
                </button>
              )}
            </div>
            {images && picking === i && (
              <ImageChooser
                library={images.library}
                value={item.imageId ?? null}
                onPick={(id) => setImage(item.rowId, i, id)}
                onClose={() => setPicking(null)}
                label={`Image for ${kind.toLowerCase()} ${optionMarker(i)}`}
              />
            )}
            </li>
          );
        })}
      </ul>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange((prev) => [...prev, { text: '', points: newPoints, imageId: null, rowId: newRowId() }])}
      >
        <Plus size={14} aria-hidden="true" /> {addLabel}
      </Button>
    </div>
  );
}

// ---- QuestionForm subcomponent ----

function QuestionForm({
  title,
  icon,
  initial,
  onSave,
  onCancel,
  saving,
  library,
  known,
}: {
  title: string;
  icon: React.ReactNode;
  initial: FormState;
  library: ImageLibrary;
  /** The edited question's image map (older versions); undefined for a new question. */
  known?: QuestionImages;
  onSave: (payload: ReturnType<typeof formToPayload>) => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<FormState>(initial);
  const uid = useId();
  const fid = (name: string) => `${uid}-${name}`;

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  // Every type needs a prompt (the server 422s without one, and its list-shaped error would
  // show only "HTTP 422"), so say it here and keep Save disabled. The same goes for the
  // other server rules in formProblem.
  const problem =
    (form.prompt.trim() ? null : 'Enter the question prompt') ??
    imageProblem(form) ??
    (isNewType(form.type) ? newTypeProblem(form) : null) ??
    formProblem(form);
  const optionImages = { library, known };
  const accuracy = form.grading === 'ACCURACY';
  const worth = formToPayload(form).points_value;
  const pinUrl =
    form.pinImageId === null
      ? undefined
      : library.images.find((img) => img.id === form.pinImageId)?.url ?? known?.[String(form.pinImageId)]?.url;

  return (
    <div className="space-y-5">
      <header className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent-hint text-accent-text"
        >
          {icon}
        </span>
        <h2 className="text-heading text-primary">{title}</h2>
      </header>

      <FormSection title="Question" description="Pick how players answer and how the answer is scored.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Question type" htmlFor={fid('type')}>
            <select
              id={fid('type')}
              className={fieldClassName}
              value={form.type}
              onChange={(e) => set('type', e.target.value as QuestionType)}
            >
              <option value="multiple_choice">Multiple Choice</option>
              <option value="true_false">True / False</option>
              <option value="fill_in_the_blank">Fill in the Blank</option>
              <option value="multi_select">Multi-Select (Select All That Apply)</option>
              <option value="pin_drop">Pin Drop (grid)</option>
              <option value="closest_guess">Closest Guess (number)</option>
            </select>
          </Field>
          <Field
            label="Grading"
            htmlFor={fid('grading')}
            hint={accuracy ? 'Points depend on the answer.' : 'Any answer earns the full points.'}
          >
            <select
              id={fid('grading')}
              className={fieldClassName}
              value={form.grading}
              onChange={(e) => set('grading', e.target.value as GradingType)}
            >
              <option value="ACCURACY">Accuracy</option>
              <option value="COMPLETENESS">Completeness</option>
            </select>
          </Field>
        </div>
        <Field label="Prompt" htmlFor={fid('prompt')}>
          <textarea
            id={fid('prompt')}
            className={cn(fieldClassName, 'resize-y text-body-lg')}
            rows={3}
            placeholder="Question text…"
            value={form.prompt}
            onChange={(e) => set('prompt', e.target.value)}
            required
          />
        </Field>
        <ImagePicker
          label="Question image (optional)"
          value={form.promptImageId}
          onChange={(id) => set('promptImageId', id)}
          library={library}
          known={known}
        />
      </FormSection>

      {/* Type-specific */}
      {form.type === 'multiple_choice' && (
        <FormSection
          title="Options"
          description={accuracy ? 'Give points to each correct option. Wrong options get 0.' : undefined}
        >
          <AnswerRows
            items={form.mcOptions}
            onChange={(update) => setForm((prev) => ({ ...prev, mcOptions: update(prev.mcOptions) }))}
            kind="Option"
            lettered
            showPoints={accuracy}
            pointsHeader="Points"
            pointsMin="0"
            minCount={2}
            newPoints={0}
            addLabel="Add option"
            images={optionImages}
          />
        </FormSection>
      )}

      {form.type === 'multi_select' && (
        <FormSection
          title="Options"
          description={
            accuracy
              ? 'Correct options: positive pts. Distractors: negative pts (penalty). Score = sum of selected, capped at 0.'
              : undefined
          }
        >
          <AnswerRows
            items={form.msOptions}
            onChange={(update) => setForm((prev) => ({ ...prev, msOptions: update(prev.msOptions) }))}
            kind="Option"
            lettered
            showPoints={accuracy}
            pointsHeader="Points (neg = penalty)"
            minCount={2}
            newPoints={1}
            addLabel="Add option"
            images={optionImages}
          />
        </FormSection>
      )}

      {form.type === 'true_false' && accuracy && (
        <FormSection title="Points per answer">
          <div className="grid grid-cols-2 gap-4 sm:max-w-sm">
            <Field label="True" htmlFor={fid('tf-true')}>
              <Input
                id={fid('tf-true')}
                type="number"
                value={form.tfTruePoints}
                onChange={(e) => set('tfTruePoints', Number(e.target.value))}
                className="tabular-nums"
                min="0"
                step="any"
              />
            </Field>
            <Field label="False" htmlFor={fid('tf-false')}>
              <Input
                id={fid('tf-false')}
                type="number"
                value={form.tfFalsePoints}
                onChange={(e) => set('tfFalsePoints', Number(e.target.value))}
                className="tabular-nums"
                min="0"
                step="any"
              />
            </Field>
          </div>
        </FormSection>
      )}

      {form.type === 'fill_in_the_blank' && accuracy && (
        <FormSection title="Accepted answers" description="A player's answer matches any of these. Case does not matter.">
          <AnswerRows
            items={form.fibAnswers}
            onChange={(update) => setForm((prev) => ({ ...prev, fibAnswers: update(prev.fibAnswers) }))}
            kind="Answer"
            lettered={false}
            showPoints
            pointsHeader="Points"
            pointsMin="0"
            minCount={1}
            newPoints={1}
            addLabel="Add answer"
          />
          <Field
            label="Edit distance tolerance (fuzzy match)"
            htmlFor={fid('fib-distance')}
            hint="How many typed characters can differ. 0 means an exact match."
          >
            <Input
              id={fid('fib-distance')}
              type="number"
              value={form.fibEditDistance}
              onChange={(e) => set('fibEditDistance', Number(e.target.value))}
              className="w-28 tabular-nums"
              min="0"
            />
          </Field>
        </FormSection>
      )}

      {form.type === 'pin_drop' && (
        <FormSection title="Target point">
          {accuracy ? (
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
              <div className="space-y-3">
                <div className="flex gap-3">
                  {(['pinX', 'pinY'] as const).map((key) => (
                    <Field key={key} label={key === 'pinX' ? 'x' : 'y'} htmlFor={fid(key)}>
                      <select
                        id={fid(key)}
                        className={cn(fieldClassName, 'w-24 font-mono tabular-nums')}
                        value={form[key]}
                        onChange={(e) => set(key, Number(e.target.value))}
                      >
                        {GRID_VALUES.map((v) => <option key={v} value={v}>{v}</option>)}
                      </select>
                    </Field>
                  ))}
                </div>
                <p className="max-w-xs text-caption text-muted">Full points for exactly this point; 0 otherwise.</p>
              </div>
              <div className="w-60 max-w-full">
                <PinGrid maxSize={240} target={{ x: form.pinX, y: form.pinY }} imageUrl={pinUrl} />
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
              <p className="text-sm text-secondary sm:flex-1">Completeness: any point on the grid earns the points.</p>
              {pinUrl && (
                <div className="w-60 max-w-full">
                  <PinGrid maxSize={240} imageUrl={pinUrl} />
                </div>
              )}
            </div>
          )}
          <ImagePicker
            label="Grid background (optional)"
            value={form.pinImageId}
            onChange={(id) => set('pinImageId', id)}
            library={library}
            known={known}
          />
        </FormSection>
      )}

      {form.type === 'closest_guess' && (
        <FormSection title="Number answer">
          <Field label="Unit (optional, shown to players)" htmlFor={fid('cg-unit')}>
            <Input
              id={fid('cg-unit')}
              value={form.cgUnit}
              onChange={(e) => set('cgUnit', e.target.value)}
              placeholder="e.g. m, years, kg"
              className="sm:w-48"
            />
          </Field>
          {accuracy && (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                {([
                  ['cgCorrect', 'Correct value'],
                  ['cgFull', 'Full-credit tolerance ±'],
                  ['cgPartial', 'Partial-credit tolerance ±'],
                ] as const).map(([key, label]) => (
                  <Field key={key} label={label} htmlFor={fid(key)}>
                    <Input
                      id={fid(key)}
                      type="number"
                      step="any"
                      value={form[key]}
                      onChange={(e) => set(key, e.target.value)}
                      className="tabular-nums"
                    />
                  </Field>
                ))}
              </div>
              <p className="text-caption text-muted">
                Full points within the full-credit tolerance; partial credit falls linearly to 0 at
                the partial-credit tolerance.
              </p>
            </>
          )}
        </FormSection>
      )}

      {/* Time + points */}
      <FormSection title="Timing and points">
        <div className="flex flex-wrap gap-4">
          <Field label="Time limit (seconds)" htmlFor={fid('time')} hint="2 to 300 seconds.">
            <Input
              id={fid('time')}
              type="number"
              value={form.timeLimitSeconds}
              onChange={(e) => set('timeLimitSeconds', Number(e.target.value))}
              className="w-32 tabular-nums"
              min="2"
              max="300"
            />
          </Field>
          {(form.grading === 'COMPLETENESS' || isNewType(form.type)) && (
            <Field label="Points value" htmlFor={fid('points')}>
              <Input
                id={fid('points')}
                type="number"
                value={form.pointsValue}
                onChange={(e) => set('pointsValue', Number(e.target.value))}
                className="w-32 tabular-nums"
                min="0"
                max="100000"
                step="any"
              />
            </Field>
          )}
        </div>
      </FormSection>

      {/* Sticky action bar: stays in reach while a long form scrolls. */}
      <div className="glass-strong sticky bottom-3 z-10 flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3">
        <div className="min-w-0 flex-1" aria-live="polite">
          {problem ? (
            <p key={problem} className="flex animate-fade-in items-center gap-2 text-sm font-semibold text-warning">
              <AlertTriangle size={16} aria-hidden="true" className="shrink-0" />
              {problem}
            </p>
          ) : (
            <p className="text-sm text-secondary">
              Worth <span className="font-semibold tabular-nums text-primary">{formatNum(worth)}</span> pts
              in <span className="font-semibold tabular-nums text-primary">{form.timeLimitSeconds}</span> s
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button
            type="button"
            onClick={() => onSave(formToPayload(form))}
            disabled={problem !== null}
            loading={saving}
          >
            <Check size={16} aria-hidden="true" />
            {saving ? 'Saving…' : 'Save Question'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---- Question card (read view) ----

function OptionPreview({
  marker,
  text,
  pts,
  image,
}: {
  marker: string;
  text: string;
  pts: number;
  image?: React.ReactNode;
}) {
  const correct = pts > 0;
  const penalty = pts < 0;
  return (
    <li
      className={cn(
        'flex items-center gap-3 rounded-lg border px-3 py-2 text-sm',
        correct ? 'border-success bg-success-tint' : 'border-line',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-flex size-7 shrink-0 items-center justify-center rounded-md font-mono text-xs font-bold',
          correct ? 'border border-success text-success' : penalty ? 'bg-danger-tint text-danger' : 'bg-raised text-secondary',
        )}
      >
        {correct ? <Check size={14} strokeWidth={3} /> : marker}
      </span>
      {image}
      <span className={cn('min-w-0 flex-1 break-words', correct ? 'font-semibold text-primary' : 'text-secondary')}>
        {text}
        {correct && <span className="sr-only"> (correct)</span>}
      </span>
      {pts !== 0 && (
        <Badge tone={correct ? 'success' : 'danger'} className="tabular-nums">
          {signedPoints(pts)}
        </Badge>
      )}
    </li>
  );
}

/** id -> url for QuestionImage, from the editor's image map. */
function urlsOf(images: QuestionImages | undefined): ImageUrls {
  return Object.fromEntries(Object.entries(images ?? {}).map(([id, v]) => [id, v.url]));
}

function AnswerPreview({ q }: { q: Question }) {
  if (q.type === 'multiple_choice' || q.type === 'multi_select') {
    const opts = (q.config['options'] as string[]) ?? [];
    const pts = (q.answer_data['answer_points'] as number[]) ?? [];
    const ids = (q.config['optionImageIds'] as (number | null)[] | undefined) ?? [];
    const urls = urlsOf(q.images);
    return (
      <ul className="grid gap-2 sm:grid-cols-2">
        {opts.map((opt, oi) => (
          <OptionPreview
            key={oi}
            marker={optionMarker(oi)}
            text={optionDisplay(opt, oi)}
            pts={pts[oi] ?? 0}
            image={
              ids[oi] != null ? (
                <QuestionImage id={ids[oi]} urls={urls} alt={`${optionDisplay('', oi)} image`} className="size-10 min-h-0 shrink-0 rounded-md p-0" />
              ) : undefined
            }
          />
        ))}
      </ul>
    );
  }
  if (q.type === 'true_false') {
    const ap = q.answer_data['answer_points'] as Record<string, number> | undefined;
    return (
      <ul className="grid grid-cols-2 gap-2">
        {(['true', 'false'] as const).map((k) => (
          <OptionPreview key={k} marker={k === 'true' ? 'T' : 'F'} text={k === 'true' ? 'True' : 'False'} pts={ap?.[k] ?? 0} />
        ))}
      </ul>
    );
  }
  if (q.type === 'fill_in_the_blank') {
    const accepted = (q.answer_data['acceptedAnswers'] as string[]) ?? [];
    const distance = q.answer_data['editDistance'] as number;
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-secondary">Accepted:</span>
        {accepted.map((a, ai) => (
          <Badge key={ai} tone="success" size="md">{a}</Badge>
        ))}
        {distance > 0 && (
          <Badge tone="outline" size="md" className="tabular-nums">±{distance} edit distance</Badge>
        )}
      </div>
    );
  }
  if (q.type === 'pin_drop') {
    const t = q.answer_data['target'] as { x: number; y: number } | undefined;
    const bg = configId(q.config['imageId']);
    return (
      <div className="space-y-2">
      {bg !== null && (
        <p className="flex items-center gap-2 text-sm text-secondary">
          <QuestionImage id={bg} urls={urlsOf(q.images)} alt="Grid background" className="size-10 min-h-0 rounded-md p-0" />
          Grid background
        </p>
      )}
      <p className="flex items-center gap-2 text-sm">
        <Crosshair size={16} aria-hidden="true" className={q.grading_type === 'ACCURACY' ? 'text-success' : 'text-muted'} />
        {q.grading_type === 'ACCURACY' ? (
          <span className="font-semibold tabular-nums text-success">Target ({t?.x}, {t?.y})</span>
        ) : (
          <span className="text-secondary">Any point (completeness)</span>
        )}
      </p>
      </div>
    );
  }
  if (q.type === 'closest_guess') {
    const unit = q.config['unit'] as string | undefined;
    return (
      <p className="flex items-center gap-2 text-sm">
        <Target size={16} aria-hidden="true" className={q.grading_type === 'ACCURACY' ? 'text-success' : 'text-muted'} />
        {q.grading_type === 'ACCURACY' ? (
          <span className="tabular-nums text-secondary">
            <span className="font-semibold text-success">
              {formatNum(q.answer_data['correct'] as number)}{unit ? ` ${unit}` : ''}
            </span>{' '}
            (±{formatNum(q.answer_data['fullTolerance'] as number)} full, ±{formatNum(q.answer_data['partialTolerance'] as number)} partial)
          </span>
        ) : (
          <span className="text-secondary">Any number{unit ? ` in ${unit}` : ''} (completeness)</span>
        )}
      </p>
    );
  }
  return null;
}

// ---- Main page ----

export default function QuestionEditorPage() {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();
  const [game, setGame] = useState<Game | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const library = useImageLibrary(game?.course_id ?? null);

  async function load() {
    try {
      const [g, qs] = await Promise.all([
        api.get<Game>(`/host/games/${gameId}`),
        api.get<Question[]>(`/host/games/${gameId}/questions`),
      ]);
      setGame(g);
      setQuestions(qs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [gameId]);

  async function addQuestion(payload: ReturnType<typeof formToPayload>) {
    setSaving(true);
    setError('');
    try {
      await api.post(`/host/games/${gameId}/questions`, payload);
      setShowAddForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save question');
    } finally {
      setSaving(false);
    }
  }

  async function updateQuestion(id: number, payload: ReturnType<typeof formToPayload>) {
    setSaving(true);
    setError('');
    try {
      await api.put(`/host/games/${gameId}/questions/${id}`, payload);
      setEditingId(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update question');
    } finally {
      setSaving(false);
    }
  }

  async function deleteQuestion(id: number) {
    if (!confirm('Delete this question?')) return;
    try {
      await api.delete(`/host/games/${gameId}/questions/${id}`);
      await load(); // remaining questions are renumbered server-side
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete question');
    }
  }

  async function moveQuestion(index: number, direction: -1 | 1) {
    const newOrder = [...questions];
    const target = index + direction;
    if (target < 0 || target >= newOrder.length) return;
    [newOrder[index], newOrder[target]] = [newOrder[target], newOrder[index]];
    try {
      await api.post(`/host/games/${gameId}/questions/reorder`, {
        order: newOrder.map((q) => q.id),
      });
      setQuestions(newOrder);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reorder');
    }
  }

  async function handleExport() {
    setDownloading(true);
    setError('');
    try {
      await api.download(`/host/games/${gameId}/export`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setDownloading(false);
    }
  }

  function openAddForm() {
    setShowAddForm(true);
    setEditingId(null);
  }

  if (loading) {
    return (
      <div className="flex min-h-[50vh] animate-fade-in items-center justify-center gap-3 p-8 text-body-lg text-secondary">
        <Spinner /> Loading…
      </div>
    );
  }

  // The API refuses edits in both cases (409); these flags just avoid offering them.
  const archived = Boolean(game?.archived_at);
  const locked = Boolean(game?.has_active_room);
  const readOnly = archived || locked;

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 pb-16 pt-16 sm:px-8 sm:pt-16">
      <header className="glass animate-fade-in rounded-3xl p-5 sm:p-8">
        <button
          onClick={() => navigate(game ? `/courses/${game.course_id}/quizzes` : '/courses')}
          className="-ml-2 mb-4 inline-flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-semibold text-secondary transition-colors duration-fast ease-smooth hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft size={16} aria-hidden="true" /> Back to quizzes
        </button>
        <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <h1 className="break-words text-title text-primary">{game?.title}</h1>
            <p className="mt-2 text-body-lg text-secondary tabular-nums">
              {questions.length} question{questions.length !== 1 ? 's' : ''}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-3">
            <Button variant="secondary" onClick={() => void handleExport()} loading={downloading}>
              <Download size={16} aria-hidden="true" />
              {downloading ? 'Exporting…' : 'Export JSON'}
            </Button>
            {!readOnly && (
              <Button onClick={openAddForm}>
                <Plus size={16} aria-hidden="true" /> Add Question
              </Button>
            )}
          </div>
        </div>
      </header>

      {locked && (
        <div className="flex animate-slide-down flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-warning bg-warning-tint px-4 py-3 text-sm font-medium text-warning">
          <Lock size={16} aria-hidden="true" className="shrink-0" />
          <span className="min-w-0 flex-1">A room using this quiz is open. Edits are locked until it ends.</span>
          {game && (
            <button
              onClick={() => navigate(`/courses/${game.course_id}/rooms`)}
              className="rounded-md px-1 font-semibold underline underline-offset-2 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              View rooms
            </button>
          )}
        </div>
      )}
      {archived && (
        <div className="glass-subtle flex animate-slide-down items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium text-secondary">
          <Archive size={16} aria-hidden="true" className="shrink-0" />
          This quiz is archived. Restore to edit (from the course's quiz list).
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="flex animate-slide-down items-start gap-2 rounded-xl border border-danger bg-danger-tint px-4 py-3 text-sm font-semibold text-danger"
        >
          <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}

      {/* Add question form */}
      {showAddForm && !readOnly && (
        <Card variant="glass-strong" className="animate-scale-in p-4 sm:p-6">
          <QuestionForm
            title="New Question"
            icon={<Plus size={20} />}
            initial={defaultForm()}
            onSave={(payload) => void addQuestion(payload)}
            onCancel={() => setShowAddForm(false)}
            saving={saving}
            library={library}
          />
        </Card>
      )}

      {/* Question list */}
      {questions.length === 0 && !showAddForm && (
        <div className="glass-subtle flex animate-fade-in flex-col items-center gap-4 rounded-2xl border-dashed px-6 py-12 text-center">
          <p className="text-body-lg text-secondary">No questions yet. Add one above.</p>
          {!readOnly && (
            <Button variant="outline" onClick={openAddForm}>
              <Plus size={16} aria-hidden="true" /> Add Question
            </Button>
          )}
        </div>
      )}

      <ol className="stagger space-y-4">
        {questions.map((q, i) => (
          <li key={q.id} className="animate-slide-up">
            {editingId === q.id ? (
              <Card variant="glass-strong" className="animate-scale-in p-4 sm:p-6" style={staggerStyle(0)}>
                <QuestionForm
                  title={`Edit Question ${i + 1}`}
                  icon={<Pencil size={18} />}
                  initial={questionToForm(q)}
                  onSave={(payload) => void updateQuestion(q.id, payload)}
                  onCancel={() => setEditingId(null)}
                  saving={saving}
                  library={library}
                  known={q.images}
                />
              </Card>
            ) : (
              <Card
                variant="question"
                edge="left"
                className="flex animate-fade-in gap-3 py-4 pl-5 pr-4 sm:gap-5 sm:py-5 sm:pl-7 sm:pr-6"
                style={staggerStyle(0)}
              >
                {/* Number chip + reorder buttons */}
                <div className="flex shrink-0 flex-col items-center gap-2">
                  <span
                    className="inline-flex size-11 items-center justify-center rounded-xl bg-accent-hint font-display text-lg font-bold tabular-nums text-accent-text"
                  >
                    <span className="sr-only">Question </span>
                    {i + 1}
                  </span>
                  {!readOnly && (
                    <div className="flex flex-col overflow-hidden rounded-lg border border-line">
                      <button
                        onClick={() => void moveQuestion(i, -1)}
                        disabled={i === 0}
                        aria-label={`Move question ${i + 1} up`}
                        title="Move up"
                        className={iconButton}
                      >
                        <ChevronUp size={18} aria-hidden="true" />
                      </button>
                      <button
                        onClick={() => void moveQuestion(i, 1)}
                        disabled={i === questions.length - 1}
                        aria-label={`Move question ${i + 1} down`}
                        title="Move down"
                        className={cn(iconButton, 'border-t border-line')}
                      >
                        <ChevronDown size={18} aria-hidden="true" />
                      </button>
                    </div>
                  )}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
                    <div className="flex min-w-[12rem] flex-1 flex-wrap items-center gap-1.5">
                      <Badge tone="accent">{TYPE_NAME[q.type]}</Badge>
                      <Badge tone="neutral">{GRADING_NAME[q.grading_type]}</Badge>
                      <Badge tone="outline" className="tabular-nums">
                        <Clock size={12} aria-hidden="true" /> {q.time_limit_seconds}s
                      </Badge>
                      <Badge tone="outline" className="tabular-nums">
                        <Trophy size={12} aria-hidden="true" /> {formatNum(q.points_value)} pts
                      </Badge>
                    </div>
                    {!readOnly && (
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => { setEditingId(q.id); setShowAddForm(false); }}
                          aria-label={`Edit question ${i + 1}`}
                        >
                          <Pencil size={14} aria-hidden="true" /> Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="px-2.5"
                          onClick={() => void deleteQuestion(q.id)}
                          disabled={q.has_answers}
                          aria-label={`Delete question ${i + 1}`}
                          title={q.has_answers ? "Answered in a past session, can't delete" : 'Delete question'}
                        >
                          <Trash2 size={16} aria-hidden="true" />
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="mt-3 break-words text-body-lg font-semibold text-primary">{q.prompt}</p>
                  {configId(q.config['promptImageId']) !== null && (
                    <QuestionImage
                      id={configId(q.config['promptImageId'])}
                      urls={urlsOf(q.images)}
                      alt="Question image"
                      className="mt-3 max-h-40 max-w-full"
                    />
                  )}
                  {Object.values(q.images ?? {}).some((img) => !img.current) && !readOnly && (
                    <p className="mt-2 text-caption font-semibold text-warning">
                      Uses an older image version. Edit to pick the current image.
                    </p>
                  )}
                  <div className="mt-4">
                    <AnswerPreview q={q} />
                  </div>
                </div>
              </Card>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
