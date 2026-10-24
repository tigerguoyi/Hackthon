# Role B integration audit / 兼容审计

## Fixed inputs and scope

- User-specified B baseline: `61aefb91602d4e1236383ce7dfede3cd64bb1d3b`.
- Pinned read-only A reference: `96d13a377e98602c9998e3ebb82186843762febf`, from `https://github.com/LiNTe912/NextPlay-AI.git`.
- Destination: the user's **Desktop/英超黑客松/Hackthon** working copy, current `main` branch. No A push, merge, branch switch or PR.
- Initial desktop HEAD was `ecc68d2` plus the five already-applied UI files. Every tracked desktop file was checked against `61aefb9` in the other local B copy. Only the desktop index/commit pointer was aligned to that existing commit; no working file was discarded.
- The supplied plan says not to commit; the user's direct instruction explicitly supersedes that constraint and requests the final local commit message **Ne_Generator**.

## P0 findings, confirmed from files

| Area | A reference | B baseline / integration choice |
|---|---|---|
| Contract | `contracts/model.py`, `contracts/schema.json`, full source record schema, A/B/C fixtures exist | Canonical hash helpers and fixture JSON copied byte-for-byte; source record schema stored as `contracts/records.schema.json`. B's legacy root schema is not replaced. |
| Published new league | Both `data/league` and `data/league-validated` contain 40 metadata files, 40 decision caches, four full trajectory files | Use **A data/league only**, not two duplicate leagues. Each decision carries a snapshot. |
| Coverage | 91,031 decisions, 2,234–2,334 per match; all carry exact pre-action state; 82 generated goals | New isolated dataset indexes all 91,031 snapshots. Only four full animations; 40-match decision coverage does not imply 40 full animations. |
| Policy | Every new league metadata has `action_policy.revision=player-actions-1` | Choice weights/probabilities stay separate from execution probabilities and Q2. |
| Old B decision identification | Carry/event-chain inference; 5,377 exact covered states | Retained for legacy only. New pipeline uses generator `decision_id` and `snapshot`; explicitly marked fallback only if a source lacks decisions. |
| Directions and clocks | Explicit HOME/AWAY directions and metadata period boundaries | Adapter uses these fields, including repeated directions and custom period lengths; never guesses new-source direction by half. |
| Team parameters | In league data, not the fixed-case metadata | Registered league ratings used with a binding digest. Fixed cases use explicitly labelled neutral ratings. No unexplained zero fill. |
| Goalkeeper / substitute IDs | Metadata position plus on-pitch identity | Adapter retains real IDs. Private scoped Q2 binding resolves a substituted keeper for B's original suffix convention and maps all results back. |
| Carry intent | Generator logs choice of `carry`, but `start_carry()` draws its target afterwards | No forced actual-forward mapping. Missing intended direction remains unknown. |
| Pass templates | A rollout is a ground ball at 14 m/s; original B plans at 14.5 and permits high balls | Contract mode uses B's quarter-second receiver lead, ball origin and a 14 m/s plan; this exact template is injected into A's existing rollout. Legacy research is unchanged. |
| Legal actions | Contract supports pass/carry/shot/hold, offside and active teammate checks | Validity mask applies at both Q2 steps. Public high balls and hold have no rank; hold is not assigned a fictitious zero value. |
| Main model | A outcome and B Q2 are different | Only B Q2 supplies public ranking. A utility/rollout code is not a second ranking source. |
| Profiles | Source Sofascore observations and simulated foot profile differ | Source status/kind/conflicts retained for display; simulated foot values stay neutral. No radar multiplier on success/Q2. |
| Cache | Original B manifest has old source and rule hashes | New source, snapshots, model tables, trees, calibration and score files are written only under `data/league-enriched-gen-96d13a37/`. |

The initial document's warning that full new decision caches might be absent is **not true of this pinned checkout**: the files were present and parsed, not inferred from the README.

## Preserved, adapted, and intentionally unsupported

- `generate.py`, `enriched_rules.py`, `enriched_pipeline.py`, `data/league/` and `data/league-enriched-v1/` are preserved. `role-b-original-anchors.json` protects the generator and old data/model/output files with hashes.
- `role_b/q2.py` binds the unchanged B function bodies into a per-call private namespace for direction, duration and candidate support. It never changes module globals or the A reference. A neutral/legacy-mode equality test compares complete candidate Q values with the original evaluator.
- A's `decision/rollout.py` is reused under `vendor/a_decision`. The local B copy adds one optional `analysis` dependency parameter. Scenario loops, reaction times, speed/acceleration, target error, first-touch rule and stopping conditions are unchanged. `vendor/a_decision/engine.py` is the exact upstream source.
- A's canonical helpers and schemas are not rewritten. The JS validator changes only the source schema path because B's legacy schema is preserved.
- No new match engine, FQI, individual weak-foot success model, airborne duel model or actual C/Foundry connection is claimed. Model agents are unavailable; the computed comparison survives and the public response has `explanation:null`, `agent_trace:[]`.

## Public versus internal boundary

`POST /api/role-b/analysis` returns exactly the canonical response fields. Eligible candidates have ordinal rank, textual opportunity/risk, assumptions and citations. It never returns Q, probabilities, value gain, scenario weight, temperature or Softmax.

`GET /api/role-b/catalog` and `/snapshot` supply registered observed snapshots and profile provenance; `/routes` supplies geometric route coordinates, not numeric decision scores. A bound `/rollout` uses the same action ID and target. `/actual` overlays historical choice **after** comparison, without changing ranks or candidate-set ID.

The existing numerical dashboard is explicitly labelled **internal research / original match**. The new public-contract page is `team-integration.html`. Numerical new-source diagnostics are available only through `python -m role_b.diagnostics`, not a public HTTP endpoint.

## Validation environment finding

A's tracked `node_modules` does not contain the built `ajv/dist/2020.js`. Its unchanged contract test invocation initially produced four dependency failures. A was not repaired. B installed the exact development dependency versions from the upstream lockfile using the available pnpm tool. The unchanged 12 A test methods then ran successfully in a read-only harness whose validator subprocess points to B's installed AJV and byte-identical canonical schemas. This distinction is retained rather than claiming an unmodified `npm ci` was run in A.

## Reproduction and evidence

- `role_b/upstream.json`: source commit and original vendor file hashes.
- `tests/fixtures/contracts/`: byte-identical A/B/C fixture snapshots, requests, examples and presentation-only context.
- New dataset `manifest.json`: source hashes, per-match metadata versions, actor/time validation and exact coverage.
- `reports/training.json`: match-group isolation, new xT fitting, pass teacher MAE and actual pass Brier.
- `reports/scoring.json`: precomputed count, availability and recommendation distribution without quotas.
- `validation/result.json`: complete snapshot, exchange and source-protection validation.
- `docs/role-b-validation.md`: commands, observed results and remaining interpretation boundaries.

All generated results are synthetic-match analysis under explicit hypotheses. Contract/schema correctness, reproducibility and directional scenario checks do not establish real-football counterfactual accuracy.
