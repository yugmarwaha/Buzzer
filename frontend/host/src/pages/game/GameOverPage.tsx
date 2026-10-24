import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Clock } from 'lucide-react';
import { useGame } from './GameLayout';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import { Spinner } from '../../components/ui/spinner';
import { staggerStyle } from '../../components/ui/motion';
import { AnswerBars, DonutStat, QuestionAccuracyChart, ScoreHistogram, type AnswerBarDatum } from '../../components/charts';
import type { AnswerReveal, HostQuestionSummaryItem, ImageUrls } from '../../types/game';
import { NewTypeResults, isNewType } from '../../components/NewTypeResults';
import { QuestionImage } from '../../components/QuestionImage';
import { optionDisplay, optionLetter } from '../../lib/utils';

// ---------------------------------------------------------------------------
// Per-question summary card
// ---------------------------------------------------------------------------

const optionLabel = optionLetter;

const TYPE_LABEL: Record<string, string> = {
  multiple_choice: 'Multiple Choice',
  true_false: 'True / False',
  fill_in_the_blank: 'Fill in the Blank',
  multi_select: 'Multi-Select',
  pin_drop: 'Pin Drop',
  closest_guess: 'Closest Guess',
};

/** Rows for AnswerBars. The letter is the badge only; the label is the option text alone. */
function buildBars(item: HostQuestionSummaryItem, urls: ImageUrls | undefined): AnswerBarDatum[] {
  const { type, gradingType, config, answerDistribution } = item;
  const reveal = item.answerReveal as AnswerReveal;
  const graded = gradingType === 'ACCURACY';

  if (type === 'multiple_choice' || type === 'multi_select') {
    return (config.options ?? []).map((opt, i) => {
      let correct: boolean | null = null;
      if (graded && 'type' in reveal && reveal.type === 'multiple_choice') correct = reveal.correctIndices.includes(i);
      if (graded && 'type' in reveal && reveal.type === 'multi_select') correct = (reveal.answerPoints[i] ?? 0) > 0;
      const imageId = config.optionImageIds?.[i];
      const media =
        imageId != null ? (
          <QuestionImage id={imageId} urls={urls} alt={`${optionDisplay('', i)} image`} className="size-12 min-h-0 p-0 text-xs" />
        ) : undefined;
      return { key: String(i), letter: optionLabel(i), label: optionDisplay(opt, i), count: answerDistribution[String(i)] ?? 0, correct, media };
    });
  }

  if (type === 'true_false') {
    const correctValue = graded && 'type' in reveal && reveal.type === 'true_false' ? reveal.correctValue : null;
    return (['true', 'false'] as const).map((val) => ({
      key: val,
      letter: null,
      label: val === 'true' ? 'True' : 'False',
      count: answerDistribution[val] ?? 0,
      correct: correctValue === null ? null : (val === 'true') === correctValue,
    }));
  }

  return [];
}

function QuestionCard({ item, index, urls }: { item: HostQuestionSummaryItem; index: number; urls: ImageUrls | undefined }) {
  const { type, gradingType, prompt, answerDistribution, totalAnswered, totalPlayers, correctCount, avgAnswerTimeMs, pointsValue } = item;
  const reveal = item.answerReveal as AnswerReveal;
  const accuracy = gradingType === 'ACCURACY';

  const answeredPct = totalPlayers > 0 ? Math.round((totalAnswered / totalPlayers) * 100) : 0;
  const correctPct = totalAnswered > 0 ? Math.round((correctCount / totalAnswered) * 100) : 0;
  const bars = buildBars(item, urls);
  const fitb = type === 'fill_in_the_blank' && 'type' in reveal && reveal.type === 'fill_in_the_blank' ? reveal : null;

  return (
    <Card
      variant="question"
      edge="left"
      tone={accuracy ? 'accent' : 'warning'}
      className="w-full p-6 pl-8 animate-slide-up"
      style={staggerStyle(index)}
    >
      <article aria-labelledby={`q-${item.questionId}-prompt`} className="flex flex-col gap-5">
        {/* Header */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex h-10 min-w-10 items-center justify-center rounded-lg bg-accent-hint px-2 text-xl font-bold text-accent-text tabular-nums">
            Q{index + 1}
          </span>
          <Badge size="md">{TYPE_LABEL[type] ?? type}</Badge>
          <Badge size="md" tone={accuracy ? 'accent' : 'warning'}>
            {accuracy ? 'Accuracy' : 'Participation'}
          </Badge>
          <Badge size="md" tone="outline" className="tabular-nums">
            {pointsValue} {pointsValue === 1 ? 'pt' : 'pts'}
          </Badge>
          <span className="ml-auto flex items-center gap-3 text-lg text-secondary tabular-nums">
            <span>
              <span className="font-bold text-primary">{totalAnswered}</span>/{totalPlayers} answered
            </span>
            {avgAnswerTimeMs !== null && (
              <span className="inline-flex items-center gap-1 text-muted">
                <Clock className="size-4" aria-hidden="true" />
                avg {(avgAnswerTimeMs / 1000).toFixed(1)}s
              </span>
            )}
          </span>
        </div>

        {/* Prompt */}
        <p id={`q-${item.questionId}-prompt`} className="text-heading text-primary break-words">{prompt}</p>
        <QuestionImage id={item.config.promptImageId} urls={urls} alt="Question image" className="max-h-48 w-auto max-w-full self-start" />

        {/* Distribution + response rate */}
        <div className="grid items-center gap-6 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            {bars.length > 0 && (
              <AnswerBars
                size="md"
                total={totalAnswered}
                bars={bars}
                emptyMessage="No answers submitted"
                title={`Answers for question ${index + 1}`}
              />
            )}

            {isNewType(type) && (
              <NewTypeResults
                type={type}
                gradingType={gradingType}
                reveal={reveal}
                distribution={answerDistribution}
                totalAnswered={totalAnswered}
                totalPlayers={totalPlayers}
                showGrid={false}
              />
            )}

            {fitb && (
              <div className="flex flex-wrap items-center gap-2 text-lg text-secondary">
                <span>Accepted:</span>
                {fitb.acceptedAnswers.map((a, i) => (
                  <Badge key={i} tone="success" size="md" className="font-mono">"{a}"</Badge>
                ))}
                {fitb.editDistance > 0 && (
                  <span className="text-muted">(±{fitb.editDistance} typo)</span>
                )}
              </div>
            )}
          </div>

          <DonutStat
            value={totalAnswered}
            total={totalPlayers}
            label="Response rate"
            size={120}
            tone={accuracy ? 'primary' : 'participation'}
            className="justify-self-center"
          />
        </div>

        {/* Footer stats */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-line pt-4 text-xl tabular-nums">
          {accuracy ? (
            <>
              <span className="font-semibold text-success">{correctCount} correct</span>
              <span className="text-secondary">{correctPct}% accuracy</span>
              <span className="text-secondary">{answeredPct}% responded</span>
            </>
          ) : (
            <>
              <span className="font-semibold text-warning">{totalAnswered} completed</span>
              <span className="text-secondary">{answeredPct}% response rate</span>
            </>
          )}
        </div>
      </article>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({ label, value, index }: { label: string; value: number; index: number }) {
  return (
    <div className="glass rounded-2xl px-5 py-4 text-center animate-slide-up" style={staggerStyle(index)}>
      <p className="text-xl font-semibold text-secondary">{label}</p>
      <p key={value} className="text-stat text-primary tabular-nums animate-tick">{value.toLocaleString()}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GameOverPage() {
  const { gameOver } = useGame();
  const navigate = useNavigate();

  if (!gameOver) {
    return (
      <div className="flex-1 flex items-center justify-center gap-3">
        <Spinner />
        <p className="text-muted text-xl">Loading final results…</p>
      </div>
    );
  }

  const { scores: answeredScores, playerCount, maxPossibleScore, questionSummary = [] } = gameOver;
  // The leaderboard only has players with a score row; a player who never answered scored 0.
  const scores = [
    ...answeredScores,
    ...Array<number>(Math.max(0, playerCount - answeredScores.length)).fill(0),
  ];

  const avg = scores.length > 0
    ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
    : 0;
  const high = scores.length > 0 ? Math.max(...scores) : 0;

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-8 px-6 py-10 md:px-10">
      {/* Hero */}
      <header className="flex flex-col items-center gap-4 text-center">
        <h1 className="text-display text-primary animate-scale-in">Game Over!</h1>
        <p className="text-secondary text-xl tabular-nums animate-fade-in">
          {playerCount} {playerCount === 1 ? 'player' : 'players'} · Max possible: {maxPossibleScore.toLocaleString()} pts
        </p>
        <Button size="xl" onClick={() => navigate('/courses')} className="animate-fade-in">
          <ArrowLeft className="size-6" aria-hidden="true" />
          Back to courses
        </Button>
      </header>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Average" value={avg} index={0} />
        <StatCard label="High score" value={high} index={1} />
        <StatCard label="Players" value={playerCount} index={2} />
      </div>

      {/* Histogram */}
      <section aria-labelledby="score-dist-title" className="glass rounded-2xl p-6 md:p-8 animate-slide-up" style={staggerStyle(3)}>
        <h2 id="score-dist-title" className="text-heading text-primary mb-4">Score distribution</h2>
        <ScoreHistogram scores={scores} maxPossibleScore={maxPossibleScore} />
      </section>

      {/* Per-question breakdown */}
      {questionSummary.length > 0 && (
        <section aria-labelledby="breakdown-title" className="flex flex-col gap-5 pb-8">
          <h2 id="breakdown-title" className="text-heading text-primary">Question breakdown</h2>
          <div className="glass rounded-2xl p-6 md:p-8">
            <QuestionAccuracyChart
              questions={questionSummary.map((q, i) => ({
                id: q.questionId,
                number: i + 1,
                prompt: q.prompt,
                kind: q.gradingType === 'ACCURACY' ? 'accuracy' : 'participation',
                answered: q.totalAnswered,
                total: q.totalPlayers,
                correct: q.correctCount,
              }))}
            />
          </div>
          {questionSummary.map((item, i) => (
            <QuestionCard key={item.questionId} item={item} index={i} urls={gameOver.imageUrls} />
          ))}
        </section>
      )}
    </div>
  );
}
