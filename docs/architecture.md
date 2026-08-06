# Architecture

Two pipelines that meet at one place. The knowledge base is built ahead of
time and offline; the call runs in real time and reads from it. They share
nothing else, which is deliberate: a policy change is a rebuild, not a code
change, and the agent cannot quote a term that no longer exists.

---

## 1. The call

```mermaid
flowchart TB
    caller([Caller in the browser])

    subgraph browser [Browser]
        mic[Microphone capture<br/>16 kHz mono, 20 ms frames]
        meter[Level meter]
        play[Playback queue]
        panel[Transcript, citations, live nudges]
    end

    subgraph server [FastAPI server, one WebSocket per call]
        ep[Endpointer<br/>voice detection, 700 ms to end a turn<br/>voiced-frame floor rejects noise]
        asr[Speech recognition<br/>per-market language hint and domain terms]
        agent[Agent turn<br/>slots, escalation, objections]
        ret[Retrieval<br/>keyword + vector, RRF, authority ranking]
        llm[Reply generation<br/>records for this turn only]
        tts[Speech synthesis<br/>native voice per market, sentence by sentence]
    end

    subgraph side [Side channel, never on the caller's clock]
        an[Live analyst<br/>background worker]
        sig[Signals<br/>lexical, then model if unsettled]
        nud[Nudge engine<br/>priority, cooldown, budget, floors]
    end

    subgraph after [After the call]
        lead[Lead record]
        note[Handover note<br/>in the market's language]
        hook[Webhook]
    end

    caller --> mic --> ep
    mic --> meter
    ep -->|utterance| asr --> agent
    agent -->|needs knowledge| ret --> llm
    agent --> llm --> tts --> play --> caller
    llm --> panel

    agent -.->|handed over, reply already going out| an
    an --> sig --> nud --> panel

    agent --> lead --> note --> hook
```

The dotted line is the only one that matters for understanding the design.
Analysis is handed over **before** the audio goes out, so the two overlap. The
reply never waits for it.

### One turn, in order

| Stage | Typical |
| --- | --- |
| Endpointing, caller stops to turn detected | ~700 ms |
| Speech recognition | ~400 ms |
| Retrieval | ~120 ms |
| Reply generation | ~1.5 s |
| First sentence synthesised | ~700 ms |
| **Caller hears the first word** | **~3.5 s** |

Measured from the moment the caller stops talking, which is the silence they
actually experience. Recorded call medians run 4.9 to 5.7 seconds on the free
tier, where throttling rather than work is the difference.

### Two things about the audio path

**Half duplex by default.** While the agent speaks, the microphone is ignored
for the length of the reply plus 400 ms. On laptop speakers the microphone
hears the agent, the recogniser turns that into words, and the agent answers
itself — which happened, and is why four separate guards exist. Callers on
headphones can tick a box and interrupt freely.

**Speaker attribution is free.** The server transcribed the caller and
generated the agent's text, so it knows which side every word came from. No
diarization is inferred, and the compliance detectors can be applied to the
agent alone.

---

## 2. The knowledge base

```mermaid
flowchart LR
    subgraph sources [Sources]
        web[Web pages]
        pdf[PDF documents]
        rules[Qualification rules]
    end

    subgraph build [Build, offline]
        ext[Extract<br/>depth-first walk, table-aware]
        clean[Clean<br/>boilerplate, repeated footers]
        dedup[Deduplicate<br/>superseded kept, not searched]
        pii[PII protection<br/>7 kinds, one-way tokens]
        chunk[Chunk and version<br/>stable ids, content hashing]
        embed[Embed<br/>hosted, 768 dimensions]
    end

    subgraph store [Store]
        db[(SQLite<br/>records, authority, conflicts)]
        vec[(Vectors)]
    end

    web --> ext
    pdf --> ext
    rules --> ext
    ext --> clean --> dedup --> pii --> chunk --> embed
    chunk --> db
    embed --> vec
```

### Retrieval, per turn

```mermaid
flowchart LR
    q[Caller question] --> kw[Keyword search<br/>BM25]
    q --> vs[Vector search<br/>cosine]
    kw --> rrf[Reciprocal rank fusion]
    vs --> rrf
    rrf --> auth[Authority ranking<br/>binding 1.00 to promotional 0.35<br/>contradiction penalty, similarity term]
    auth --> floor{Confidence<br/>above 0.60?}
    floor -->|yes| ctx[Records for this turn]
    floor -->|no| none[Nothing matched,<br/>and the agent is told so]
    ctx --> reply[Reply]
    none --> reply
```

**Grounding is structural, not instructed.** The model is given only the
records retrieved for the current turn, rebuilt every turn, and told plainly
when nothing matched. It is not asked to be honest about what it does not
know; it is not given the material to invent from.

Two things are stored but never searched: superseded duplicates, kept for
traceability, and escalation routing rules, which say where a call goes rather
than answering anything.

**Citations mean the answer used that record.** A record is only cited when
distinctive words from it appear in the reply. Citing everything retrieved put
five sources under a questionnaire line that answered nothing, which teaches a
reader that citations mean nothing.

---

## 3. Markets

One code path, three configurations. Nothing about a market lives in code.

| | Philippines, English | Philippines, Taglish | Indonesia |
| --- | --- | --- | --- |
| Pack | `health_shield_en` | `life_ph` | `multifinance_id` |
| ASR language hint | `en` | **none, deliberately** | `id` |
| Voice | `en-US-AriaNeural` | `fil-PH-BlessicaNeural` | `id-ID-GadisNeural` |
| Regional handling | — | — | Javanese, Sundanese |

Taglish gets no language hint because forcing Tagalog makes the recogniser
spell the English half phonetically, and half of what a Filipino caller says on
an insurance call is English. That decision is measured, not asserted:
`results/asr_evaluation.md`.

Each pack carries its own conversation flow, objection handling, escalation
triggers, closing lines, and the lines said when the system itself fails — so
a call never switches to English at the moment something has gone wrong.

---

## 4. Choices worth defending

**Gemini for replies, Groq as failover.** On the call path a throttle goes
straight to the second provider rather than retrying: retrying spent three
seconds and failed over anyway, so the caller heard three seconds of silence to
reach the same answer.

**Groq Whisper for recognition, Deepgram measured against it.** 97% against
73% overall, and the gap is entirely Taglish and regional speech. Latency
decides it independently: 346 ms against 3141 ms.

**Short domain hints.** A hint is also a list of words the recogniser hands
back when it cannot make out the audio, so it carries only terms a general
recogniser gets wrong. Cutting the ordinary words out left accuracy at 97%.

**Hosted embeddings, not local.** A local model scored −0.024 on
Tagalog against English, worse than random. Hosted scored +0.895.

**Deliberation off on the call path.** Thinking tokens are charged against the
output budget, and with a short limit what came back was fragments of reasoning
rather than an answer.

**One background worker for analysis, dropping rather than queueing.** A
backlog on a live call is worthless: advice about turn two delivered at turn
nine is not advice.
