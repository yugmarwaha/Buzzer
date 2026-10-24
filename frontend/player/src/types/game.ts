export type QuestionType =
  | 'multiple_choice'
  | 'true_false'
  | 'fill_in_the_blank'
  | 'multi_select'
  | 'pin_drop'
  | 'closest_guess';

/** Public question config. T8 image fields hold image IDs; resolve them through the
 *  payload's `imageUrls` map, never by building URLs. */
export interface QuestionConfig {
  options?: string[];
  maxLength?: number;
  unit?: string;
  promptImageId?: number;
  /** Aligned with `options`; null = that option has no image. */
  optionImageIds?: (number | null)[];
  /** pin_drop canvas background. */
  imageId?: number;
}

/** Image id (as a string) -> `/api/images/<key>`. A referenced id missing here was removed. */
export type ImageUrls = Record<string, string>;

/** Answer shapes per type; pin_drop sends {x, y}, closest_guess sends {value} (T7). */
export interface PlayerAnswer {
  selectedIndex?: number;
  selectedValue?: boolean;
  text?: string;
  selectedIndices?: number[];
  x?: number;
  y?: number;
  value?: number;
}

export interface SyncStatePayload {
  status: 'LOBBY' | 'IN_PROGRESS' | 'COMPLETED' | 'ABANDONED';
  playerCount: number;
  questionLocked?: boolean;
  // Lets a reload restore the current screen:
  questionPhase?: 'QUESTION' | 'RESULTS' | 'COMPLETED' | null;
  hasAnswered?: boolean;
  questionResults?: PlayerResultsPayload | null;  // RESULTS phase
  gameOver?: PlayerGameOverPayload | null;        // status COMPLETED (players who took part)
  // The current question (client-safe, as in new_question) and this player's OWN answer to
  // it, sent in RESULTS or once answered, so a reload keeps "You answered: ...".
  question?: QuestionPayload | null;
  yourAnswer?: { questionId: number; answerData: Record<string, unknown> } | null;
}

export interface PlayerJoinedPayload {
  playerCount: number;
}

export interface QuestionPayload {
  questionId: number;
  questionNumber: number;
  totalQuestions: number;
  type: QuestionType;
  prompt: string;
  config: QuestionConfig;
  timeLimitSeconds: number;
  pointsValue: number;
  questionLocked?: boolean;  // only on a late-join/reload push of an already locked question
  imageUrls?: ImageUrls;
}

export interface AnswerResultPayload {
  questionId: number;
  isCorrect: boolean;
  pointsAwarded: number;
  totalScore: number;
  alreadyAnswered?: boolean;
}

export type PlayerAnswerReveal =
  | { type: 'multiple_choice'; correctIndices: number[] }
  | { type: 'true_false'; correctValue: boolean }
  | { type: 'fill_in_the_blank'; acceptedAnswers: string[]; editDistance: number }
  | { type: 'completeness' }
  | { type: 'multi_select'; answerPoints: number[] }
  | { type: 'pin_drop'; target: { x: number; y: number } }
  | { type: 'closest_guess'; correct: number; fullTolerance: number; partialTolerance: number; unit: string | null };

export interface PlayerResultsPayload {
  questionId: number;
  answerReveal: PlayerAnswerReveal;
  yourPoints: number;
  yourScore: number;
  yourRank: number;
  playerCount: number;
  imageUrls?: ImageUrls;
}

export interface QuestionSummaryItem {
  questionId: number;
  prompt: string;
  type: QuestionType;
  gradingType: 'ACCURACY' | 'COMPLETENESS';
  config: QuestionConfig;
  pointsAwarded: number;
  maxPoints: number;
  isCorrect: boolean; // backend scoring: full points only (the recap shows any points as ✓)
  answerTimeMs: number | null;
  playerAnswer: PlayerAnswer | null;
  answerReveal: PlayerAnswerReveal;
}

export interface PlayerGameOverPayload {
  yourFinalScore: number;
  yourFinalRank: number;
  playerCount: number;
  questionSummary: QuestionSummaryItem[];
  /** Images of every summary question's snapshot config (old versions included). */
  imageUrls?: ImageUrls;
}

export type PlayerPhase = 'lobby' | 'question' | 'feedback' | 'results' | 'gameover';
