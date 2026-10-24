// REST shapes for the course-first host API (backend app/schemas/host.py).

export interface Course {
  id: number;
  name: string;
  semester: string;
}

export interface GameSummary {
  id: number;
  course_id: number;
  title: string;
  description: string;
  max_players: number;
  question_count: number;
  archived_at: string | null;
  has_active_room: boolean;
  created_at: string;
}

export interface ActiveRoom {
  session_id: string;
  room_code: string;
  status: 'LOBBY' | 'IN_PROGRESS';
  game_id: number;
  game_title: string;
  host_user_id: string | null;
  host_display_name: string | null;
  created_at: string;
  /** Redis room state exists; false = stale room ("Not running"). */
  live: boolean;
  player_count: number | null;
  has_answers: boolean;
  /** Distinct players with at least one saved answer (from MySQL). */
  answered_count: number;
  /** Caller started it, or is an admin. */
  can_end: boolean;
}

/** A current course image (T8, backend schemas/images.py ImageItem). */
export interface ImageItem {
  id: number;
  course_id: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  width: number;
  height: number;
  /** `/api/images/<key>`: public, cached forever (each version has its own URL). */
  url: string;
  created_at: string;
  /** Questions using it in non-archived / archived quizzes; delete needs both 0. */
  use_count: number;
  archived_use_count: number;
}

/** Editor map for one question: image id -> url, and false for an older version. */
export type QuestionImages = Record<string, { url: string; current: boolean }>;

export interface SessionItem {
  session_id: string;
  room_code: string;
  status: 'COMPLETED' | 'ABANDONED';
  game_id: number;
  game_title: string;
  host_user_id: string | null;
  host_display_name: string | null;
  created_at: string;
  completed_at: string | null;
  player_count: number;
  can_merge_guests: boolean;
}
