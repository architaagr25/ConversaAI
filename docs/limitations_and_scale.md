# Limitations, and what breaks at ten times the volume

Written plainly. Everything here is either measured or a known gap, and none of
it is hedged into sounding better than it is.

---

## 1. What is genuinely weak

### Short replies are recognised badly, in the markets that matter most

The evaluation set reports 97% word accuracy. That is not what a caller gets,
because the set is made of full sentences and a real qualification call is
mostly two-word answers.

Observed on recorded calls:

| Said | Heard |
| --- | --- |
| Iya, silakan | Sosan |
| Opo, pwede po | Oppo, bayadepo |
| Kwarenta y singko po | 40-5 po |
| Medyo mahal po yata | Sumahal po yata |

The last one has a consequence beyond the transcript: the hardship phrase never
matched, so no nudge was produced on a turn that plainly warranted one. The
insight layer reads text, so it inherits every recognition error. Precision
survives a bad transcript; recall does not.

This is the single largest weakness in the system. Closing it needs either a
recogniser tuned for short in-domain utterances, or a design that stops relying
on free-text answers for slot filling — offering choices rather than asking
open questions.

### The confirmation question repeats

When a mangled reply fails to fill a slot, the agent asks again. There is a
give-up counter after two attempts on a knowledge question and no equivalent on
a slot, so on two recorded calls the agent asked whether it was speaking to the
policyholder three times. A caller experiences that as not being listened to.

### No native speaker has reviewed the localised packs

The mechanical checks pass: register consistency, no formal and informal
pronouns in one sentence, regional markers mirrored correctly. Mechanical
checks only prove nothing is obviously broken. Whether it sounds like a person
is a judgement that needs somebody who grew up speaking the language.

Javanese Krama in particular has speech levels a non-speaker cannot evaluate.
The agent could be using the wrong one throughout and nothing here would catch
it.

### Regional accent is not produced, only understood

The brief asks for a regional accent outside Jakarta. What exists is regional
*language* — greeting, politeness, register — in a standard Jakarta *accent*.
No free provider offers a Javanese or Sundanese-accented Indonesian voice.

Recognition is the better half: regional input was handled correctly in every
probe. Understanding a regional caller works; sounding like one does not.

### Compliance has not been reviewed by anyone qualified

OJK regulates collections conduct in Indonesia, including contact hours and
what may be said. The Philippine packs touch insurance selling rules. The
prohibitions in both are engineering judgement about what is obviously wrong,
not a compliance position.

### The nudge phrase lists are hand written

They cover the objections these two markets actually produce and will miss one
phrased in a way nobody anticipated. The model tier catches some of that, and
only runs when the first tier finds nothing on a long turn.

Nothing is learned from outcomes. A nudge consistently ignored, or consistently
followed by a lost call, is not fed back. That needs outcome-labelled calls at
a volume this project does not have.

### No audio signal is used for insights

Tone, pace, interruptions and raised voices carry real information about a call
going wrong. Everything in the insight layer reads text. Dead air is the only
exception, and it comes from the endpointer rather than from any analysis of
the audio.

### Latency floors at about 3.5 seconds

Under free-tier throttling, recorded medians run 4.9 to 5.7 seconds from the
caller finishing to the first word back. A person expects under two.

---

## 2. With noisy audio

Everything above was found on a quiet laptop microphone in a quiet room. Noise
is not a degradation of that, it is a different failure, and it fails in a
direction that is easy to miss.

### Nothing returns an error

This is the thing to understand about noise here. Voice activity detection is a
classifier, not a threshold, and a fan, a keyboard, a television or a second
person talking are all things it will call speech. The pipeline behind it does
not reject what it is handed. A recogniser given a few seconds of room tone
returns a fluent sentence, confidently, because its training data is full of
subtitle files whose quiet passages carry captions. The agent then answers a
question nobody asked.

Three gates were added after watching exactly that happen, and each was written
for a case seen rather than imagined:

**A proportion of voice, not just an amount.** A three second recording with
five voiced frames scattered through it clears any absolute floor low enough to
accept a one word answer. At least a fifth of the recording has to be voice.
Spoken answers run a third and up; noise-triggered recordings sit near a tenth.

**A loudness floor.** Room tone on a laptop sits at 20 to 40 RMS, a person
speaking sits above 120. The floor is 100. It was 220 at one point, set against
synthesised audio, and it rejected a real caller at 188 — which is its own
lesson about tuning a gate against material that is cleaner than the real
thing.

**The recogniser's own hint, coming back.** The domain hint improves spelling
of terms like *cicilan*. It is also a list of words the recogniser reaches for
when it cannot make out the audio. A live call answered "and the plus, max"
repeatedly, out of a hint ending "Essential, Plus, Max". Hints are now cut to
terms a general recogniser genuinely gets wrong, and a transcript made entirely
of hint words is discarded.

### What noise still costs

**Short replies degrade first, and they degrade worst.** "Opo", "iya", "yes" —
one syllable against background is where recognition fails, and it is the most
common reply on a qualification call. Every gate above trades recall for
precision, so each one also throws away some real speech. That trade is
deliberate: a discarded "yes" makes the agent ask again, and an invented answer
goes into a lead record and nobody ever knows.

**Endpointing gets slower or wrong.** Background speech keeps the detector from
seeing the run of silence that ends a turn, so turns run long or merge.

**Barge-in becomes unusable on speakers.** It is off by default for this
reason. In a noisy room with interruption enabled, the agent stops mid-sentence
for a door closing.

**Nudge recall drops with ASR quality**, and nothing in the nudge evaluation
measures that. Its 45 probes are clean text, so 100% precision and recall is a
statement about the detectors given a correct transcript, not about the system
in a call centre. That gap is the single largest untested risk here.

### What would actually fix it

Not more thresholds. A trained voice activity model rather than a classifier
tuned by hand, per-caller noise profiling over the first seconds of a call, and
an ASR provider with word-level confidence so a low-confidence transcript can
be treated as a non-answer instead of an answer. All three need either paid
services or training data, and neither was available here.

---

## 3. What is measured, and what is asserted

Worth separating, because a reader cannot tell from the outside.

**Measured:** retrieval accuracy, ASR accuracy per market and provider, nudge
precision and recall, every latency figure, embedding quality across languages,
model choice and cost.

**Asserted:** that the Taglish sounds natural, that the Indonesian register is
right, that the escalation triggers cover what real callers say, that the
prohibitions match regulation. All four need a domain expert and none had one.

---

## 4. At ten times the volume

Assume ten concurrent calls rather than one, and roughly a thousand a day.

### What breaks first

**The free tiers, immediately.** Groq and Gemini free quotas are already
producing 429s at one call at a time; the 3.7 second p95 on the model tier is
throttling, not work. This is the first thing to fail and the easiest to fix:
it is a billing change, not an engineering one.

**One process, one machine.** The server holds call state in memory and each
call owns a thread for background analysis. Ten concurrent calls is survivable;
a hundred is not. Call state moves to Redis, the analysis workers become a
queue consumed by a pool, and the web tier becomes stateless behind a load
balancer.

**Speech synthesis is a single unauthenticated service.** `edge-tts` is free
and has no quota published, which means it also has no guarantee. At volume it
would need replacing with something contracted, and the native voices are the
constraint on which providers are acceptable.

**The vector index is a NumPy array scanned in full.** Fine at 102 records and
fine at ten thousand. At a million it needs an approximate index. This is the
least urgent scaling problem despite sounding like the most.

### What does not break

**Retrieval quality.** Nothing about the ranking depends on corpus size, and
the authority ranking gets more useful as sources multiply, not less.

**The nudge engine.** Lexical signals cost 0.02 ms. The model tier runs on
about a quarter of turns and is the only per-call cost that grows.

**Grounding.** Records are assembled per turn from what retrieval returned. A
larger corpus changes which records arrive, not whether the agent can invent.

### What would need rethinking rather than scaling

**The knowledge base is rebuilt whole.** At this size that takes seconds. With
a real corpus and real change rates it needs incremental updates keyed on
content hashes — the hashing is already there and used for change detection, so
this is a smaller job than it sounds.

**Chunking is unexercised.** No source document in this corpus was long enough
to split, so the chunking path has tests but no real evidence behind it. A real
policy wording would exercise it immediately, and that is where it would first
be found wanting.

**The name-detection stoplist is corpus-specific.** PII redaction of names
works against this corpus and was tuned against it. A different corpus would
need it re-derived, and a missed name is a privacy failure rather than a
quality one.

**Half-duplex audio.** The deaf window while the agent speaks is a workaround
for the microphone hearing the speaker. At volume, with real telephony rather
than a browser, that problem disappears and the window should go with it —
along with the four guards built around it.

### What ten times the volume would surface that one call cannot

The honest answer is: the things this evaluation cannot see. The ASR weakness on
short replies was only found by placing real calls, not by the 18-utterance
test set that reported 97%. A thousand calls a day would surface a similar
class of problem in retrieval, in escalation triggers, and in the register
checks — and none of it is predictable from here.

That is an argument for the logging and the suppressed-nudge records already in
place, rather than for guessing now.
