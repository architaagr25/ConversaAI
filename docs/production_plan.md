# What would be built next, and in what order

This is a prototype that works. It is not a system anybody should put in front
of customers, and the distance between those two things is what this page is
for.

The order below is by risk, not by effort. Everything in the first section is
something that could harm a customer or produce a wrong record. Everything in
the last section is something that would merely be better.

Each item says what it costs, because a plan without that is a wish list.

---

## Before a single real customer call

### 1. A compliance review by someone qualified

The prohibitions in the packs are engineering judgement about what is obviously
wrong. OJK regulates collections conduct in Indonesia, including contact hours
and permitted language; the Philippine packs touch insurance selling rules.
Neither has been read by anyone who knows those rules.

This is first because it is the only item on this page where being wrong is a
regulatory matter rather than a quality one.

**Cost:** a compliance officer per market, a day each. **Blocks:** launch.

### 2. Native speaker review of both localised packs

Mechanical checks pass — register consistency, no mixed pronouns, regional
markers mirrored. They prove nothing is obviously broken, which is not the same
as sounding right. Javanese Krama has speech levels that a non-speaker cannot
evaluate at all; the agent could be using the wrong one throughout and nothing
here would catch it.

**Cost:** one native speaker per market, half a day each, plus a rewrite pass.
**Blocks:** launch in that market.

### 3. Word-level ASR confidence, and a paid recogniser

Short replies are the weakest part of the system and they are weakest in the
markets that matter most. The current design cannot tell a confident
recognition from a guess, so a mangled "opo" becomes an answer in a lead
record.

What fixes it is not more thresholds. It is a recogniser that returns per-word
confidence, so a low-confidence transcript can be treated as a non-answer and
re-asked, rather than accepted. That means a paid tier.

**Cost:** provider spend, roughly one engineer-week to thread confidence
through the call loop and the slot filler. **Blocks:** trusting any lead record
produced by the system.

### 4. Slot filling that does not depend on free text

Independent of the recogniser: asking "how old are you" and parsing whatever
comes back is the fragile design. Offering choices, confirming numbers back,
and accepting digits are all cheaper than perfect recognition.

The confirmation loop needs fixing at the same time. There is a give-up counter
after two attempts on a knowledge question and no equivalent on a slot, so on
two recorded calls the agent asked the same question three times.

**Cost:** one engineer-week. **Blocks:** nothing, but it is the highest ratio
of quality gained to effort on this page.

---

## Before more than a handful of concurrent calls

### 5. Move the knowledge base off SQLite and out of process memory

The store is rebuilt whole and the index is held per process. At ten concurrent
calls that is ten copies of the same vectors, and a rebuild is a restart.

The replacement is a vector store as a service — pgvector is enough at this
size — with the index built once and read by every worker.

**Cost:** one engineer-week, plus a managed Postgres. **Blocks:** running more
than one server process.

### 6. Incremental knowledge base builds

Rebuilding everything takes seconds at 106 records. At ten thousand it does
not, and a single corrected sentence should not require it. Content-hash the
sources, re-embed only what changed, and keep the version history that already
exists.

**Cost:** three days. **Blocks:** any content update cadence faster than
nightly.

### 7. Real observability

Timings are logged per turn and written into the call record. Nobody is
watching them. A dashboard over turn latency by component, recognition failure
rate per market, retrieval confidence distribution, and nudges fired against
nudges suppressed.

The confidence floor of 0.60 was set from 23 questions. In production it should
be set from the live distribution and re-checked as content grows.

**Cost:** three days with a hosted metrics service. **Blocks:** knowing whether
any of this still works next month.

### 8. Per-call and per-tenant cost control

Free tier hides this. Every call costs recognition, embedding, one or two model
calls per turn, deliberation on about a quarter of turns, and synthesis. None
of it is metered or capped, and a caller who talks for an hour costs whatever
they cost.

**Cost:** three days. **Blocks:** any commercial deployment.

---

## To make it good rather than working

### 9. Learn from outcomes

Nothing here is fed back. A nudge that is consistently ignored, or consistently
followed by a lost call, is exactly as loud on the hundredth call as the first.
The suppression log already records what was withheld and why, which is half of
what a feedback loop needs; the other half is call outcomes, which needs volume
this project does not have.

The same applies to retrieval. Every declined question is recorded in the lead
as an unanswered question, and that list is the content backlog nobody is
reading.

**Cost:** ongoing, and needs a few thousand labelled calls first.

### 10. Audio signal, not just text

Tone, pace, interruptions and raised voices carry real information about a call
going wrong, and everything in the insight layer currently reads text. Dead air
is the only exception and it comes from the endpointer, not from analysis.

**Cost:** two engineer-weeks and a paid audio model. **Blocks:** nothing —
this is the item most likely to be cut.

### 11. A regional-accented Indonesian voice

The brief asks for a regional accent. What exists is regional *language* in a
standard Jakarta accent, because no free provider offers otherwise. Paid
providers do, or a voice can be cloned from a consenting speaker.

**Cost:** provider spend, or a day plus a speaker. **Blocks:** the honest
version of a claim currently written as a limitation.

### 12. Barge-in that works on speakers

Interruption is off by default because on speakers the microphone hears the
agent. Proper acoustic echo cancellation on the server side, rather than
relying on the browser's, would let it default on — which is most of what makes
a call feel like a conversation rather than a form.

**Cost:** one engineer-week, and it is genuinely hard to get right.

---

## What is deliberately not on this list

**A prettier interface.** The brief asks for a reliable core workflow over
visual polish, and the page has what a reviewer needs to see: transcript,
sources, nudges, level meter, summary.

**More languages.** Two markets are already asserted rather than verified.
Adding a third would add a third unverified claim.

**More nudge types.** Sixteen already exist and six is the per-call budget. The
constraint is which six arrive, not how many exist.
