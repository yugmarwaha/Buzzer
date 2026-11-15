# Load testing

`scripts/load_test.py` plays one whole game with N simulated players and reports latency
percentiles. It also checks that nothing was lost. Read `docs/realtime.md` first for the events
it measures.

## How to run it

```bash
# Isolated stack: production mode, its own database, WORKERS backend processes
WORKERS=4 docker compose -f docker-compose.loadtest.yml up -d --build --wait

python scripts/load_test.py --base-url http://localhost:8020 \
    --stress-key local-load-test --players 500 --over 50

docker compose -f docker-compose.loadtest.yml down -v
```

It also runs against the dev stack (`--base-url http://localhost:8000`, no `--stress-key`), but
dev mode logs every SQL statement and runs one process, so use the isolated stack for numbers.

Options: `--players N` (also the quiz's `max_players`), `--over K` (K more joins than the limit,
which must all be refused), `--questions Q`, `--think-max S` (each player waits 0..S seconds
before it answers; `0` makes every player answer at the same instant), `--json FILE`.

## What it measures

| Metric | From | To |
|---|---|---|
| `join` | player sends `join_room` | player gets `sync_state` |
| `question_fan_out` | host sends `host_advance` | each player gets `new_question` |
| `answer_round_trip` | player sends `submit_answer` | player gets `answer_received` (after the MySQL commit) |
| `results_fan_out` | host sends `host_advance` | each player gets `question_results` |

All clients run in one Python process on the same machine as the server. The numbers include
the client's own event-loop time and no real network, so compare runs with each other; do not
read them as internet latency.

## What it checks

A run passes only if all of these hold:

- Every answer got exactly one `answer_received`, with the points the test expected.
- The host's live counter reached the number of players on every question.
- Each player's final score in `game_over` (read from MySQL) equals the sum of the points the
  server acknowledged during play (kept in Redis).
- Exactly `--over` joins were refused with "This room is full".
- No client received an `error` event.

## Results

Apple M4, Docker with 10 CPUs and 12 GB, MySQL 8.0, Redis 7.0, Python 3.12. One room, 5
multiple-choice questions, 10% extra joins over the limit. Every run passed.
Latency is `answer_round_trip` in milliseconds.

**Spread answers** (`--think-max 3`, players answer within 3 seconds):

| Workers | Players | Answers recorded | p50 | p95 | p99 | Answers/s | Joins refused |
|---|---|---|---|---|---|---|---|
| 1 | 250 | 1,250 / 1,250 | 8.0 | 13.3 | 23.3 | 83 | 25 / 25 |
| 1 | 500 | 2,500 / 2,500 | 6.9 | 11.4 | 14.9 | 166 | 50 / 50 |
| 4 | 250 | 1,250 / 1,250 | 8.0 | 12.2 | 16.8 | 83 | 25 / 25 |
| 4 | 500 | 2,500 / 2,500 | 6.9 | 10.7 | 13.4 | 166 | 50 / 50 |

Here the answer rate is set by the players, not the server, so more workers change little.

**Burst** (`--think-max 0`, every player answers at the same instant):

| Workers | Players | Answers recorded | p50 | p95 | p99 | Answers/s |
|---|---|---|---|---|---|---|
| 1 | 250 | 1,250 / 1,250 | 358 | 634 | 671 | 390 |
| 1 | 500 | 2,500 / 2,500 | 665 | 1,106 | 1,153 | 421 |
| 4 | 250 | 1,250 / 1,250 | 179 | 283 | 311 | 771 |
| 4 | 500 | 2,500 / 2,500 | 333 | 556 | 588 | 785 |

In a burst the server is the limit. Four workers record answers 1.9 times as fast as one
(785 against 421 per second at 500 players) and halve the p95 (556 ms against 1,106 ms).

Question fan-out to 500 players had a p95 of 30 to 43 ms in every run.

## What the load test found

The first burst runs failed: players waited for an `answer_received` that never came. The
backend log showed `redis.exceptions.MaxConnectionsError: Too many connections`. redis-py's
default connection pool holds 100 connections and raises as soon as all are in use; it does not
wait. More than 100 `submit_answer` handlers ran at once, so the extra handlers died before they
recorded the answer, with no reply to the player. In the two failed runs (250 and 500 players,
first question only) the log has 428 such dropped answers out of 750 sent. Players leaving all
at once failed the same way in the disconnect handler.

The fix is `redis_client.create_redis`: a `BlockingConnectionPool`, so a handler waits for a
free connection (`REDIS_MAX_CONNECTIONS`, `REDIS_POOL_WAIT_SECONDS`). After it, every burst run
above records every answer. `tests/unit/test_real_redis.py` keeps both sides: 300 concurrent
claims through a 5-connection blocking pool all succeed, and the same burst through the default
pool fails.

## Limits of these numbers

- One machine. The clients take CPU from the server, and there is no network delay.
- One room. Many rooms at once were not measured.
- WebSocket transport only. With several workers, clients that fall back to HTTP long-polling
  need sticky sessions at the proxy; the tracked nginx config has none.
- Question timers are local to the worker that started them (`backend/app/websocket/CLAUDE.md`).
  The runs here never reconnect a host to a different worker mid-question.
