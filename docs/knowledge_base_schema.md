# Knowledge base schema

What a record holds, how it is built, and how it is kept honest over time.
Classification and vocabulary are covered separately in
[`knowledge_taxonomy.md`](knowledge_taxonomy.md).

Current build: **106 records, 102 searchable**, from 11 sources across 3 business
units. Stored in `data/kb/knowledge_base.sqlite` with a line-per-record copy in
`data/kb/records.jsonl`.

---

## 1. Fields

| Field | Type | Purpose |
| --- | --- | --- |
| `record_id` | text, primary key | Derived from the source, not from position. See §3. |
| `title` | text | The heading this content sat under |
| `content` | text | The text that gets embedded and quoted |
| `category` | text | One of nine. Decides what the agent may do with it |
| `business_unit` | text | Scopes retrieval so markets cannot cross |
| `authority` | text | `binding`, `operational`, `published`, `promotional` |
| `source_type` | text | `web_page`, `pdf_policy`, `pdf_table`, `form_export`, `rules_file` |
| `source_ref` | text | Exact location, down to the heading |
| `source_origin` | text | The file or URL it came from |
| `source_retrieved_at` | text | When the source was read |
| `version` | integer | Increments only when the content changes |
| `content_hash` | text | Detects whether a re-extraction actually changed anything |
| `language` | text | `en` or `id` |
| `pii` | integer | Whether personal data was found |
| `pii_types` | json | What kinds, after redaction |
| `terminology_variants` | json | Other words callers use for the same thing |
| `conflicts_with` | json | Records making incompatible claims |
| `duplicate_of` | text | The surviving copy, when this one was superseded |
| `quality_flags` | json | Extraction problems and concept markers |
| `char_count` | integer | Length, for chunking checks |
| `chunk_index` / `chunk_count` | integer | Position within a split section |
| `retrievable` | integer | Whether search may return it |
| `first_seen` / `last_updated` | text | Audit dates. See §4 |

Indexes exist on `(business_unit, category)`, `authority`, `retrievable` and
`source_origin`, which are the four things retrieval filters on.

---

## 2. Sample records

### A product record, matching the shape asked for in the brief

```
record_id             kb_f86e38d780_00
title                 Branch partnership benefits
content               Operational, marketing and technology support is provided
                      to branch partners. Partners receive a dedicated
                      relationship manager, access to the Solara agent portal,
                      co-funded local marketing, and a full onboarding
                      programme for new staff...
category / source     partnership_benefits / web/partners.html#Branch partnership benefits
version / PII         1 / false
authority             published
business_unit         group
char_count            350
```

### A binding policy record, in conflict with a marketing claim

```
record_id             kb_885605fbd0_00
title                 2 Waiting periods
content               2.1 No Waiting Period applies to treatment required as a
                      direct result of an Accident occurring on or after the
                      Commencement Date. 2.2 A Waiting Period of thirty (30)
                      days applies to all illness claims. 2.3 A Waiting Period
                      of twenty-four (24) months applies to any Pre-existing
                      Condition declared at application...
category / source     policy_rule / documents/health_shield_policy_wording.pdf#2 Waiting periods
authority             binding
conflicts_with        ["kb_b916cc637f_00"]
quality_flags         ["covers_waiting_period", "covers_lapse", "covers_rider",
                       "covers_pre_existing_condition"]
version / PII         1 / false
```

### The record it is in conflict with

```
record_id             kb_b916cc637f_00
title                 Cover that starts quickly
content               No long delays. Accidents are covered from day one,
                      illnesses after just 30 days, and even pre-existing
                      conditions are covered after only 30 days of membership...
category / source     policy_rule / web/health-shield-campaign.html#Cover that starts quickly
authority             promotional
conflicts_with        ["kb_885605fbd0_00", "kb_c5add40066_00", "kb_e8c76ec437_00"]
quality_flags         ["contradicts_binding_source"]
```

Both records are stored. The agent is not free to choose between them: the
binding source wins, and the promotional record carries a flag saying so.

---

## 3. Identifiers

`record_id` is `kb_<hash of source_ref>_<chunk index>`.

Numbering records as they are processed is the obvious approach and it is
wrong. Adding one page renumbers everything after it, so a citation given to a
caller last week resolves to different content this week, and version history
becomes meaningless because every record looks edited.

Deriving the identifier from `source_ref` means it changes only when the
content genuinely moves to a different place in a different document.

One consequence worth stating: `conflicts_with` and `duplicate_of` are written
by earlier stages that still number by position, so they are rewritten to the
final identifiers during the build. A cross reference pointing at an identifier
that no longer exists is worse than none, because it looks like a working link.
Verified as zero dangling references on every build.

---

## 4. Versioning

| Situation | version | first_seen | last_updated |
| --- | --- | --- | --- |
| New record | 1 | now | now |
| Content changed | +1 | unchanged | now |
| Rebuild, content identical | unchanged | unchanged | unchanged |

The third row is the one that matters. Re-running the pipeline over unchanged
sources must leave every date alone, or the audit trail records nothing but the
last time somebody ran the build.

Change is decided by `content_hash`, which normalises case and whitespace, so
reformatting a source does not register as an edit.

---

## 5. Chunking

Records are split at **1400 characters**, with **180 characters of overlap**,
falling back to whole sections where they already fit. Nothing in the current
corpus needs splitting: the longest record is 853 characters. The strategy is
exercised against an 18,818 character section from a public page, which splits
into 15 chunks with no words lost.

Three rules:

- **Split on sentence boundaries**, never mid-sentence.
- **Never split a table row.** A row cut in half loses the column it belongs
  to, which turns a premium figure into a loose number. Table content is split
  on line boundaries and rejoined with newlines, not spaces, because running
  the rows together destroys the same meaning as cutting one in half.
- **Fold short trailing fragments** back into the previous chunk rather than
  storing a record that answers nothing.

Overlap is taken from the end of the previous chunk, so the opening sentence of
every chunk is still its own.

---

## 6. What is deliberately not in here

**Customer records.** The form export holds 25 leads. None became records. They
are customer data, and a voice agent has no business retrieving somebody else's
lead when asked about premiums. What was extracted is the shape of the form:
which fields it captures and what values they accept, which is what
qualification questions are built from.

**Public reference pages.** Three Wikipedia articles were extracted in order to
prove the pipeline works on markup nobody wrote for it. They are kept in
`data/processed` as evidence and excluded from the knowledge base. An agent
answering Solara questions out of a general encyclopaedia is ungrounded even
when the encyclopaedia is correct.

**Superseded duplicates.** Four records are stored with `retrievable = 0` and a
`duplicate_of` pointer. Deleting them would leave no way to check the build kept
the right copy.
