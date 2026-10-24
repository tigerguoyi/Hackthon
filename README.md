# Touchline — Premier League 2026/27 replay and squad explorer

## Ne_Generator: A-generator integration with B's two-step model

This integration is based on B commit `61aefb91602d4e1236383ce7dfede3cd64bb1d3b`
and the read-only A source `96d13a377e98602c9998e3ebb82186843762febf`.
It does not replace B's generator, original league, enriched-v1 results or Q2
function bodies. See [the compatibility audit](docs/role-b-compat-audit.md).

Double-click **启动Ne_Generator.command**, or run `python3 server.py` and open
`http://127.0.0.1:5173/team-integration.html`. A free port is selected by the
launcher. The page includes the three pinned A/B/C cases, every registered new
match's decision snapshots, editable **neutral** simulated foot settings,
structured evidence, a separately revealed historical choice and a separate
counterfactual pass timeline. Python 3.9+ standard library is sufficient at runtime;
no API key, training package or external model is needed.

The primary ranking remains **B Q2**: tactical outcome assumptions, two geometric
action steps and newly fitted 12×8 xT tail value. Internally:
`Q = immediate goal contribution + own continuation - opponent continuation risk`.
The next decision takes a maximum and can be optimistic. Counterfactual outcomes
are unobserved; no rank is asserted to be the real optimal football action.
Pass/shot model priors are not player-specific calibrated success probabilities.

New A data contains 40 matches and 91,031 generator decision snapshots. All are
indexed independently in `data/league-enriched-gen-96d13a37/`; only four matches
have full animation. The new snapshot coverage must not be confused with B's old
5,377 covered event-derived states. Dataset/model manifests and snapshot/candidate
IDs prevent mixing these versions. No missing full trajectory is invented.

The public contract publishes ranks, textual opportunities/risks and citations,
not Q2, value gain, scenario weights or Softmax numbers. The legacy numerical
dashboard remains an explicitly separate **internal research** view. New numerical
diagnostics are local CLI output only:

```sh
python3 -m role_b.diagnostics --case a
```

Source-data Sofascore indices are descriptive observations with collection time,
radar kind and identity conflicts, not simulated player profiles or action
probabilities. Generator style multipliers model **choice preference**, not
execution success or Q2; this integration never multiplies them into success.
Unknown foot/weak-foot values remain null. Even a supplied simulated foot setting
is kept neutral until a defensible execution-window model is validated. Source
observations are never rewritten, and collection time is not historical evidence.

Current public support: legal ground passes, three standard carries and ordinary
non-goalkeeper shots. High balls and hold remain explicitly unsupported, unranked
and non-rolloutable. Offside is checked at release, using active IDs and explicit
directions. A's existing four-case pass rollout receives the **exact B ranked
target**, ball origin and 14 m/s template. Four scenarios are not probability
samples and never update the ranking. Actual carry intent is unknown when the
generator draws its target only after logging the action-type choice.

### Team interfaces

- `POST /api/role-b/analysis` (aliases `/api/decision`, `/api/compare-actions`): exact
  `contracts/schema.json` analysis request, resolving a registered snapshot.
- `POST /api/role-b/routes`, `/actual`, `/agents`: `{request,candidate_set_id}`.
- `POST /api/role-b/rollout` (alias `/api/rollout`):
  `{request,candidate_set_id,action_id}`. Only eligible passes are accepted.
- `GET /api/role-b/catalog`, `/decisions?match_id=...`,
  `/snapshot?match_id=...&snapshot_id=...`: observed source lookup.
- Python `role_b.comparison.compare_actions` is C's common tool entry; the second
  return value is private diagnostics and must never be forwarded as a public
  response. `accept_agent_response` rejects a mismatched candidate set. No actual
  Foundry/model agent is configured: computed candidates remain available with
  `model_unavailable`, null explanation and an empty trace, never fabricated prose.

Canonical JSON and candidate-set hashing reuse A's `contracts/model.py` unchanged.
Only `is_actual` may change without invalidating the candidate set. Profile,
model/parameter binding, candidates or evidence changes invalidate it. Future
events, endpoints, outcomes and historical selection are excluded from the
comparison signature and only enter the separate post-computation display path.

### Independent offline pipeline

The existing `enriched_pipeline.py` is deliberately legacy-only. Use Python 3.12
for offline fitting (the viewer still needs only Python 3.9+). Use the new
parameterised pipeline for A's different inputs; never point the old pipeline at
new data or reuse its xT/teacher/cache as if they were new-source results.

```sh
python3.12 -m venv .venv-role-b
. .venv-role-b/bin/activate
python -m pip install -r requirements-role-b.txt
python -m role_b.pipeline audit --source /path/to/pinned-A/data/league
python -m role_b.pipeline train --source /path/to/pinned-A/data/league
python -m role_b.pipeline score --all-decisions
python -m role_b.pipeline validate

# Development schema/tests (npm ci, or pnpm install, in B only)
npm ci
npm run test:contract
npm run validate:schema
npm run validate:role-b
npm run validate:legacy
npm test
```

Four matchweek folds retain 20 fit / 10 calibration / 10 test matches. Every xT
table and pass baseline is newly fitted; the main tactical probabilities remain
B's explicit assumptions. Pass teacher probabilities are A's pre-sampling labels,
never input features. Actual resolved pass outcomes are validation labels only.
New pre-action decision streams support spatial fitting; outcome-selected legacy
previews are not substituted. Display temperatures use calibration matches only.
The default `score` command writes a periodic audit sample with full on-demand
evaluation; `--all-decisions` materialises every source decision using four workers.
No future rollout branches are stored for all candidates. Private fitting samples
are ignored, new models and source snapshots are versioned separately.

All original SHA-256 anchors are checked again during validation. Results and
limitations are recorded in [the validation report](docs/role-b-validation.md).
Hold value, high-ball physics, individual weak-foot/radar causal effects and a live
C agent are intentionally not claimed. Independent football review and licensed
tracking/event validation are still needed before real-football quality claims.

A dependency-free football data demo for match replay, event consumption, live statistics and squad browsing. Club names, 2026/27 squad numbers and player details are real; every match result, event and tracking frame is procedurally generated.

## Frontend

The Touchline dashboard reads `data/league/` directly. No build step, CDN or third-party package is required.

```sh
# From the project root (Python 3)
python3 server.py

# Or use the npm shortcut
npm run dev
```

Open <http://127.0.0.1:5173>. Serve the folder over HTTP rather than opening `index.html` directly: the browser needs to load JSONL files and a module Worker.

### Views

The default landing page is **Home**. Choose any of the 20 clubs and click
**Set home club**; the browser remembers this choice. The simulation clock defaults
to **the start of matchweek 3 (7 November 2026)**, independently of the computer's
clock. Home shows form, results, goals, points and league position using only weeks
1–2; week 3 is the next fixture and week 4 is on the horizon. Future scores never
enter these homepage calculations. Click a past result to open its match report.
The match explorer defaults to week 3 (your club's match when selected); explicit
`?match=` links still open the requested replay. The explorer remains an archive
of all generated matches. `CURRENT_MATCHWEEK` in `club-home.js` sets the clock.

- **Select match** — filter 40 fixtures by matchweek, or use previous/next. `?match=<match_id>` deep-links a match.
- **Overview** — score, possession, shots, passing, interceptions, pressure events, event feed and player focus.
- **Replay** — smooth 5 Hz tracking for players and the ball, 1× / 4× / 10× speed, timeline scrubbing and ±10 second controls. Red cards remove players; substitutions switch to the incoming player's identity. Space toggles playback; arrow keys jump 10 seconds.
- **Analysis** — an English post-match report, first- and second-half review, match progression, Player of the Match with evidence and leading performers, plus final match statistics.
- **Lineups** — matchday XI plus substitutes, appearance status, cards and simulated distance.
- **Squads** — a new league-wide tab with all 20 clubs' 2026/27 rosters. Pick a club, search by player, position or nationality, and browse players grouped by goalkeepers, defenders, midfielders and forwards.

The replay starts at the selected match's `preview-60s.json` clip. Matches marked `tracking: "full"` continue loading the complete `tracking.jsonl` in a Web Worker; all other matches remain a 60-second preview. If full tracking fails, the preview stays usable. Selecting a match reloads its `metadata`, `summary`, `events`, `states` and `preview` files.

### Independent match-summary model service

Run `npm run dev` (or `python3 server.py`) and open **Settings**. Enter an HTTPS
Responses API base URL, model ID and API key, then save. The service must support
structured JSON output. In **Analysis**, click **Generate model summary** to write
and save a new full-match and half-by-half review for the selected completed match.
The request sends the computed match report to your configured provider and may
incur provider usage charges. Scores, statistics, evidence and Player of the Match
remain computed locally. Failed requests leave the existing report intact.

`model_service.py` owns model narration independently of `analysis_agent.py`.
`server.py` provides same-origin settings and generation APIs on loopback only.
The key is saved in `.local/model-service.json` with owner-only file permissions;
this directory is ignored by Git and blocked from static serving. Settings responses
only expose whether a key exists. Leave the key blank to retain it, or choose
**Remove saved API key**. Changing the service URL requires entering a key again.
Do not serve this directory with a generic static server after saving credentials.
Pure static hosting can still show pre-generated reports but cannot use settings
or generate new model summaries. This server is intended for local personal use.

### Post-match analysis agent

The project interface, generated reports, documentation and agent prompts are in English.
`analysis_agent.py` runs automatically from `write_match()` after each simulated match ends.
It checks the final whistle, event count, match identity and goal sequence before analysing
the entire event stream. Each match receives an `analysis.json` file, including matches
with only a 60-second tracking preview. Existing finished matches have reports too.

Open **Analysis** to read and export the final report at any replay position. A full-time
banner links to the report when the full replay finishes. A preview ending does not count
as a final whistle. Reports do not change the replay's time-filtered event feed.

The default **local evidence agent** uses deterministic English reporting and transparent
impact scoring; it is rule-based, not an LLM. It considers every player who appeared,
including substitutes and goalkeepers. Goals, non-goal shots on target, saves, interceptions,
passing and cards contribute to the award. The exact formula and tie-break order appear in
each report. Pressure tags, distance, unrecorded assists and xG are not used to infer quality
or tactical intent. These are simulated matches and the award is a demo assessment.

```sh
# Generate or refresh reports without rerunning the simulation
npm run analyze

# Analyse a single completed match
python3 analysis_agent.py data/league/matches/PL26-W01-MCI-CHE

# Explicitly regenerate cached reports
python3 analysis_agent.py --force
```

For model-written English summaries and half-by-half analysis, set `OPENAI_API_KEY` and
`OPENAI_MODEL` in the generating process's environment, then use:

```sh
python3 analysis_agent.py --mode openai

# Also use model narration automatically for newly generated matches
TOUCHLINE_ANALYSIS_MODE=openai python3 generate.py
```

Choose a model supporting the [Responses API structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
Model mode sends the derived match evidence (including player names, statistics and event
references) to OpenAI. Credentials remain in the Python process and are never included in
browser code or reports. The model writes the narrative; the evidence, ranking and award
remain computed and auditable. Generated prose can still contain mistakes.

Reports are cached by source fingerprint, agent version, mode and model. Missing
configuration, timeout, refusal or malformed responses retain the local report with a
visible explanation. Run the same analysis command to retry. Local mode requires no
credentials, network, packages or background service.

### Squad data

`data/league/squads.json` contains 20 clubs and 566 registered players, including shirt number, position, detailed position, nationality, date of birth and age at 2026-10-19.

Sources:

- [Premier League 2026/27 squad lists](https://www.premierleague.com/en/news/4706139/see-all-the-202627-premier-league-squad-lists) (3 September 2026)
- [Premier League 2026/27 squad numbers](https://www.premierleague.com/en/news/4725682/all-20-premier-league-clubs-squad-numbers-for-202627-season) (22 September 2026)

Positions, nationalities and dates of birth were cross-checked against the Premier League player directory. Match lineups are selected from each club's real squad by position, then supplemented with substitutes. The match simulation still determines results, events, cards and tracking.

## Repository layout

```text
data/league/
├── league.json              # clubs, fixtures, results, table, scorers, summary
├── fixtures.jsonl           # one JSON fixture per line
├── standings.json           # league table
├── squads.json              # 20 real 2026/27 squads
├── validation_report.json   # league consistency report
└── matches/
    └── PL26-W01-MCI-CHE/
        ├── metadata.json
        ├── summary.json
        ├── analysis.json    # English report, award, ranking and source evidence
        ├── events.jsonl
        ├── states.jsonl
        ├── preview-60s.json
        └── tracking.jsonl   # only for featured/full matches

data/                      # legacy single-match example used by validate.py/package.py
index.html
styles.css
app.js
core.js
tracking-worker.js
generate.py
analysis_agent.py
validate.py
validate_league.py
package.py
schema.json
```

## Generate the league

The default league is 20 clubs × 4 matchweeks × 10 matches = 40 fixtures. Every club plays once per matchweek and ends with 2 home / 2 away matches.

```sh
# Default: 4 matchweeks, full tracking for the marquee match of each week
python3 generate.py

# Change the number of matchweeks (1-19) or the tracking scope
python3 generate.py --weeks 4 --tracking featured
python3 generate.py --weeks 4 --tracking all
python3 generate.py --weeks 4 --tracking none

# Add a full replay for a specific fixture
python3 generate.py --full-match PL26-W01-TOT-ARS

# Generate an alternate league with a different seed
python3 generate.py --seed 12345 --output data/league-alt
```

`--tracking` controls full 5 Hz tracking:

| Value | Meaning | Approx. size (4 weeks) |
| --- | --- | --- |
| `featured` (default) | Full tracking for the highest-rated fixture each week; 60s previews elsewhere | ~300 MB |
| `all` | Full tracking for all 40 matches | ~1.3 GB |
| `none` | No full tracking; previews, events, states and summaries only | ~190 MB |

Fixtures run from Saturday 24 October 2026 across four weekends, with local kick-off times in `Europe/London`. The schedule uses the first four rounds of a single round-robin rotation, so no pairing repeats.

## Data model

Each match keeps the existing `HOME / AWAY` stream format:

- 28 registered players per team: starters `H01–H11` / `A01–A11`, substitutes `H12–H14` / `A12–A14`.
- 22 players + 1 ball in tracking frames while nobody is sent off.
- 5 Hz sampling: 30,001 frames from `0` to `6,000,000` ms (100 simulated minutes).
- 6,001 one-second state snapshots.
- `metadata.json` maps `HOME`/`AWAY` to real clubs and includes player names, shirt numbers, positions and squad status.
- `summary.json` includes final score, statistics, goals and per-player distance.
- `events.jsonl` includes passes, carries, pressure, interceptions, shots, saves, restarts, cards, substitutions and match phases.
- `tracking.jsonl` is written only when full replay is requested.

The root `data/` folder remains the original two-team Harbor FC vs Mountain FC example. The frontend uses `data/league/`; the root data is retained for the legacy single-match validator and packaging script.

## Event generation

- `Match.emit()` — builds every event with `event_id`, `sequence`, `match_id`, `period`, `start_ms`, `end_ms`, `available_at_ms` and `possession_id`.
- `Match.finish_action()` — emits `carry`, `pass`, `interception`, `shot`, `save`, `stoppage` and `restart` events.
- `Match.step()` — emits `pressure`, `period_start`, `period_end`, `match_end` and `action_cancelled`.
- `Match.set_possession()` — emits `possession_change`.
- `Match.apply_administrative_events()` — emits yellow cards, red cards and substitutions.
- `build_summary()` — extracts goals from `shot` events with `outcome="goal"`.
- `write_match()` — writes each match's `events.jsonl`.
- `generate_league()` — creates each match and calls `write_match()`.

Events are only released when `available_at_ms <= replay time`. The replay never reveals future passes, cards, substitutions or goals.

## Validation

```sh
npm test

python3 validate.py
python3 validate_league.py
python3 validate.py data/league/matches/PL26-W01-MCI-CHE
```

`validate.py` checks a single match: 5 Hz continuity, identities, pitch bounds, velocity/acceleration limits, ball attachment, event causality, goals, statistics and state snapshots. `validate_league.py` checks the 20-club schedule, every match's summary/events/states/preview, the table, scorer totals and that match lineups come from the real squad file.

## Limitations

This is a demo, not a professional football model. It does not model offsides, a complete foul/referee system, physical collisions, fatigue or full ball physics. Cards and substitutions follow a deterministic demo script; substitutes inherit the outgoing formation slot; pressure is a proximity heuristic. Match outcomes are heuristic and are not calibrated to real football distributions. Use the label **simulated match data** whenever presenting scores, distances or tactical explanations.

Real 2026/27 squad data is sourced from the Premier League and is included for identification and demonstration. Match events, tracking and results are generated and are not official Premier League data.
# Original-preserving decision enrichment

This branch keeps the original generator and all `data/league/` files unchanged.
Choose **Enriched analysis → Analysis → Decision analysis** to compare tactical
two-step hypotheses with the original simulator baseline. Softmax preferences
sum to 100 across the supported options at each moment; they are not player
ability or success probability. Only existing tracking is used: four full
matches plus 36 preview windows. Missing states remain explicitly unavailable.

Run the precomputed demo with `python3 server.py`. Offline reproduction uses
`requirements-enrichment.txt` and `python enriched_pipeline.py all`.
See [中文实现与运行说明](docs/保留原生成器的决策分析.md).
