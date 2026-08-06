# Live insights and nudges

Analysis of a call while it is still happening, delivered to whoever is
watching it, in time to change how it ends.

The distinction that matters: this is not a report written after the call. A
post-call summary is a different and easier problem, because nothing is
waiting on it. Everything here is constrained by the fact that a person is on
the line.

---

## 1. The rule the design is built around

**The caller must never wait for it.**

A nudge that makes the agent a second slower has cost more than it gave. The
silence is what the caller notices; the nudge is not for them.

So analysis is handed to a background worker the moment a turn completes, and
the reply is already being spoken by then. The measured effect on response
time is zero, because the caller's clock stops when the first audio arrives
and this starts after that.

Two consequences, stated rather than hidden:

**Nudges can land one turn late.** On a fast exchange the model tier sometimes
does not finish before the caller speaks again. It is delivered against the
turn it came from rather than presented as current.

**Work is dropped, not queued, when it falls behind.** A backlog on a live
call is worthless: advice about turn two delivered at turn nine is not advice.
There is one worker, and a turn arriving while it is busy is skipped and
counted. The count is in the call summary.

---

## 2. Speaker attribution is free here

Acoustic diarization infers, from one mixed stream, who was speaking. This
system does not have to infer it. The call loop transcribed the caller's audio
and generated the agent's text, so it knows which side every word came from,
outright and without error.

That matters more than it sounds. The compliance detectors only apply to the
agent. A customer asking *"will you take the vehicle if I miss one?"* is not
the agent threatening repossession, and a system scanning the turn as a single
block of text would report it as one. There is a test for exactly that case.

The cost is that this only works for a bot-led call. A human-to-human call
recorded as one stream would need real diarization, and would inherit its
error rate.

---

## 3. Two tiers, because they cost different amounts

| Tier | What it does | p50 | p95 | Runs on |
| --- | --- | --- | --- | --- |
| Lexical | Phrase and state matching over the turn | 0.02 ms | 0.04 ms | every turn |
| Model | Sentiment, intent, hang-up risk | 3244 ms | 6039 ms | 9 of 33 turns |

The first tier is effectively free and catches most of what matters on these
calls: somebody saying they cannot pay this month, somebody asking for a
person, somebody repeating a question they already asked.

The second tier is asked only where the first found something it could not
settle, or where a long turn matched nothing at all — which is the case phrase
lists are worst at, an objection in words nobody wrote down. On the evaluation
set it is reached on **27% of turns**. Running it on every turn would roughly
triple the cost of a call to change almost no decisions.

Phrases are per language. An Indonesian customer refusing says *"belum
sempat"*, not *"no"*. A detector written in English waits for a refusal it will
never hear — the same failure the conversation packs were built to avoid.

---

## 4. What is detected

**From the caller.** Hardship, soft refusal, frustration, confusion, payment
promise, buying signal, repeated question.

**From the agent.** Guaranteeing an outcome, pressure selling, raising
repossession. These are compliance breaches and they are worth catching in the
second they happen rather than in a report next month.

**From the call loop, with certainty rather than inference.** The agent
declining to answer, escalation, the caller correcting an earlier answer, dead
air.

A repeated question is matched on meaning, not spelling. The threshold started
at 60% word overlap and missed *"magkano po ang premium ko kada buwan"* against
*"magkano po ba ang premium ko every month"* — the same question, where the
only words surviving a switch into English are the loanwords. It is 50% now.

---

## 5. Controls, and why each exists

Every one of these is here because of a way the first version was unusable.

| Control | Why |
| --- | --- |
| Cooldown per kind | Hardship fired on four consecutive turns of a real call, because the customer kept explaining the same difficulty |
| Budget per call | Past about six, the reader stops reading. Which six arrive matters more than how many |
| One per turn | Two at once is unreadable while a call is running |
| Priority | A compliance breach outranks a sentiment change, every time |
| Per-rule confidence floor | See below |
| Mute by kind, global floor offset, off entirely | For a team that wants fewer, or none of a category |

The floors are per rule rather than global because a false positive does not
cost the same everywhere. Telling somebody to slow down when they did not need
to costs nothing. Telling a supervisor the agent made an illegal promise when
it did not costs their trust in the whole panel. Compliance rules sit at 0.85;
hardship sits at 0.70.

**Suppressed nudges are kept, not dropped.** What was withheld and why is the
only way to tune a threshold afterwards, and the only way to notice a detector
firing constantly and being swallowed.

---

## 6. Accuracy

29 labelled turns, of which **12 should produce nothing**.

- **precision 100%**, **recall 100%**, zero false positives on this set.

The negatives are the point. A detector scored only against turns that ought
to fire reports perfect accuracy and is unusable on a real call, because every
case it was asked about is one it gets right.

Several negatives are deliberate near misses — sharing vocabulary with a
detector while meaning the opposite:

| Turn | Detector it must not trigger |
| --- | --- |
| "I can afford it, that is not the problem" | hardship |
| "No, money is not an issue for me" | hardship |
| "I understand, that is clear enough" | confusion |
| "There is no guarantee of approval" (agent) | agent_guarantee |
| "Will you take the vehicle if I miss one?" (caller) | agent_threat |

Full detail in `results/nudge_evaluation.md`.

**100% on 29 turns is not 100% in production.** The set is small, it was
written alongside the detectors, and a phrase list scores well against phrases
somebody thought of. The honest claim is that the obvious false positives have
been checked for and are absent, not that the false positive rate is zero.

---

## 7. On a real call

`id_collections`, placed over the WebSocket interface:

> **Caller.** Belum sempat pak, nanti aja ya, lagi susah bulan ini.
> **Nudge, turn 1, 2061 ms.** Genuine difficulty, not an objection. Stop asking
> for payment and offer the restructuring options.

Two further signals on the same turn were withheld by the one-per-turn cap.
The nudge arrived on turn 1 of 3, well inside the call.

**And a call where nothing fired.** On `ph_taglish` the caller said *"Medyo
mahal po yata para sa akin ngayon"* and the recogniser returned *"Sumahal po
yata para sa aking ngayon"*. The hardship phrase never matched, so no nudge was
produced on a turn that plainly warranted one.

That is the honest limit of this layer. It reads text, so it inherits every
recognition error, and the Taglish short-utterance weakness recorded in
`results/asr_evaluation.md` translates directly into missed nudges. Precision
survives a bad transcript; recall does not.

---

## 8. Known limits

**Recall depends on the recogniser**, as above. This is the largest gap.

**The phrase lists are hand written.** They cover the objections these two
markets actually produce, and they will miss an objection phrased in a way
nobody anticipated. The model tier exists to catch some of that, and is reached
only when the first tier finds nothing on a long turn.

**Nothing is learned from outcomes.** A nudge that was consistently ignored, or
consistently followed by a lost call, is not fed back. Doing that needs
outcome-labelled calls at a volume this project does not have.

**No audio signal is used.** Tone, pace, interruptions and raised voices carry
real information about a call going wrong, and everything here reads the text
only. Dead air is the sole exception, and it comes from the endpointer rather
than from any analysis of the audio.

**The model tier is throttled on the free tier.** The 3.7 second p95 is
throttling, not work. Under a paid quota it sits near the p50.
