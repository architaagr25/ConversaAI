# ConversaAI

A voice platform for financial services conversations, built around a single principle:
the bot answers from an approved knowledge base, or it says it does not know.

The system covers four capabilities that share one retrieval layer:

- **Knowledge base** — mixed business content (web pages, PDFs, rate tables, form
  exports) converted into clean, deduplicated, traceable records with citations.
- **Voice agent** — a browser-based calling interface for health insurance lead
  qualification, grounded in that knowledge base.
- **Localized agents** — the same capability for the Philippines (life insurance and
  bancassurance, English–Tagalog code-switching) and Indonesia (multifinance, formal
  and colloquial Bahasa, with Javanese and Sundanese speech recognised and mirrored
  in the reply).
- **Live insights** — streaming analysis of a call while it is still in progress,
  producing short, actionable nudges with measured end-to-end latency.

---

## The business context

Solara Finance Group is a financial services company operating three units:

| Unit | Sector | Market |
| --- | --- | --- |
| Solara Health Shield | Health insurance | English |
| Solara Life Philippines | Life insurance and bancassurance | Philippines |
| Solara Multifinance Indonesia | Vehicle and consumer financing | Indonesia |

Its product information lives across a website, marketing PDFs, policy documents, rate
tables and form exports. Agents answer the same questions on the phone all day, in
three languages, and the answers drift. This platform makes that knowledge canonical
and puts it behind the voice channel.

---

## Repository layout

```
core/                shared configuration, logging, timing and provider fallback
knowledge_base/      extraction, cleaning, chunking, indexing and retrieval
voice_agent/         calling interface, call loop, per-market speech and voices
insights/            live signal detection and nudge delivery, during the call
data/agents/         the three conversation packs, one per market
data/raw/            original unprocessed source material
data/processed/      cleaned intermediate output
data/kb/             the built knowledge base and its index
docs/                architecture, localization notes, limitations
results/             recordings, transcripts and measured results
tests/               automated checks
scripts/             operational and evaluation scripts
```

All three markets run on one code path. Nothing about a market lives in code:
each pack in `data/agents/` carries its own flow, objection handling,
escalation triggers, closing lines, and the lines said when the system itself
fails.

---

## Setup

Requires Python 3.12 and FFmpeg available on the system path.

```
git clone https://github.com/architaagr25/ConversaAI.git
cd ConversaAI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Then fill in `.env` with your own API keys. `.env.example` lists every variable the
system reads and explains what each one is for.

Check the setup before going further:

```
python scripts\verify_setup.py      interpreter, dependencies, FFmpeg
python scripts\check_services.py    every external service, with a real request
```

---

## Making a call

Build the knowledge base once, then start the call interface:

```
python -m knowledge_base.web          extract from web sources
python -m knowledge_base.documents    extract from PDFs, forms and rules
python -m knowledge_base.clean        classify, deduplicate, find contradictions
python -m knowledge_base.pii          redact personal data
python -m knowledge_base.store        build the store
python -m knowledge_base.retrieve     build the search index

python -m voice_agent.server
```

Open `http://127.0.0.1:8000` and allow microphone access. Speak normally and
pause when you have finished.

Tick **Allow interrupting** if you are on headphones, and you can talk over the
agent to cut it off. It is off by default because on laptop speakers the
microphone hears the agent, the recogniser turns that into words, and the agent
starts answering itself. With it off the microphone is muted while the agent
talks, so wait for it to finish.

### A public address

Browsers only allow microphone access over HTTPS, or on localhost. To take a
call from another machine, put a tunnel in front of it:

```
cloudflared tunnel --url http://localhost:8000
```

That prints a `https://something.trycloudflare.com` address that works
immediately. No account, no card, and nothing to configure. Download
`cloudflared` from Cloudflare's releases page, or install it with
`winget install Cloudflare.cloudflared`.

### Without a microphone

Two harnesses, useful for testing and for producing evidence:

```
python scripts\try_agent.py                     type instead of speaking
python scripts\call_client.py --script all      place scripted calls, record them
```

`call_client.py` synthesises the caller's speech, streams it to the server in
real time, and saves a WAV, a transcript with sources, and a summary to
`results/calls/`.

---

## What was built, and how it was checked

Start at **[`results/README.md`](results/README.md)**. It is the reading order
for every measured claim, and each figure comes from a script that can be run
again.

The headline numbers:

| | |
| --- | --- |
| Retrieval | 21 correct, 2 partial, 0 incorrect, out of 23 |
| Speech recognition | 97% against a second provider's 73%, over 18 utterances |
| Live nudges | 100% precision and recall over 33 turns, 13 of which should stay silent |
| Recorded calls | 9, across three markets |
| Automated checks | 539 |

Reproduce the two evaluations directly:

```
python scripts\evaluate_asr.py        speech recognition, both providers
python scripts\evaluate_nudges.py     nudge accuracy and per-component latency
python -m pytest                      everything else
```

---

## Documentation

| | |
| --- | --- |
| [Architecture](docs/architecture.md) | How a call flows, how the knowledge base is built, and why |
| [Live insights](docs/live_insights.md) | In-call analysis, controls, and what it costs |
| [Knowledge taxonomy](docs/knowledge_taxonomy.md) | How records are categorised and ranked |
| [Knowledge base schema](docs/knowledge_base_schema.md) | Fields, versioning and traceability |
| [Philippines localization](docs/localisation_philippines.md) | What was adapted rather than translated |
| [Indonesia localization](docs/localisation_indonesia.md) | Registers, regional speech, and one requirement only partly met |
| [Limitations and scale](docs/limitations_and_scale.md) | What is weak, what is asserted rather than measured, and what breaks at ten times the volume |

The limitations page is the one to read if you only read one. It separates what
was measured from what was assumed, and neither is hedged.
