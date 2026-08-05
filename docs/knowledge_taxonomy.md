# Knowledge taxonomy and source map

How Solara's content is classified, where each piece comes from, and what the
agent is allowed to do with it.

Solara Finance Group is a fictional company. Every name, figure, policy term and
customer record in this repository is invented for testing. Nothing here
describes a real product or a real person.

---

## 1. Why a taxonomy comes first

Retrieval quality is decided before a single vector is computed. If records are
not classified, three things go wrong:

- The agent cannot tell a marketing claim from a binding policy rule, so it
  quotes advertising copy as though it were cover.
- Contradictions cannot be resolved, because there is nothing to say which
  source outranks which.
- The same question in two markets returns the other market's answer, since
  nothing scopes a search to the business unit the caller belongs to.

So category, business unit and authority are assigned at extraction time and
carried on every record.

---

## 2. Business units

Each unit has its own products, regulator, currency and language. A record
belongs to exactly one, or to `group` where it applies across all of them.

| Code | Unit | Sector | Market | Language | Currency |
| --- | --- | --- | --- | --- | --- |
| `health_ph_en` | Solara Health Shield | Health insurance | English-speaking | English | PHP |
| `life_ph` | Solara Life Philippines | Life and bancassurance | Philippines | English, Tagalog, Taglish | PHP |
| `multifinance_id` | Solara Multifinance Indonesia | Vehicle and consumer finance | Indonesia | Bahasa Indonesia | IDR |
| `group` | Solara Finance Group | Corporate | All | English | n/a |

A caller is scoped to one unit at the start of a call. Retrieval filters on it,
so an Indonesian installment question can never surface a Philippine premium
answer.

---

## 3. Categories

Nine categories, chosen so that every question the voice agent has to field maps
onto exactly one. The assessment asks the agent to answer product, policy,
qualification, FAQ and objection questions; the remaining four exist because the
source material contains them and they need somewhere to go.

| Category | What belongs in it | Example question it answers |
| --- | --- | --- |
| `product` | Plans, coverage, benefits, limits, tenor options | "What does the Plus plan cover?" |
| `policy_rule` | Binding terms: waiting periods, exclusions, lapse, penalties | "How long before pre-existing conditions are covered?" |
| `qualification` | Eligibility criteria and the questions used to test them | "Can I apply if I am 62?" |
| `pricing` | Premiums, rates, fees, down payments, penalty amounts | "What is the monthly premium at my age?" |
| `process` | Claims, applications, payments, renewals, reinstatement | "How do I file a claim?" |
| `faq` | Common questions in the company's own words | "Do I need a medical exam?" |
| `objection` | Approved responses to resistance | "This is too expensive." |
| `partnership_benefits` | Branch and agent partnership material | "What support do branch partners get?" |
| `corporate` | Company, contact, regulatory, service hours | "What are your office hours?" |

`objection` is kept separate from `faq` deliberately. An objection response is a
persuasion script with compliance limits on it, and it must never be returned as
a factual answer to a question about cover.

---

## 4. Authority, and how conflicts are settled

Sources disagree. The website says one thing, a policy document says another, and
a marketing leaflet says something looser than both. Every record therefore
carries an authority level, and retrieval prefers the higher one when two records
answer the same question.

| Level | Meaning | Sources |
| --- | --- | --- |
| 1 `binding` | Contractual terms. Wins every conflict. | Policy wording, rate tables, regulatory notices |
| 2 `operational` | How the company actually works day to day. | Qualification rules, process documents, forms |
| 3 `published` | Public-facing statements of fact. | Website product and FAQ pages |
| 4 `promotional` | Marketing copy. Never quoted as cover. | Campaign pages, brochures, banners |

Rules that follow from this:

- A `promotional` record is never returned alone as the answer to a
  `policy_rule` or `pricing` question.
- Where a `binding` and a `published` record conflict, the binding one is
  returned and the conflict is recorded against both.
- A conflict that cannot be resolved by authority is flagged for review, and the
  agent says it needs to check rather than picking one.

---

## 5. Source types

| Type | Where it comes from | Known problems to expect |
| --- | --- | --- |
| `web_page` | Company site | Navigation, footers, cookie banners, blocks repeated across pages |
| `pdf_policy` | Policy wording documents | Running headers and footers, page numbers, clause numbering |
| `pdf_table` | Rate tables | Tables that lose meaning when flattened to text |
| `form_export` | CRM form submissions | Contains personal data. Inconsistent field names |
| `rules_file` | Internal qualification logic | Structured, but written for humans not machines |

---

## 6. Record schema

Every record carries the fields below. The assessment's example record maps onto
this directly.

| Field | Purpose |
| --- | --- |
| `record_id` | Stable identifier, e.g. `kb_product_001` |
| `title` | Short human-readable name |
| `content` | The text that gets embedded and quoted |
| `category` | One of the nine above |
| `business_unit` | Which unit it belongs to, or `group` |
| `authority` | `binding`, `operational`, `published`, `promotional` |
| `source_type` | How it was extracted |
| `source_ref` | Exact location: file and section, or URL and heading |
| `source_retrieved_at` | When the source was read |
| `version` | Record version, incremented when content changes |
| `content_hash` | Detects whether a re-extraction actually changed anything |
| `pii` | Whether personal data was found |
| `pii_types` | What kinds, once redacted |
| `language` | Language of the content |
| `terminology_variants` | Other words callers use for the same thing |
| `conflicts_with` | Record ids stating something incompatible |
| `quality_flags` | Extraction or source problems noticed |

`terminology_variants` earns its place because callers do not use the company's
words. Someone asking about their *hulog*, their *cicilan* and their *monthly due*
are all asking about the same field, and keyword search will miss two of the
three unless the variants travel with the record.

---

## 7. Terminology standardisation

The source material uses different words for the same concept, both across units
and within a single page. The canonical term is what records are written in; the
variants are what callers actually say, and are kept for keyword matching.

### Money owed each period

| Canonical | Unit | Variants found in sources and used by callers |
| --- | --- | --- |
| `premium` | `health_ph_en`, `life_ph` | contribution, monthly due, bayad, hulog, premium payment |
| `installment` | `multifinance_id` | cicilan, angsuran, monthly payment, setoran |

### Other recurring terms

| Canonical | Variants |
| --- | --- |
| `waiting_period` | qualifying period, elimination period, exclusion period |
| `lapse` | policy lapse, nahinto, terminated for non-payment |
| `due_date` | jatuh tempo, payment date, takdang araw |
| `late_fee` | denda, penalty, surcharge, late payment charge |
| `down_payment` | DP, uang muka, initial payment |
| `tenor` | term, loan period, jangka waktu |
| `beneficiary` | nominee, benepisyaryo, claimant |
| `rider` | add-on, supplementary benefit, optional cover |
| `pre_existing_condition` | PEC, prior illness, existing sickness |

---

## 8. Personal data

The form export contains personal data, as real CRM exports do. It is all
invented, and it is treated as though it were not.

| Kind | Handling |
| --- | --- |
| Name | Replaced with a placeholder before the record is stored |
| Email, phone | Replaced. Format preserved so validation logic stays testable |
| Government ID | Replaced. Never leaves the machine |
| Date of birth | Reduced to an age band, since age is what qualification needs |
| Address | Reduced to city and province |
| Policy or account number | Replaced |

Redaction happens before embedding, so no personal data is sent to any external
service. Records touched this way carry `pii: true` and a list of what was found,
which is what makes the handling auditable rather than merely claimed.

Amounts, ages and dates that are *not* identifying are kept, because
qualification logic depends on them.

---

## 9. Known defects planted in the sample corpus

The sample sources contain deliberate problems, so that the cleaning pipeline is
demonstrated against them rather than described. Each is listed here with what
should happen to it.

| Defect | Where | Expected handling |
| --- | --- | --- |
| Navigation, footer, cookie banner on every page | All web pages | Removed before extraction |
| Identical "About Solara" block on three pages | Web pages | Kept once, duplicates dropped |
| Near-duplicate benefit copy, reworded | Product and campaign pages | Detected by similarity, higher authority kept |
| Waiting period given as 24 months, 2 years, and 30 days | Web page, policy PDF, campaign page | Binding source wins, conflict recorded |
| Premium figure differing between website and rate table | Web page, rate table PDF | Binding source wins, conflict recorded |
| Three date formats for the same date | Across sources | Normalised to ISO |
| Terminology drift within one page | Life Philippines page | Canonical term assigned, variants kept |
| Personal data in a form export | Form CSV | Detected, redacted, flagged |
| Truncated, unreadable PDF | Documents | Extraction fails cleanly, source flagged, run continues |
| Empty section with a heading and no content | Web page | Dropped, not stored as an empty record |

The last two matter most. A pipeline that stops on a bad file is not usable, and
one that silently stores empty records looks like it worked.
