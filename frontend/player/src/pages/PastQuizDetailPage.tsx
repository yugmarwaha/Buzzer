import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api } from '../lib/api';
import { NotPlayedRow, QuestionRow } from '../components/QuestionRecap';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { Spinner } from '../components/ui/spinner';
import { staggerStyle } from '../components/ui/motion';
import type { PastQuizDetail } from '../types/past';

/** One finished quiz, as the questions were asked. Unreached questions show only "Not played". */
export default function PastQuizDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [quiz, setQuiz] = useState<PastQuizDetail | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .get<PastQuizDetail>(`/player/past-quizzes/${sessionId}`)
      .then(setQuiz)
      .catch((err) => {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) navigate('/past', { replace: true });
        else setError(err instanceof Error ? err.message : 'Failed to load quiz');
      });
  }, [sessionId, navigate]);

  if (error) {
    return (
      <div className="mx-auto min-h-screen max-w-lg space-y-3 p-4 pt-8">
        <p role="alert" className="text-body text-danger">{error}</p>
        <Link to="/past" className="inline-flex min-h-11 items-center text-body font-semibold text-accent-text hover:underline">← Past quizzes</Link>
      </div>
    );
  }
  if (!quiz) return <div className="flex min-h-screen items-center justify-center gap-3 text-body-lg text-secondary"><Spinner /> Loading…</div>;

  return (
    <div className="min-h-screen p-4 max-w-lg mx-auto flex flex-col gap-4">
      <Link to="/past" className="inline-flex min-h-11 items-center self-start text-body font-semibold text-accent-text hover:underline">← Past quizzes</Link>
      <Card variant="glass-strong" className="animate-slide-up rounded-3xl p-6 text-center">
        <h1 className="text-title text-primary">{quiz.game_title}</h1>
        <p className="mt-1 text-body-lg text-secondary">{quiz.course_name}</p>
        {quiz.status === 'ABANDONED' && <Badge tone="warning" size="md" className="mt-2">Abandoned</Badge>}
        <p className="mt-3 text-stat tabular-nums text-primary">
          {quiz.score.toLocaleString()} / {quiz.max_score.toLocaleString()} pts
        </p>
      </Card>
      <div className="stagger flex flex-col gap-3 pb-6">
        {quiz.questions.map((q, i) => (
          <div key={i} className="animate-slide-up" style={staggerStyle(i)}>
            {q.played ? <QuestionRow item={q} index={i} urls={quiz.image_urls} /> : <NotPlayedRow index={i} />}
          </div>
        ))}
      </div>
    </div>
  );
}
