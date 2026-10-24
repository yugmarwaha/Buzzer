import { useEffect } from 'react';
import { useGame } from './GameLayout';
import { Button } from '../../components/ui/button';
import { TimerBar } from '../../components/ui/TimerBar';
import { Card } from '../../components/ui/card';
import { Spinner } from '../../components/ui/spinner';
import { Badge } from '../../components/ui/badge';
import { Progress } from '../../components/ui/progress';
import { staggerStyle } from '../../components/ui/motion';
import { PinGrid } from '../../components/PinGrid';
import { QuestionImage, urlFor } from '../../components/QuestionImage';
import { cn } from '../../lib/utils';
import { BarChart3, CheckCircle2, Lock, LockOpen } from 'lucide-react';

export default function QuestionPage() {
  const { currentQuestion, answeredCount, playerCount, allAnswered, answerPhaseEnded, questionLocked, lockedTimerSeconds, autoAdvance, emitAdvance, emitLockQuestion } = useGame();

  useEffect(() => {
    if (!autoAdvance || !answerPhaseEnded) return;
    const id = setTimeout(emitAdvance, 1500);
    return () => clearTimeout(id);
  }, [autoAdvance, answerPhaseEnded, emitAdvance]);

  if (!currentQuestion) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="flex items-center gap-3 text-xl text-muted animate-fade-in">
          <Spinner />
          Loading question…
        </p>
      </div>
    );
  }

  const typeLabel: Record<string, string> = {
    multiple_choice: 'Multiple Choice',
    true_false: 'True / False',
    fill_in_the_blank: 'Fill in the Blank',
    multi_select: 'Multi-Select',
    pin_drop: 'Pin Drop',
    closest_guess: 'Closest Guess',
  };

  function editDistanceLabel(d: number): string {
    if (d === 0) return 'Exact spelling required';
    if (d === 1) return 'Up to 1 typo allowed';
    return `Up to ${d} typos allowed`;
  }

  const remainingSeconds = questionLocked && lockedTimerSeconds !== null
    ? lockedTimerSeconds
    : currentQuestion.startedAt
      ? Math.max(0, currentQuestion.timeLimitSeconds - (Date.now() - new Date(currentQuestion.startedAt).getTime()) / 1000)
      : currentQuestion.timeLimitSeconds;

  const isAccuracy = currentQuestion.gradingType !== 'COMPLETENESS';
  // T8: with a question image, the prompt steps down a size so the image can be large and the
  // whole screen (timer, card, answer count, buttons) still fits a projector.
  const hasImage = currentQuestion.config.promptImageId != null;

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-6 px-6 py-6">
      <div className="w-full max-w-5xl flex flex-col gap-6">
        {/* Question meta: position, type, grading */}
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 animate-fade-in">
          <p className="text-xl font-semibold text-secondary tabular-nums">
            Question {currentQuestion.questionNumber} of {currentQuestion.totalQuestions}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral" size="lg">{typeLabel[currentQuestion.type] ?? currentQuestion.type}</Badge>
            <Badge tone={isAccuracy ? 'accent' : 'warning'} size="lg">
              {isAccuracy ? 'Accuracy' : 'Participation'}
            </Badge>
            {currentQuestion.type === 'fill_in_the_blank' && currentQuestion.gradingType === 'ACCURACY' && (
              <Badge tone="outline" size="lg">
                {editDistanceLabel(currentQuestion.editDistance ?? 0)}
              </Badge>
            )}
          </div>
        </div>

        <TimerBar
          key={currentQuestion.questionId}
          totalSeconds={currentQuestion.timeLimitSeconds}
          initialSeconds={remainingSeconds}
          paused={questionLocked}
        />

        {!questionLocked && (
          <Card
            key={`prompt-${currentQuestion.questionId}`}
            variant="question"
            tone="accent"
            className={cn('animate-scale-in', hasImage ? 'px-6 py-5 md:px-10 md:py-6' : 'px-6 py-8 md:px-12 md:py-10')}
          >
            {/* Scrolls only when a long prompt passes 45vh (20vh beside an image); py-1 holds the
                glyph tops and tails inside the box, so a prompt that fits shows no scrollbar. */}
            <div className={cn('overflow-y-auto overflow-x-hidden py-1', hasImage ? 'max-h-[20vh]' : 'max-h-[45vh]')}>
              <h2 className={cn('text-primary text-center text-pretty break-words', hasImage ? 'text-title' : 'text-stage')}>
                {currentQuestion.prompt}
              </h2>
            </div>
            {/* T8: the prompt image, hidden while locked like the prompt */}
            <QuestionImage
              id={currentQuestion.config.promptImageId}
              urls={currentQuestion.imageUrls}
              alt="Question image"
              eager
              className="mx-auto mt-4 max-h-[42vh] w-auto max-w-full"
            />
            {/* T7: hidden while locked, like the prompt */}
            {currentQuestion.type === 'closest_guess' && currentQuestion.config.unit && (
              <p className="mt-6 text-center text-2xl text-secondary">
                Answer in <span className="font-semibold text-accent-text">{currentQuestion.config.unit}</span>
              </p>
            )}
          </Card>
        )}

        {/* T7: hidden while locked, like the prompt */}
        {!questionLocked && currentQuestion.type === 'pin_drop' && (
          <div className="glass rounded-2xl mx-auto w-full max-w-xl p-4 animate-slide-up" style={staggerStyle(1)}>
            <PinGrid maxSize={520} imageUrl={urlFor(currentQuestion.imageUrls, currentQuestion.config.imageId)} />
          </div>
        )}

        {/* Answer progress */}
        <div className="glass-subtle rounded-2xl flex flex-col gap-3 px-6 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
            <p className="text-2xl text-secondary tabular-nums">
              <span key={answeredCount} className="inline-block text-stat text-primary tabular-nums animate-tick">
                {answeredCount}
              </span>{' '}
              / {playerCount} answered
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {allAnswered && (
                <Badge tone="success" size="lg" className="animate-pop">
                  <CheckCircle2 className="size-5" aria-hidden="true" />
                  All answered!
                </Badge>
              )}
              {questionLocked && (
                <Badge tone="warning" size="lg" className="animate-pop">
                  <Lock className="size-5" aria-hidden="true" />
                  Answers locked
                </Badge>
              )}
            </div>
          </div>
          <Progress
            value={answeredCount}
            max={Math.max(playerCount, 1)}
            tone={allAnswered ? 'success' : 'accent'}
            size="lg"
            label="Answers received"
          />
        </div>
      </div>

      <div className="flex flex-wrap justify-center gap-4">
        <Button
          size="xl"
          variant="outline"
          onClick={emitLockQuestion}
          className={cn('min-w-56', questionLocked && 'border-warning text-warning hover:bg-warning-tint')}
        >
          {questionLocked
            ? <><LockOpen className="size-6" aria-hidden="true" />Unlock Question</>
            : <><Lock className="size-6" aria-hidden="true" />Lock Question</>}
        </Button>
        <Button size="xl" onClick={emitAdvance} className="min-w-56">
          <BarChart3 className="size-6" aria-hidden="true" />
          Show Results
        </Button>
      </div>
    </div>
  );
}
