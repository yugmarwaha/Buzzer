import { useEffect, useState } from 'react';
import { ArrowRight, Trophy } from 'lucide-react';
import { useGame } from './GameLayout';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import { staggerStyle } from '../../components/ui/motion';
import { AnswerBars, DonutStat } from '../../components/charts';
import type { AnswerReveal, ImageUrls, QuestionConfig } from '../../types/game';
import { NewTypeResults, isNewType } from '../../components/NewTypeResults';
import { QuestionImage, urlFor } from '../../components/QuestionImage';
import { optionDisplay, optionLetter } from '../../lib/utils';

const RESULTS_DISPLAY_SECONDS = 10;

const optionLabel = optionLetter;

// ---------------------------------------------------------------------------
// Levenshtein distance — used to colour word-cloud entries for FITB questions
// ---------------------------------------------------------------------------

function levenshtein(a: string, b: string): number {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, (_, i) =>
    Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0))
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1]
          : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

// ---------------------------------------------------------------------------
// Word Cloud — for fill_in_the_blank questions
// ---------------------------------------------------------------------------

interface WordCloudProps {
  distribution: Record<string, number>;
  answerReveal: AnswerReveal;
  totalAnswered: number;
  totalPlayers: number;
}

function WordCloud({ distribution, answerReveal, totalAnswered, totalPlayers }: WordCloudProps) {
  const entries = Object.entries(distribution).sort(([, a], [, b]) => b - a);
  const maxCount = Math.max(...entries.map(([, c]) => c), 1);

  const isAccuracy =
    'type' in answerReveal && answerReveal.type === 'fill_in_the_blank';
  const acceptedAnswers: string[] =
    isAccuracy && 'acceptedAnswers' in answerReveal
      ? (answerReveal as { type: 'fill_in_the_blank'; acceptedAnswers: string[]; editDistance: number }).acceptedAnswers.map(a => a.toLowerCase())
      : [];
  const editDistance: number =
    isAccuracy && 'editDistance' in answerReveal
      ? (answerReveal as { type: 'fill_in_the_blank'; acceptedAnswers: string[]; editDistance: number }).editDistance
      : 0;

  function sizeClass(count: number): string {
    const f = count / maxCount;
    if (f > 0.75) return 'text-5xl font-black';
    if (f > 0.5)  return 'text-4xl font-bold';
    if (f > 0.25) return 'text-3xl font-semibold';
    if (f > 0.1)  return 'text-2xl font-medium';
    return 'text-xl';
  }

  function isCorrectWord(word: string): boolean {
    if (!isAccuracy || acceptedAnswers.length === 0) return false;
    return acceptedAnswers.some(a => levenshtein(word.toLowerCase(), a) <= editDistance);
  }

  return (
    <div className="w-full flex flex-col gap-5">
      {isAccuracy && acceptedAnswers.length > 0 && (
        <p className="text-center text-secondary text-2xl">
          Correct answer:{' '}
          <span className="text-success font-bold">
            {(answerReveal as { acceptedAnswers: string[] }).acceptedAnswers.join(' / ')}
            {editDistance > 0 && (
              <span className="text-muted font-normal ml-1">(±{editDistance})</span>
            )}
          </span>
        </p>
      )}

      <div className="glass rounded-2xl flex flex-wrap gap-x-6 gap-y-4 justify-center items-center min-h-40 p-8 stagger">
        {entries.map(([word, count]) => (
          <span
            key={word}
            title={`${count} player${count !== 1 ? 's' : ''}`}
            className={`${sizeClass(count)} break-words animate-pop ${
              isCorrectWord(word) ? 'text-success' : 'text-secondary'
            }`}
          >
            {word}
          </span>
        ))}
        {entries.length === 0 && (
          <p className="text-muted text-xl">No answers submitted</p>
        )}
      </div>

      <p className="text-secondary text-xl text-right tabular-nums">
        <span className="font-bold text-primary">{totalAnswered}</span> / {totalPlayers} answered
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bar Chart — for multiple_choice, true_false, multi_select and participation options
// ---------------------------------------------------------------------------

interface BarChartProps {
  // `letter` is the option badge (A, B, ...); null for True/False rows.
  bars: { letter: string | null; text: string; count: number; correct: boolean | null; media?: React.ReactNode }[];
  totalAnswered: number;
  totalPlayers: number;
}

function AnswerBarChart({ bars, totalAnswered, totalPlayers }: BarChartProps) {
  return (
    <div className="w-full grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] items-stretch">
      <div className="glass rounded-2xl p-6 md:p-8 animate-slide-up">
        <AnswerBars
          size="lg"
          total={totalAnswered}
          emptyMessage="No answers submitted"
          bars={bars.map((b, i) => ({
            key: b.letter ?? b.text ?? String(i),
            letter: b.letter,
            label: b.text,
            count: b.count,
            correct: b.correct,
            media: b.media,
          }))}
        />
        <p className="mt-5 text-secondary text-xl text-right tabular-nums">
          <span className="font-bold text-primary">{totalAnswered}</span> / {totalPlayers} answered
        </p>
      </div>
      <div className="glass-subtle rounded-2xl p-6 flex items-center justify-center animate-slide-up" style={staggerStyle(1)}>
        <DonutStat value={totalAnswered} total={totalPlayers} label="Response rate" size={168} />
      </div>
    </div>
  );
}

/** T8: the option's image for a bar row, or nothing when the option has none. */
function optionMedia(config: QuestionConfig | undefined, urls: ImageUrls | undefined, i: number) {
  const id = config?.optionImageIds?.[i];
  if (id == null) return undefined;
  return (
    <QuestionImage id={id} urls={urls} alt={`${optionDisplay('', i)} image`} className="size-16 min-h-0 p-0 text-xs" />
  );
}

function buildBars(
  reveal: AnswerReveal,
  distribution: Record<string, number>,
  config: QuestionConfig | undefined,
  urls: ImageUrls | undefined,
): BarChartProps['bars'] {
  const options = config?.options;
  if (reveal.type === 'multiple_choice') {
    const opts = options ?? [];
    return opts.map((opt, i) => ({
      letter: optionLabel(i),
      text: optionDisplay(opt, i),
      count: distribution[String(i)] ?? 0,
      correct: reveal.correctIndices.includes(i),
      media: optionMedia(config, urls, i),
    }));
  }

  if (reveal.type === 'multi_select') {
    const opts = options ?? [];
    return opts.map((opt, i) => ({
      letter: optionLabel(i),
      text: optionDisplay(opt, i),
      count: distribution[String(i)] ?? 0,
      correct: (reveal.answerPoints[i] ?? 0) > 0,
      media: optionMedia(config, urls, i),
    }));
  }

  if (reveal.type === 'true_false') {
    return [
      { letter: null, text: 'True', count: distribution['true'] ?? 0, correct: reveal.correctValue === true },
      { letter: null, text: 'False', count: distribution['false'] ?? 0, correct: reveal.correctValue === false },
    ];
  }

  // completeness (non-FITB) — no right/wrong
  if (options && options.length > 0) {
    return options.map((opt, i) => ({
      letter: optionLabel(i),
      text: optionDisplay(opt, i),
      count: distribution[String(i)] ?? 0,
      correct: null,
      media: optionMedia(config, urls, i),
    }));
  }

  if ('true' in distribution || 'false' in distribution) {
    return [
      { letter: null, text: 'True', count: distribution['true'] ?? 0, correct: null },
      { letter: null, text: 'False', count: distribution['false'] ?? 0, correct: null },
    ];
  }

  return [];
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ResultsPage() {
  const { questionResults, currentQuestion, autoAdvance, emitAdvance } = useGame();
  const [countdown, setCountdown] = useState(RESULTS_DISPLAY_SECONDS);

  const isLast = currentQuestion
    ? currentQuestion.questionNumber >= currentQuestion.totalQuestions
    : false;

  useEffect(() => {
    if (!autoAdvance) return;
    setCountdown(RESULTS_DISPLAY_SECONDS);
    const interval = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) { clearInterval(interval); emitAdvance(); return 0; }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [autoAdvance, questionResults, emitAdvance]);

  const isFitb = currentQuestion?.type === 'fill_in_the_blank';
  // Both payloads carry the same question's map; merge in case one arrived without it.
  const urls: ImageUrls = { ...currentQuestion?.imageUrls, ...questionResults?.imageUrls };
  const isNew = isNewType(currentQuestion?.type);

  const bars =
    !isFitb && !isNew && questionResults
      ? buildBars(
          questionResults.answerReveal,
          questionResults.answerDistribution,
          currentQuestion?.config,
          urls,
        )
      : [];

  return (
    <div className="flex-1 w-full max-w-6xl mx-auto flex flex-col items-center justify-center gap-6 px-6 py-6 md:px-10">
      <div className="flex flex-col items-center gap-3 text-center animate-fade-in">
        {currentQuestion && (
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Badge tone="accent" size="lg" className="tabular-nums">
              {currentQuestion.questionNumber} of {currentQuestion.totalQuestions}
            </Badge>
            <Badge tone={currentQuestion.gradingType === 'COMPLETENESS' ? 'warning' : 'neutral'} size="lg">
              {currentQuestion.gradingType === 'COMPLETENESS' ? 'Participation' : 'Accuracy'}
            </Badge>
          </div>
        )}
        <h2 className="text-title text-primary">
          Question {currentQuestion?.questionNumber} Results
        </h2>
      </div>

      {currentQuestion && (
        <Card variant="question" edge="left" className="w-full px-8 py-6 animate-slide-up">
          <p className="text-heading text-primary text-center break-words">
            {currentQuestion.prompt}
          </p>
          <QuestionImage
            id={currentQuestion.config.promptImageId}
            urls={urls}
            alt="Question image"
            className="mx-auto mt-4 max-h-[25vh] w-auto max-w-full"
          />
        </Card>
      )}

      {questionResults && currentQuestion && isNew ? (
        <NewTypeResults
          type={currentQuestion.type}
          gradingType={currentQuestion.gradingType}
          reveal={questionResults.answerReveal}
          distribution={questionResults.answerDistribution}
          totalAnswered={questionResults.totalAnswered}
          totalPlayers={questionResults.totalPlayers}
          showGrid
          imageUrl={urlFor(urls, currentQuestion.config.imageId)}
        />
      ) : questionResults && isFitb ? (
        <WordCloud
          distribution={questionResults.answerDistribution}
          answerReveal={questionResults.answerReveal}
          totalAnswered={questionResults.totalAnswered}
          totalPlayers={questionResults.totalPlayers}
        />
      ) : questionResults ? (
        <AnswerBarChart
          bars={bars}
          totalAnswered={questionResults.totalAnswered}
          totalPlayers={questionResults.totalPlayers}
        />
      ) : null}

      <div className="flex flex-col items-center gap-3 pt-2">
        <Button size="xl" onClick={emitAdvance} className="px-12">
          {isLast ? 'Show Final Results' : 'Next Question'}
          {isLast ? <Trophy className="size-6" aria-hidden="true" /> : <ArrowRight className="size-6" aria-hidden="true" />}
        </Button>
        {autoAdvance && (
          <p className="text-secondary text-xl tabular-nums">
            Auto-advancing in{' '}
            <span key={countdown} className="inline-block font-bold text-primary animate-tick">{countdown}</span>s
          </p>
        )}
      </div>
    </div>
  );
}
