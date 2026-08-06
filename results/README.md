# Results

Every claim made anywhere in this repository has a file here behind it. This
page is the reading order.

Nothing below was written from memory. Each figure comes from a script in
`scripts/` that can be run again, and the calls are real recordings placed over
the same interface a person would use.

---

## Start here

| If you have | Read |
| --- | --- |
| Five minutes | This page |
| Twenty minutes | This page, then [voice_agent_test_calls.md](voice_agent_test_calls.md) and [asr_evaluation.md](asr_evaluation.md) |
| An hour | Everything, plus [../docs/architecture.md](../docs/architecture.md) |

---

## Q1 — Knowledge-grounded voice agent

**Where:** [voice_agent_test_calls.md](voice_agent_test_calls.md) &middot;
recordings in [calls/](calls/)

Five calls, one per situation the brief asks for, each with a recording, a
transcript with sources, and a lead record.

| Call | Situation |
| --- | --- |
| `cooperative` | Straight through qualification |
| `objection` | Objection answered from approved responses |
| `conflicting` | Caller corrects an earlier answer |
| `out_of_scope` | A question the knowledge base cannot answer |
| `escalation` | Caller asks for a person |

The agent is grounded structurally rather than by instruction: it receives only
the records retrieved for the current turn, rebuilt every turn, and is told
plainly when nothing matched. It is not asked to be honest about what it does
not know; it is not given the material to invent from.

Response time is measured from the moment the caller stops talking, which is
the silence they actually experience — not from the end of the trailing pause,
which would hide the endpointing delay and report a number no caller ever gets.

---

## Q2 — Production knowledge base

**Where:** [retrieval_evaluation.md](retrieval_evaluation.md) &middot;
[../docs/knowledge_taxonomy.md](../docs/knowledge_taxonomy.md) &middot;
[../docs/knowledge_base_schema.md](../docs/knowledge_base_schema.md)

**21 correct, 2 partially correct, 0 incorrect, out of 23.** Every question was
written down with its expected answer before any result was seen.

Build reports, each from a separate stage:

| Stage | File |
| --- | --- |
| Web extraction | [web_extraction_report.txt](web_extraction_report.txt) |
| Document extraction | [document_extraction_report.txt](document_extraction_report.txt) |
| Cleaning and deduplication | [cleaning_report.txt](cleaning_report.txt) |
| PII detection | [pii_detection_report.txt](pii_detection_report.txt) |
| Store build, chunking, versioning | [kb_build_report.txt](kb_build_report.txt) |
| Embedding model choice | [embedding_model_comparison.txt](embedding_model_comparison.txt) |

106 sections in, 102 searchable. Four are superseded duplicates and seven are
escalation routing rules — both kept for traceability and excluded from search,
because a routing rule says where a call goes rather than answering anything.

The confidence floor of 0.60 was set by measurement, not preference: in-scope
questions scored 0.665 to 0.815, out-of-scope 0.505 to 0.548.

---

## Q3 — Native-language voice bots

**Where:** [asr_evaluation.md](asr_evaluation.md) &middot;
[native_language_calls.md](native_language_calls.md) &middot;
[../docs/localisation_philippines.md](../docs/localisation_philippines.md) &middot;
[../docs/localisation_indonesia.md](../docs/localisation_indonesia.md)

Two providers over 18 utterances, identical audio to both:

| Provider | Overall | Standard | Regional | Median latency |
| --- | --- | --- | --- | --- |
| Groq Whisper | **97%** | 99% | 90% | **559 ms** |
| Deepgram | 69% | 68% | 74% | 4481 ms |

They are level on English and standard Indonesian. Everything separating them
is Taglish and regional speech, which is the part of the brief that matters.
Deepgram returned nothing at all for plain Tagalog.

Four native-language calls, two per market, including a Javanese caller outside
Jakarta. Three localisation examples per market, drawn from those calls rather
than written for the report.

**Reproduce:** `.venv\Scripts\python scripts/evaluate_asr.py`

---

## Q4 — Live insights and nudges

**Where:** [nudge_evaluation.md](nudge_evaluation.md) &middot;
[../docs/live_insights.md](../docs/live_insights.md)

Analysis during the call, not after it. **100% precision, 100% recall** on 33
labelled turns, of which **13 should produce nothing**.

The negatives are the point. A detector scored only against turns that ought to
fire reports perfect accuracy and is unusable, because every case it was asked
about is one it gets right. Several negatives are deliberate near misses —
"I can afford it, that is not the problem" against the hardship detector.

| Component | p50 | p95 | Runs on |
| --- | --- | --- | --- |
| Lexical signals | 0.02 ms | 0.20 ms | every turn |
| Model deliberation | ~1100 ms | ~9000 ms | 9 of 33 turns |

None of it is on the caller's critical path. Work is handed to a background
worker the moment a turn completes, while the reply is already being spoken.

**Reproduce:** `.venv\Scripts\python scripts/evaluate_nudges.py`

---

## Foundations

Measured before anything was built on them, because each of these decisions
would have been expensive to reverse later.

| Question | File |
| --- | --- |
| Which language model, and how fast | [language_model_comparison.txt](language_model_comparison.txt) |
| Which embeddings | [embedding_comparison.txt](embedding_comparison.txt) |
| Speech round trip latency | [speech_roundtrip.txt](speech_roundtrip.txt) |
| End-to-end timing harness | [foundation_latency.txt](foundation_latency.txt) |

The embedding comparison is the one worth reading. A local model scored
**−0.024** on Tagalog against English — worse than random, on a project whose
second market is Taglish. The hosted model scored **+0.895**. Nothing about the
local model failed; it returned results, and the results were wrong.

---

## Records produced by calls

`leads/` — one JSON per call: what was collected, the eligibility decision and
which rule made it, questions the agent could not answer, and a handover note
in the market's language.

`webhooks/` — what would have been posted to a CRM.

Contact details are redacted in the stored copy. Age, income and city stay,
because they are what the qualification rules run on and without them the
record cannot explain why a decision went the way it did.

---

## What is not here

No native speaker has reviewed the Filipino or Indonesian. Mechanical checks
prove nothing is obviously broken, which is not the same as sounding right.

The Indonesian regional requirement is partly met: the agent speaks regional
*language* in a standard Jakarta *accent*, because no free provider offers a
Javanese or Sundanese-accented voice.

Recognition of short replies is the weakest part of the system, and it is
weakest in exactly the markets that matter most. Full detail in
[../docs/limitations_and_scale.md](../docs/limitations_and_scale.md).
