"""Socket.io event name constants."""

# Client → Server
JOIN_ROOM = "join_room"
REJOIN_ROOM = "rejoin_room"
HOST_ADVANCE = "host_advance"
HOST_LOCK_QUESTION = "host_lock_question"
SUBMIT_ANSWER = "submit_answer"

# Server → Client (broadcast to all in room)
NEW_QUESTION = "new_question"
QUESTION_RESULTS = "question_results"
GAME_OVER = "game_over"
PLAYER_JOINED = "player_joined"
PLAYER_LEFT = "player_left"
HOST_DISCONNECTED = "host_disconnected"
HOST_RECONNECTED = "host_reconnected"
GAME_ABANDONED = "game_abandoned"

# Server → specific client
ANSWER_RECEIVED = "answer_received"
SYNC_STATE = "sync_state"
ERROR = "error"

# Server → Host only
ANSWER_STATUS = "answer_status"
ANSWER_PHASE_ENDED = "answer_phase_ended"

# Server → all clients in room
QUESTION_LOCKED = "question_locked"
QUESTION_UNLOCKED = "question_unlocked"
