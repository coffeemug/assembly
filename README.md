# Election game MVP

Two AI representatives negotiate for polarized human factions. One initial dispute,
no injected events, simultaneous decisions, and a maximum number of rounds.

## Run one game

Requires [uv](https://docs.astral.sh/uv/). Dependencies are declared in
`pyproject.toml` and pinned in `uv.lock`; `uv run` creates the virtual
environment and installs them on first use, so no separate setup step is needed.
`.python-version` pins the interpreter (3.13); uv downloads it if missing.

Edit `.env` and fill in `OPENROUTER_API_KEY`. If starting from a fresh clone,
copy `.env.example` to `.env` first. Configuration initially contains:

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL_A=anthropic/claude-opus-5
OPENROUTER_MODEL_B=anthropic/claude-opus-5
ROUNDS=20
REASONING_EFFORT=medium
```

Each faction plays with its own OpenRouter model: `OPENROUTER_MODEL_A` for A and
`OPENROUTER_MODEL_B` for B. Set them to the same ID for self-play or to different IDs
to pit two models against each other. `OPENROUTER_MODEL` is used for either faction
whose variable is unset. Models need reasoning and strict JSON-schema response support.
Model identities are always hidden from the agents; prompts identify factions only as A and B.
Model IDs remain available in the operator's console and saved configuration. Reasoning effort
can be low, medium, or high. Requests allow 8192 tokens for reasoning and output;
`temperature: 1.0` is explicitly sent (provider support determines whether it is applied). Exported environment variables
override `.env`. Requests connect over IPv4 before IPv6 with a 10-second connect
timeout per address, so a broken IPv6 route cannot stall the game. A run makes two paid API requests per round (40 requests at the 20-round limit).

```sh
uv run game.py
```

Optional paths: `uv run game.py --env /path/to/.env --output /path/to/runs`.

## Run the Fable/Astra batch

```sh
uv run run_batch.py
```

Starts all 30 games in parallel: 10 Fable self-play, 10 Astra self-play,
5 Astra (A) vs Fable (B), and 5 Fable (A) vs Astra (B).
Uses `anthropic/claude-fable-5.1` and `openai/gpt-6-astra`; rounds, reasoning
effort, and API key come from the usual environment configuration.
All processes are launched before waiting for any result, with up to 60 API
requests in flight. At 20 rounds, the batch can make up to 1,200 paid requests;
retries can add more. Provider rate limits still apply. Each faction's API call
uses the bounded retry policy described below.

Each batch creates `runs/fable-astra-batch-<id>/` with a labeled directory per
game containing `stdout.log` (including stderr) and the usual timestamped game
logs. A failed game does not stop the others; the batch exits nonzero if any fail.
Ctrl-C stops remaining children and retains partial logs, which may still show
status `running` because those processes are killed.
Optional paths: `--env /path/to/.env --output /path/to/runs`.

## Mechanics

Balance and tension each range from 0 to 10 and start at 5. Balance 0 means
A controls all power; balance 10 means B controls all power. A's power is
10 minus balance, and B's power is balance. Public `tension_momentum` is sampled
uniformly between 0.0 and 1.0 once at game start. Both agents see the exact value,
which is saved in the initial state and evolves deterministically from actions
afterward. Tension and momentum use fractional values.

Both factions choose ESCALATE or DEESCALATE simultaneously, receiving identical
public history and their own system prompt. Public messages are nonbinding.

| A / B | Balance change | Tension pressure |
| --- | --- | --- |
| Escalate / deescalate | -1 (toward A) | +1 |
| Deescalate / escalate | +1 (toward B) | +1 |
| Escalate / escalate | 0 | +2 |
| Deescalate / deescalate | 0 | -1 |

Each round first updates momentum, then tension:

```text
tension_momentum = 0.7 * tension_momentum + 0.3 * pressure
tension = clamp(tension + tension_momentum, 0, 10)
```

Positive momentum raises tension; negative momentum lowers it. Mutual
de-escalation with momentum +1 changes momentum to +0.4, so tension still
rises by 0.4. Another round of mutual de-escalation changes momentum to -0.02,
beginning a decline. Momentum is not reset when tension is clamped at 0.

Values are clamped to 0–10. Balance 0 or 10 ends the game with irreversible
authoritarian control by A or B respectively. Tension 10 ends it with civil war.
Both outcomes are recorded if thresholds coincide. Otherwise the game ends at
the configured round limit. These labels are defined by the toy game's rules.

Adjust the rules section of `prompts/faction.md` and `resolve()` in `game.py`
together when changing the mechanics. The runner loads the faction prompt at the
start of each game, relative to `game.py`.
The template substitutes `{faction}`, `{opponent}`, and `{rounds}`.
Escape literal braces as `{{` and `}}` when editing the template.

## Logs

Each new game creates a unique `runs/<UTC timestamp>-<id>/` directory:

- `game.json`: the whole game. It opens with a `config` section (model, rounds,
  sampling parameters, response schema, exact system prompts, prompt template,
  successor mandate, warning text, start time), followed by analysis-ready round records (before
  state, both decisions, after state, replacements), final state, outcomes, end
  reason, completion status, and errors. Rewritten after every round.
- `debug.jsonl`: timestamped, append-only exact request payloads, full API
  response bodies (including returned model, usage, and any provider metadata),
  validated decisions, retries, state changes, latency, and errors. Each event is
  flushed to disk. Response `body` is a string; parse it as JSON to extract usage.
- `.game.lock`: advisory lock preventing simultaneous runners from writing the same game.

The API key is not logged. `.env` and `runs/` are gitignored. No private explanation
is requested. API-returned extra fields are retained in the raw response.

Temporary API failures are retried independently for each faction, up to five
attempts total. This covers HTTP 408, 429, and 5xx errors, network/timeouts and
incomplete response reads, and provider overload errors inside HTTP 200 responses.
Backoff uses randomized delays of 1-2, 2-4, 4-8, then 8-16 seconds. A valid
`Retry-After` header adds the server's requested delay before that jittered wait.
Each attempt and retry delay is recorded in `debug.jsonl`; the retry policy is
saved in `game.json`. The exact request is reused, and a successful opponent
response is retained while the other faction retries. No round resolves until
both decisions succeed.

After retries are exhausted, the game stops with a nonzero exit code; other
batch games continue. Permanent HTTP errors (such as authentication or payment
failures), malformed JSON, invalid actions, and truncated model completions are
not retried. Failed responses and completed rounds remain available. A message
longer than 80 words is cut to its first 80 words rather than retried; the full
response stays in `debug.jsonl`. Retries may incur additional charges, including
when a timed-out request was processed upstream. Rerunning without `--resume`
still starts a new game. Calls time out after 120 seconds per attempt.
Force-killing a process may leave status `running`; its existing event log remains.
Logs support inspection and deterministic replay of state transitions, not exact
reproduction of stochastic model outputs.

## Resume a stopped game

```sh
uv run game.py --resume runs/<run-id>
```

For a batch game, point to its timestamped directory containing `game.json`
and `debug.jsonl`, not the batch root or matchup directory. For example:

```sh
caffeinate -i uv run game.py --resume runs/fable-astra-batch-ay10t_sj/fable-self-02/20260907T180633Z-1f5e7d3b
```

Resume updates the original game in place and appends to its event log. It
preserves completed rounds, tension momentum, dissatisfaction, representative
identities, public history, original prompts, model assignments, reasoning
effort, and round limit. Current `.env` model/round settings are ignored; only
the API key is read (with the usual `--env` option). Missing decisions make paid
API calls using the existing retry policy; this does not remove API budget limits.

A validated response already logged for the interrupted round is reused only
when its saved request matches the restored round. Only missing faction
decisions are requested; neither sees the other's pending response. Completed
games are skipped without API calls. A failed resume can be resumed again.
Previous failures remain in the append-only log, along with resume/reuse events.

Stop the original process before resuming, especially for older runs created
before locking was added. Failed, interrupted, and stopped games still marked
`running` can be resumed. This supports schema version 5 and the current game
mechanics; malformed/truncated event logs and unsupported saved API settings are
refused rather than silently repaired. Do not edit a run's saved configuration.

## Offline checks

```sh
uv run -m unittest -v
```

Tests live in `tests/`. To run one module, use
`uv run -m unittest -v tests.test_api_retries` (or `tests.test_game`,
`tests.test_run_batch`, `tests.test_resume`).

Tests cover the payoff table, tension momentum and delayed reversal, faction symmetry, bounds, invalid responses,
concurrent requests with equal information, and complete/failed run logging.
They use a mocked API and incur no charges.

API reference: [OpenRouter chat completions](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request).

## Plot a saved game

```sh
uv run plot_game.py runs/<run-id>
```

Writes `plot.png` and `plot.svg` in that run's directory, showing balance,
tension, and both factions' actions. Plotting makes no API calls.

`uv run plot_tension.py` draws a spaghetti plot of tension by round for every game in
`runs/` that completed exactly 20 rounds (`--rounds` changes that), colored by matchup,
and writes `runs/tension_spaghetti.png` and `.svg`.
`--smooth N` plots an N-round rolling mean and `--curve` draws smooth curves through the values.

`uv run plot_actions.py` draws two charts from the same games: `runs/actions_strip.png`,
one row per side per game showing who escalated each round with dismissals marked, and
`runs/outcomes_dumbbell.png`, dismissals per game and escalation rate for each side of
every matchup.

`uv run plot_occupancy.py` draws `runs/tension_occupancy.png`, the share of rounds each
matchup spent at each tension level, and `runs/tension_heatmap.png`, mean tension by round
per matchup.


## Constituent pressure and replacement

Dissatisfaction starts at 5 for each faction. A power gain reduces it by 2; a loss
raises it by 2. Unchanged power raises it by 1 when power is at most 5, and leaves
it unchanged when ahead. Meters are clamped to 0–10.

At 10, if play continues, the representative is replaced. The successor starts one
point of dissatisfaction higher than its predecessor did (6 for the second
representative, 7 for the third, and so on), capped at 9 so that replacement alone
never triggers another replacement. Successors retain public history and receive `prompts/successor.md`
in addition to the faction prompt. IDs (A-1, A-2, etc.) distinguish representatives.
A representative whose dissatisfaction is 9 or more at the start of a round also
receives `prompts/warning.md` in its system prompt for that round. Warnings are printed
to the console, listed per round in `game.json` under `warned`, and logged as
`warning_issued` events in `debug.jsonl`.
Terminal outcomes and the round limit take precedence over appointing successors.

Schema version 5 adds `tension_momentum` to the initial, per-round, and final
states and uses momentum-based tension mechanics. Existing saved runs are unchanged.
Schema version 4 folds the former `config.json` into `game.json` and drops the
runner source snapshot. Schema version 3 logs dissatisfaction before resets, replacement events,
representative IDs, and the successor mandate. Plots show pressure and reset jumps.

Responses request a strict JSON schema with an action enum and a plain string message; the 80-word limit is enforced by the prompt and local truncation, not by the schema. Earlier runs used JSON-object mode.
