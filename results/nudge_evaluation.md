# Live nudges: accuracy and latency

45 labelled turns, of which 19 should produce nothing. The negatives are the point. A detector scored only against turns that ought to fire reports perfect accuracy and is unusable, because every case it was asked about is one it gets right.

Several negatives are near misses, sharing vocabulary with a detector while meaning the opposite: "I can afford it" against hardship, "there is no guarantee" against the compliance rule.

## Accuracy

- precision: **100%** (29 correct, 0 false)
- recall: **100%** (0 missed)

| Signal | Fired correctly | False positives | Missed |
| --- | --- | --- | --- |
| agent_guarantee | 1 | 0 | 0 |
| agent_pressure | 1 | 0 | 0 |
| agent_threat | 1 | 0 | 0 |
| buying_signal | 1 | 0 | 0 |
| confusion | 1 | 0 | 0 |
| data_conflict | 1 | 0 | 0 |
| frustration | 2 | 0 | 0 |
| hardship | 3 | 0 | 0 |
| hesitation | 3 | 0 | 0 |
| knowledge_gap | 1 | 0 | 0 |
| missed_opportunity | 4 | 0 | 0 |
| missing_disclosure | 3 | 0 | 0 |
| payment_promise | 2 | 0 | 0 |
| repeated_question | 1 | 0 | 0 |
| soft_refusal | 4 | 0 | 0 |

## Where it is wrong

Nothing on this set.

## Latency, per component

Measured separately because the two tiers are three orders of magnitude apart, and a combined figure would hide both.

| Component | p50 | p95 | Runs on |
| --- | --- | --- | --- |
| lexical signals | 0.45 ms | 1.36 ms | every turn |
| model deliberation | 2027 ms | 12257 ms | 20% of turns |

The second tier is reached on 9 of 45 turns. Running it on every turn would roughly triple the cost of a call to change almost no decisions.

None of this is on the caller's critical path. Analysis is handed to a background worker the moment a turn completes, and the reply is already being spoken by then. The measured effect on response time is zero, because the caller's clock stops when the first audio arrives and this starts after that.

## Controls

Across all 45 turns run as one call, 6 nudges were delivered and the rest withheld:

| Withheld because | Count |
| --- | --- |
| call budget spent | 20 |
| cooldown | 5 |

Withheld nudges are kept rather than dropped. What was suppressed and why is the only way to tune a threshold afterwards, and the only way to notice a detector firing constantly and being swallowed.

Every rule carries its own confidence floor, because a false positive does not cost the same everywhere. Telling somebody to slow down when they did not need to costs nothing. Telling a supervisor the agent made an illegal promise when it did not costs their trust in the whole panel, so the compliance rules sit at 0.85 against 0.7 for hardship.