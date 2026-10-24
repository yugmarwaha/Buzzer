// Past Quizzes shapes (backend app/schemas/player.py).
import type { ImageUrls, QuestionSummaryItem } from './game';

export interface PastQuizItem {
  session_id: string;
  game_title: string;
  course_name: string;
  status: 'COMPLETED' | 'ABANDONED';
  completed_at: string | null;
  score: number;
  max_score: number;
}

export type PlayedQuestion = Pick<
  QuestionSummaryItem,
  'prompt' | 'type' | 'config' | 'pointsAwarded' | 'isCorrect' | 'playerAnswer' | 'answerReveal'
> & {
  order_index: number;
  played: true;
  gradingType: QuestionSummaryItem['gradingType'];
  maxPoints: number;
};

/** Exactly this for a question the game never reached — nothing else is sent. */
export interface UnplayedQuestion {
  order_index: number;
  played: false;
}

export interface PastQuizDetail extends PastQuizItem {
  questions: (PlayedQuestion | UnplayedQuestion)[];
  /** T8: images of the played questions only. */
  image_urls?: ImageUrls;
}
