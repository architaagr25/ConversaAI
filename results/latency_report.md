# End-to-end latency, per component

Measured over 9 recorded calls, replayed frame by frame in the same 20 millisecond chunks the browser sends. 156 turns, 7 nudges.

Each leg is timed separately because they are three orders of magnitude apart, and a combined figure would describe none of them.

| Component | p50 | p95 | Runs on | Samples |
| --- | --- | --- | --- | --- |
| audio chunk handling | 0.00 ms | 0.01 ms | every 20 ms frame | 29303 |
| transcription | 3360.13 ms | 3578.69 ms | every utterance | 156 |
| signal extraction | 0.11 ms | 0.24 ms | every turn | 153 |
| model deliberation | 1153.06 ms | 4358.67 ms | turns the first tier cannot settle | 27 |
| nudge generation | 0.00 ms | 0.01 ms | every turn | 153 |
| delivery to the browser | 0.03 ms | 0.03 ms | every nudge | 7 |

## Audio received to nudge displayed

| p50 | p95 | Mean | Turns |
| --- | --- | --- | --- |
| 3366 ms | 4722 ms | 3269 ms | 153 |

Measured from the frame that ends the caller's turn to the nudge being ready to send. Recognition dominates it, which is why the component table matters more than this number.

## What these numbers do and do not include

**Chunk handling** is voice activity detection on one 20 ms frame. It has to stay far below 20 ms or audio arrives faster than it can be consumed, and the margin here is the headroom for a busier machine.

**Transcription** is the hosted call, wall clock, including the network. It is the largest component by a wide margin and it is the one this project has least control over.

It is also higher here than a caller experiences, and the reason is worth stating rather than leaving for someone to find. These recordings contain both sides of the call mixed into one track, so the endpointer cuts them into segments that include the agent speaking. Median utterance here is 2.6 seconds; a caller answering a qualification question is a fraction of that. For recognition time on caller-length audio, `asr_evaluation.md` measures 18 single utterances and reports a median of 346 ms. Both numbers are real; they are measuring different inputs.

**Delivery** is serialising the nudge into the frame the browser reads. The wire time to a remote browser is not visible from here, so treat this as a floor rather than an estimate. On a local socket the write is a copy into a send buffer.

**None of the last four legs are on the caller's clock.** Analysis is handed to a background worker the moment a turn completes, while the reply is already being synthesised. They are reported because the brief asks for them and because they decide whether a nudge lands before the call moves on, not because the caller waits for them.

Reproduce with `.venv\Scripts\python scripts/measure_latency.py`.