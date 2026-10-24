import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import { useGame } from './GameLayout';
import { Check, Lock, MapPin, X } from 'lucide-react';
import { TimerBar } from '../../components/ui/TimerBar';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Spinner } from '../../components/ui/spinner';
import { PinGrid, type GridPoint } from '../../components/PinGrid';
import { QuestionImage, urlFor } from '../../components/QuestionImage';
import { cn, formatNum, optionDisplay, optionLetter, parseGuess } from '../../lib/utils';
import type { QuestionPayload } from '../../types/game';

// Answer cards (docs/plans/ui-themes.md section 12): large tactile glass cards with a letter chip.
// The entrance animation sits on the <li>, so the button's press scale is not held by the
// animation's fill mode.
const OPTION_BASE =
  'flex w-full min-h-16 items-center gap-4 rounded-2xl border-2 px-4 py-3 text-left text-body-lg font-semibold text-primary ' +
  'transition-[transform,border-color,background-color,box-shadow] duration-fast ease-smooth ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page';
const OPTION_IDLE = 'glass-strong border-line-strong hover:border-accent-text motion-safe:active:scale-[0.98]';
const OPTION_SELECTED = 'bg-accent-hint border-accent-text shadow-glow motion-safe:active:scale-[0.98]';
const OPTION_LOCKED = 'glass-subtle border-line cursor-not-allowed';
const OPTION_SELECTED_LOCKED = 'bg-accent-hint border-accent-text cursor-not-allowed';
const CHIP_BASE =
  'flex size-11 shrink-0 items-center justify-center rounded-xl font-display text-xl font-extrabold transition-colors duration-fast ease-smooth';
const CHIP_IDLE = 'border border-line-strong bg-raised text-primary';
const CHIP_SELECTED = 'bg-accent text-accent-fg shadow-button';

// One centered column: phone-first, still a calm column on a wide screen. Bottom padding clears
// the home indicator when the browser reports a safe area.
const SHELL =
  'mx-auto flex min-h-screen w-full max-w-lg flex-col gap-4 px-4 pt-2 pb-[max(1.5rem,env(safe-area-inset-bottom))]';

const TYPE_LABEL: Record<string, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True or false',
  fill_in_the_blank: 'Type your answer',
  multi_select: 'Select all that apply',
  pin_drop: 'Pin drop',
  closest_guess: 'Closest guess',
};

const optionLabel = optionLetter;

/** T8: the option's image on its answer card (the live question loads it at once). */
function OptionImage({ question, i }: { question: QuestionPayload; i: number }) {
  const id = question.config.optionImageIds?.[i];
  if (id == null) return null;
  return (
    <QuestionImage
      id={id}
      urls={question.imageUrls}
      alt={`${optionDisplay('', i)} image`}
      eager
      className="order-last max-h-[40vh] w-full basis-full min-h-24 p-0"
    />
  );
}

/** An option with an image gets a second row: the image at full card width (readable on a phone). */
function hasOptionImage(question: QuestionPayload, i: number): boolean {
  return question.config.optionImageIds?.[i] != null;
}

function QuestionHeader({ question, paused, onExpire }: { question: QuestionPayload; paused: boolean; onExpire: () => void }) {
  const label = TYPE_LABEL[question.type];
  return (
    <header className="glass sticky top-[var(--strip)] z-10 animate-slide-down rounded-2xl px-4 pb-4 pt-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-subheading text-primary tabular-nums">
          Question {question.questionNumber} <span className="font-medium text-secondary">of {question.totalQuestions}</span>
        </p>
        {label && <Badge tone="accent" size="md">{label}</Badge>}
      </div>
      <TimerBar key={question.questionId} totalSeconds={question.timeLimitSeconds} paused={paused} onExpire={onExpire} />
    </header>
  );
}

function SubmittedNote() {
  return (
    <p className="glass-subtle mx-auto flex animate-fade-in items-center gap-2 rounded-pill px-4 py-2 text-center text-body text-secondary">
      <Spinner className="text-accent-text" />
      Answer submitted — waiting for results…
    </p>
  );
}

function LockedNote() {
  return (
    <p className="mx-auto flex animate-fade-in items-center gap-2 rounded-pill bg-warning-tint px-4 py-2 text-center text-body font-semibold text-warning">
      <Lock className="size-4 shrink-0" aria-hidden="true" />
      Answers locked — waiting for results…
    </p>
  );
}

/** Shown in place of a typed-answer form after submit: a check that pops, then the answer. */
function LockedInCard({ children }: { children: ReactNode }) {
  return (
    <div className="glass-strong flex animate-scale-in flex-col items-center gap-3 rounded-3xl px-6 py-8 text-center">
      <span className="flex size-16 animate-pop items-center justify-center rounded-full bg-success-tint text-success">
        <Check className="size-9" strokeWidth={3} aria-hidden="true" />
      </span>
      <p className="text-heading text-primary">Answer locked in!</p>
      <p className="max-w-full break-words text-body-lg font-semibold text-secondary">{children}</p>
      <p className="mt-1 flex items-center gap-2 text-body text-muted">
        <Spinner />
        Waiting for results…
      </p>
    </div>
  );
}

/** Keeps the main action under the thumb while the list above scrolls. */
function ActionBar({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-10 mt-auto pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2">
      <div className="glass-strong rounded-2xl p-2">{children}</div>
    </div>
  );
}

// Remount the question view on every new_question push (questionKey), so the local answer
// state below (submitted, selections, answer clock) starts fresh for each question, and also
// for a resend of the same question after a reconnect when the server has no answer.
export default function QuestionPage() {
  const { questionKey } = useGame();
  return <QuestionView key={questionKey} />;
}

function QuestionView() {
  const { currentQuestion, questionLocked: serverLocked, emitAnswer } = useGame();
  // The local timer hit 0: stop answering (the server auto-locks a moment later). A later
  // unlock by the host reopens the question, so it clears this.
  const [timeUp, setTimeUp] = useState(false);
  useEffect(() => {
    if (!serverLocked) setTimeUp(false);
  }, [serverLocked]);
  const questionLocked = serverLocked || timeUp;
  const startTimeRef = useRef(Date.now());
  const [submitted, setSubmitted] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());
  // Which card the player tapped (multiple choice, true/false); display only.
  const [chosen, setChosen] = useState<number | boolean | null>(null);
  // T7
  const [pinPoint, setPinPoint] = useState<GridPoint | null>(null);
  const [guessText, setGuessText] = useState('');

  const toggleIndex = useCallback((i: number) => {
    if (submitted || questionLocked) return;
    setSelectedIndices(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  }, [submitted, questionLocked]);

  if (!currentQuestion) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="flex items-center gap-3 text-body-lg text-secondary">
          <Spinner className="text-accent-text" />
          Loading question…
        </p>
      </div>
    );
  }

  function submit(answerData: Record<string, unknown>) {
    if (submitted || questionLocked) return;
    setSubmitted(true);
    emitAnswer(currentQuestion!.questionId, answerData, Date.now() - startTimeRef.current);
  }

  const header = <QuestionHeader question={currentQuestion} paused={serverLocked} onExpire={() => setTimeUp(true)} />;

  if (currentQuestion.type === 'multiple_choice') {
    const options = currentQuestion.config.options ?? [];
    const locked = submitted || questionLocked;
    return (
      <div className={SHELL}>
        {header}
        <ul className="stagger flex flex-col gap-3">
          {options.map((opt, i) => {
            const isChosen = chosen === i;
            return (
              <li key={i} className="animate-slide-up">
                <button
                  disabled={locked}
                  aria-label={`${optionLabel(i)}: ${optionDisplay(opt, i)}`}
                  onClick={() => { setChosen(i); submit({ selectedIndex: i }); }}
                  className={cn(
                    OPTION_BASE,
                    hasOptionImage(currentQuestion, i) && 'flex-wrap',
                    locked ? (isChosen ? OPTION_SELECTED_LOCKED : OPTION_LOCKED) : OPTION_IDLE,
                  )}
                >
                  <span className={cn(CHIP_BASE, isChosen ? CHIP_SELECTED : CHIP_IDLE)}>{optionLabel(i)}</span>
                  <OptionImage question={currentQuestion} i={i} />
                  <span className="min-w-0 flex-1 break-words">{opt}</span>
                  {isChosen ? (
                    <Check className="size-6 shrink-0 animate-pop text-accent-text" strokeWidth={3} aria-hidden="true" />
                  ) : (
                    locked && <Lock className="size-5 shrink-0 text-secondary" aria-hidden="true" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
        {submitted && <SubmittedNote />}
        {!submitted && questionLocked && <LockedNote />}
      </div>
    );
  }

  if (currentQuestion.type === 'true_false') {
    const locked = submitted || questionLocked;
    return (
      <div className={SHELL}>
        {header}
        <ul className="stagger grid grid-cols-2 content-center gap-4 flex-1">
          {([true, false] as const).map((value) => {
            const isChosen = chosen === value;
            const Icon = value ? Check : X;
            return (
              <li key={String(value)} className="flex animate-slide-up">
                <button
                  disabled={locked}
                  onClick={() => { setChosen(value); submit({ selectedValue: value }); }}
                  className={cn(
                    OPTION_BASE,
                    'min-h-40 max-h-64 flex-1 flex-col justify-center gap-3 text-center text-title [@media(max-height:480px)]:min-h-24',
                    locked ? (isChosen ? OPTION_SELECTED_LOCKED : OPTION_LOCKED) : OPTION_IDLE,
                  )}
                >
                  <span className={cn(CHIP_BASE, 'size-14 rounded-full', isChosen ? CHIP_SELECTED : CHIP_IDLE)}>
                    <Icon className={cn('size-8', isChosen && 'animate-pop')} strokeWidth={3} aria-hidden="true" />
                  </span>
                  <span className="flex items-center gap-3">
                    {value ? 'True' : 'False'}
                    {locked && !isChosen && <Lock className="size-6 shrink-0 text-secondary" aria-hidden="true" />}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {submitted && <SubmittedNote />}
        {!submitted && questionLocked && <LockedNote />}
      </div>
    );
  }

  if (currentQuestion.type === 'fill_in_the_blank') {
    const maxLength = currentQuestion.config.maxLength ?? 100;
    return (
      <div className={SHELL}>
        {header}

        {submitted ? (
          <LockedInCard>"{inputValue.trim()}"</LockedInCard>
        ) : questionLocked ? (
          <div className="mt-4 flex flex-col items-center gap-3">
            <LockedNote />
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const trimmed = inputValue.trim();
              if (trimmed) submit({ text: trimmed });
            }}
            className="glass-strong flex animate-slide-up flex-col gap-4 rounded-2xl p-4"
          >
            <label htmlFor="answer-text" className="text-subheading text-primary">Your answer</label>
            <Input
              id="answer-text"
              type="text"
              maxLength={maxLength}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Type your answer…"
              autoFocus
              className="min-h-16 text-xl"
            />
            <Button type="submit" size="xl" className="w-full" disabled={!inputValue.trim()}>
              Submit
            </Button>
          </form>
        )}
      </div>
    );
  }

  if (currentQuestion.type === 'multi_select') {
    const options = currentQuestion.config.options ?? [];
    const locked = submitted || questionLocked;
    return (
      <div className={SHELL}>
        {header}
        <p className="text-center text-body text-secondary">Select all that apply</p>
        <ul className="stagger flex flex-col gap-3">
          {options.map((opt, i) => {
            const isSelected = selectedIndices.has(i);
            return (
              <li key={i} className="animate-slide-up">
                <button
                  disabled={locked}
                  onClick={() => toggleIndex(i)}
                  aria-pressed={isSelected}
                  aria-label={`${optionLabel(i)}: ${optionDisplay(opt, i)}`}
                  className={cn(
                    OPTION_BASE,
                    hasOptionImage(currentQuestion, i) && 'flex-wrap',
                    locked
                      ? (isSelected ? OPTION_SELECTED_LOCKED : OPTION_LOCKED)
                      : (isSelected ? OPTION_SELECTED : OPTION_IDLE),
                  )}
                >
                  <span className={cn(CHIP_BASE, isSelected ? CHIP_SELECTED : CHIP_IDLE)}>{optionLabel(i)}</span>
                  <OptionImage question={currentQuestion} i={i} />
                  <span className="min-w-0 flex-1 break-words">{opt}</span>
                  {isSelected && <Check className="size-6 shrink-0 animate-pop text-accent-text" strokeWidth={3} aria-hidden="true" />}
                  {locked && <Lock className="size-5 shrink-0 text-secondary" aria-hidden="true" />}
                </button>
              </li>
            );
          })}
        </ul>
        {!submitted && !questionLocked && (
          <ActionBar>
            <Button
              size="xl"
              className="w-full tabular-nums"
              disabled={selectedIndices.size === 0}
              onClick={() => submit({ selectedIndices: Array.from(selectedIndices) })}
            >
              Submit ({selectedIndices.size} selected)
            </Button>
          </ActionBar>
        )}
        {submitted && <SubmittedNote />}
        {!submitted && questionLocked && <LockedNote />}
      </div>
    );
  }

  if (currentQuestion.type === 'pin_drop') {
    const locked = submitted || questionLocked;
    return (
      <div className={SHELL}>
        {header}
        <div className="glass-strong flex animate-scale-in flex-col items-center gap-3 rounded-2xl p-3">
          <div className="w-full">
            <PinGrid
              maxSize={360}
              selected={pinPoint}
              onSelect={locked ? undefined : setPinPoint}
              imageUrl={urlFor(currentQuestion.imageUrls, currentQuestion.config.imageId)}
            />
          </div>
          <p className="flex items-center gap-2 text-center text-body-lg text-secondary tabular-nums">
            <MapPin className={cn('size-5 shrink-0', pinPoint ? 'text-accent-text' : 'text-muted')} aria-hidden="true" />
            {pinPoint
              ? `Your point: (${pinPoint.x}, ${pinPoint.y})`
              : locked
                ? 'No point chosen'
                : 'Tap the grid to choose a point'}
          </p>
        </div>
        {!locked && (
          <ActionBar>
            <Button
              size="xl"
              className="w-full"
              disabled={!pinPoint}
              onClick={() => pinPoint && submit({ x: pinPoint.x, y: pinPoint.y })}
            >
              Submit
            </Button>
          </ActionBar>
        )}
        {submitted && <SubmittedNote />}
        {!submitted && questionLocked && <LockedNote />}
      </div>
    );
  }

  if (currentQuestion.type === 'closest_guess') {
    const unit = currentQuestion.config.unit;
    const value = parseGuess(guessText);
    const typed = guessText.trim() !== '' && guessText.trim() !== '-';
    const invalid = typed && value === null;
    return (
      <div className={SHELL}>
        {header}

        {submitted ? (
          <LockedInCard>
            <span className="tabular-nums">{value !== null ? formatNum(value) : guessText.trim()}</span>
            {unit ? ` ${unit}` : ''}
          </LockedInCard>
        ) : questionLocked ? (
          <div className="mt-4 flex flex-col items-center gap-3"><LockedNote /></div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (value !== null) submit({ value });
            }}
            className="glass-strong flex animate-slide-up flex-col gap-4 rounded-2xl p-4"
          >
            <label htmlFor="answer-guess" className="text-subheading text-primary">Your guess</label>
            <div className="flex items-center gap-2">
              {/* The iOS decimal keypad has no minus key (design §5.7). */}
              <Button
                type="button"
                variant="secondary"
                size="xl"
                onClick={() => setGuessText((t) => (t.trimStart().startsWith('-') ? t.trimStart().slice(1) : `-${t.trimStart()}`))}
                className="w-16 shrink-0 px-0 text-2xl"
                aria-label="Toggle minus sign"
              >
                ±
              </Button>
              <Input
                id="answer-guess"
                type="text"
                inputMode="decimal"
                value={guessText}
                onChange={(e) => setGuessText(e.target.value)}
                placeholder="Your guess…"
                autoFocus
                aria-invalid={invalid || undefined}
                className="min-h-16 min-w-0 flex-1 text-2xl font-semibold tabular-nums"
              />
              {unit && <span className="shrink-0 text-body-lg font-semibold text-secondary">{unit}</span>}
            </div>
            {invalid && (
              <p className="text-center text-body text-warning">Enter a plain number like 312 or -4.5 (up to 1e12)</p>
            )}
            <Button type="submit" size="xl" className="w-full" disabled={value === null}>
              Submit
            </Button>
          </form>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <p className="text-body-lg text-secondary">Unsupported question type.</p>
    </div>
  );
}
