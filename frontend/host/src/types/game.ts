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

export interface PlayerInfo {
  userId: string;
  displayName: string;
}

export interface SyncStatePayload {
  status: 'LOBBY' | 'IN_PROGRESS' | 'COMPLETED' | 'ABANDONED';
  players: PlayerInfo[];
  playerCount: number;
  questionPhase?: 'QUESTION' | 'RESULTS' | null;
  currentQuestion?: QuestionPayload | null;
  questionLocked?: boolean;
  timerRemainingSeconds?: number | null;
  // Rebuilt server-side so a page reload restores the current screen:
  answeredCount?: number;                       // current question
  allAnswered?: boolean;                        // QUESTION phase
  questionResults?: HostResultsPayload | null;  // RESULTS phase
  gameOver?: HostGameOverPayload | null;        // status COMPLETED
}

export interface PlayerJoinedPayload {
  userId: string;
  displayName: string;
  playerCount: number;
}

export interface PlayerLeftPayload {
  userId: string;
  playerCount: number;
}

export interface QuestionPayload {
  questionId: number;
  questionNumber: number;
  totalQuestions: number;
  type: QuestionType;
  gradingType: 'ACCURACY' | 'COMPLETENESS';
  prompt: string;
  config: QuestionConfig;
  timeLimitSeconds: number;
  pointsValue: number;
  editDistance?: number;
  startedAt?: string;  // ISO timestamp — only present in sync_state reconnect payloads
  imageUrls?: ImageUrls;
}

export interface AnswerStatusPayload {
  questionId: number;
  userId: string;
  displayName: string;
  answered: boolean;
  answeredCount: number;
  totalPlayers: number;
}

export interface AnswerPhaseEndedPayload {
  questionId: number;
  answeredCount: number;
  totalPlayers: number;
  allAnswered?: boolean; // true only when every player answered (not just timer expiry)
}

export interface LeaderboardEntry {
  userId: string;
  displayName: string;
  score: number;
  rank: number;
}

export type AnswerReveal =
  | { type: 'multiple_choice'; correctIndices: number[] }
  | { type: 'true_false'; correctValue: boolean }
  | { type: 'fill_in_the_blank'; acceptedAnswers: string[]; editDistance: number }
  | { type: 'completeness' }
  | { type: 'multi_select'; answerPoints: number[] }
  | { type: 'pin_drop'; target: { x: number; y: number } }
  | { type: 'closest_guess'; correct: number; fullTolerance: number; partialTolerance: number; unit: string | null }
  | Record<string, never>;

export interface HostResultsPayload {
  questionId: number;
  answerReveal: AnswerReveal;
  answerDistribution: Record<string, number>;
  totalAnswered: number;
  totalPlayers: number;
  imageUrls?: ImageUrls;
}

export interface HostQuestionSummaryItem {
  questionId: number;
  questionNumber: number;
  prompt: string;
  type: QuestionType;
  gradingType: 'ACCURACY' | 'COMPLETENESS';
  config: QuestionConfig;
  pointsValue: number;
  answerReveal: AnswerReveal;
  answerDistribution: Record<string, number>;
  totalAnswered: number;
  totalPlayers: number;
  correctCount: number;
  avgAnswerTimeMs: number | null;
}

export interface HostGameOverPayload {
  scores: number[];
  playerCount: number;
  maxPossibleScore: number;
  questionSummary: HostQuestionSummaryItem[];
  /** Images of every summary question's snapshot config (old versions included). */
  imageUrls?: ImageUrls;
}

export type HostPhase = 'lobby' | 'question' | 'results' | 'gameover';
