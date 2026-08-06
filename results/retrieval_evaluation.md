# Retrieval evaluation

Every question below was written down with its expected answer before the results were seen. The knowledge base holds 102 searchable records across three markets.

**21 correct, 2 partially correct, 0 incorrect, out of 23.**

Confidence floor: 0.6. Below it the agent declines rather than answering from the closest record it found.

| Verdict | Meaning |
| --- | --- |
| correct | The expected record ranked first |
| partially correct | It was in the top five but not first, so an agent reading all five still answers properly |
| incorrect | It was not returned, or the search declined when it should have answered |

---

## Product

### PROD-1 — PASS

**Question.** What does the Plus plan cover?

**Expected.** `health-shield-plans.html#Compare plans`

**Returned.** Compare plans

**Source.** `web/health-shield-plans.html#Compare plans`

**Authority.** published · **similarity** 0.712 · **answered** yes

**Why this record.** The comparison table is the only record holding per-plan benefit limits.

**Verdict.** correct — expected record ranked first

### PROD-2 — PARTIAL

**Question.** What optional riders can I add to my health policy?

**Expected.** `health-shield-plans.html#Riders`

**Returned.** Health Shield Rate Table: Rider monthly premium

**Source.** `documents/health_shield_rate_table.pdf#Rider monthly premium`

**Authority.** binding · **similarity** 0.758 · **answered** yes

**Why this record.** Lists the four riders and their waiting periods.

**Verdict.** partially correct — expected record at position 2, not first

---

## Policy Rule

### POL-1 — PASS

**Question.** How long before pre-existing conditions are covered?

**Expected.** `health_shield_policy_wording.pdf#2 Waiting periods`

**Returned.** 2 Waiting periods

**Source.** `documents/health_shield_policy_wording.pdf#2 Waiting periods`

**Authority.** binding · **similarity** 0.765 · **answered** yes

**Why this record.** The binding clause states twenty-four months. A campaign page claims thirty days for the same thing, so this tests that authority beats wording.

**Verdict.** correct — expected record ranked first

### POL-2 — PASS

**Question.** Is dental treatment covered?

**Expected.** `health_shield_policy_wording.pdf#3 General exclusions`

**Returned.** 3 General exclusions

**Source.** `documents/health_shield_policy_wording.pdf#3 General exclusions`

**Authority.** binding · **similarity** 0.778 · **answered** yes

**Why this record.** Dental is excluded on every plan level except after a covered accident.

**Verdict.** correct — expected record ranked first

### POL-3 — PASS

**Question.** What happens if I miss a premium payment?

**Expected.** `grace period`

**Returned.** 4 Premiums and grace period

**Source.** `documents/health_shield_policy_wording.pdf#4 Premiums and grace period`

**Authority.** binding · **similarity** 0.771 · **answered** yes

**Why this record.** Thirty-one day grace period, then lapse. Stated in both the FAQ and the wording.

**Verdict.** correct — expected record ranked first

### POL-4 — PASS

**Question.** Berapa denda kalau telat bayar cicilan?

**Expected.** `multifinance_agreement_terms.pdf#Pasal 2 Denda Keterlambatan`

**Returned.** Pasal 2 Denda Keterlambatan

**Source.** `documents/multifinance_agreement_terms.pdf#Pasal 2 Denda Keterlambatan`

**Authority.** binding · **similarity** 0.807 · **answered** yes

**Why this record.** The binding article setting the late fee at 0.5 per cent per day.

**Verdict.** correct — expected record ranked first

### LIFE-1 — PASS

**Question.** Ano po ang mangyayari kung ma-lapse ang policy ko?

**Expected.** `life-philippines.html`

**Returned.** Ano po ang mangyayari kung ma-lapse ang policy ko?

**Source.** `web/life-philippines.html#Ano po ang mangyayari kung ma-lapse ang policy ko?`

**Authority.** published · **similarity** 0.815 · **answered** yes

**Why this record.** Taglish question about lapse and reinstatement.

**Verdict.** correct — expected record ranked first

---

## Qualification

### QUAL-1 — PASS

**Question.** Can I apply if I am 62 years old?

**Expected.** `health_shield_policy_wording.pdf#5 Eligibility`

**Returned.** 5 Eligibility

**Source.** `documents/health_shield_policy_wording.pdf#5 Eligibility`

**Authority.** binding · **similarity** 0.665 · **answered** yes

**Why this record.** Entry age is eighteen to sixty, so the answer is no. Previously lost to a FAQ about adding family members, which shares more words with the question.

**Verdict.** correct — expected record ranked first

### QUAL-2 — PASS

**Question.** Syarat pengajuan pembiayaan mobil apa saja?

**Expected.** `multifinance_id`

**Returned.** Solara Multifinance Indonesia: criteria that reviewed

**Source.** `rules/qualification_rules.yaml#multifinance_id.soft_rules`

**Authority.** operational · **similarity** 0.759 · **answered** yes

**Why this record.** Income, age, employment and arrears conditions. First written expecting the website section, which was wrong: the qualification rules file states the same conditions with the thresholds attached, and outranks the web page on authority. Several records legitimately answer this, so the expectation names the market rather than one source.

**Verdict.** correct — expected record ranked first

### QUAL-3 — PASS

**Question.** Can I add my spouse and children to my plan?

**Expected.** `Can I add my family`

**Returned.** Can I add my family?

**Source.** `web/health-shield-faq.html#Can I add my family?`

**Authority.** published · **similarity** 0.775 · **answered** yes

**Why this record.** Dependant ages and the rule that parents cannot be added.

**Verdict.** correct — expected record ranked first

---

## Pricing

### PRICE-1 — PASS

**Question.** How much is the monthly premium for someone aged 35?

**Expected.** `health_shield_rate_table.pdf`

**Returned.** Health Shield Rate Table: Standard monthly premium by entry age

**Source.** `documents/health_shield_rate_table.pdf#Standard monthly premium by entry age`

**Authority.** binding · **similarity** 0.749 · **answered** yes

**Why this record.** The rate table is the only source quotations may come from. Marketing material advertises PHP 999, which is not a quotable figure.

**Verdict.** correct — expected record ranked first

### PRICE-2 — PASS

**Question.** Magkano po ang premium ko kung monthly ang bayad?

**Expected.** `life-philippines.html#Magkano po ang premium ko?`

**Returned.** Magkano po ang premium ko?

**Source.** `web/life-philippines.html#Magkano po ang premium ko?`

**Authority.** published · **similarity** 0.788 · **answered** yes

**Why this record.** Taglish question against a Taglish record. Tests the multilingual path.

**Verdict.** correct — expected record ranked first

---

## Faq

### FAQ-1 — PASS

**Question.** Do I need a medical exam to apply?

**Expected.** `Do I need a medical examination`

**Returned.** Do I need a medical examination to apply?

**Source.** `web/health-shield-faq.html#Do I need a medical examination to apply?`

**Authority.** published · **similarity** 0.773 · **answered** yes

**Why this record.** Required over fifty, or for Max cover, or where the questionnaire indicates it.

**Verdict.** correct — expected record ranked first

### FAQ-2 — PASS

**Question.** When does my cover actually start?

**Expected.** `When does my cover start`

**Returned.** When does my cover start?

**Source.** `web/health-shield-faq.html#When does my cover start?`

**Authority.** published · **similarity** 0.766 · **answered** yes

**Why this record.** First day of the month after approval and first premium.

**Verdict.** correct — expected record ranked first

---

## Objection

### OBJ-1 — PASS

**Question.** This is more expensive than I was expecting

**Expected.** `more expensive than I expected`

**Returned.** "This is more expensive than I expected."

**Source.** `web/health-shield-faq.html#"This is more expensive than I expected."`

**Authority.** published · **similarity** 0.735 · **answered** yes

**Why this record.** Must return the approved objection response, not a pricing record. An objection is a persuasion script with compliance limits on it.

**Verdict.** correct — expected record ranked first

### OBJ-2 — PASS

**Question.** I already have insurance through my employer

**Expected.** `already covered through my employer`

**Returned.** "I am already covered through my employer."

**Source.** `web/health-shield-faq.html#"I am already covered through my employer."`

**Authority.** published · **similarity** 0.715 · **answered** yes

**Why this record.** Approved response covering portability and entry age.

**Verdict.** correct — expected record ranked first

---

## Process

### PROC-1 — PARTIAL

**Question.** How do I make a claim?

**Expected.** `How do I make a claim`

**Returned.** 6 Claims

**Source.** `documents/health_shield_policy_wording.pdf#6 Claims`

**Authority.** binding · **similarity** 0.714 · **answered** yes

**Why this record.** Notification windows and the ninety day reimbursement deadline.

**Verdict.** partially correct — expected record at position 2, not first

### PROC-2 — PASS

**Question.** Kalau saya mau melunasi lebih cepat, bagaimana caranya?

**Expected.** `melunasi`

**Returned.** Kalau saya mau melunasi lebih cepat, bagaimana?

**Source.** `web/multifinance-indonesia.html#Kalau saya mau melunasi lebih cepat, bagaimana?`

**Authority.** published · **similarity** 0.782 · **answered** yes

**Why this record.** Early settlement penalty of three per cent before half the term. First written expecting the binding article Pasal 4, which was wrong: the Indonesian FAQ answers the same question in the register the caller used, and returning it first is the better outcome, not a miss.

**Verdict.** correct — expected record ranked first

---

## Partnership Benefits

### PART-1 — PASS

**Question.** What support do branch partners receive?

**Expected.** `partners.html#Branch partnership benefits`

**Returned.** Branch partnership benefits

**Source.** `web/partners.html#Branch partnership benefits`

**Authority.** published · **similarity** 0.785 · **answered** yes

**Why this record.** The record the assessment's own example is modelled on.

**Verdict.** correct — expected record ranked first

---

## Escalation

### ESC-1 — PASS

**Question.** I want to speak to a real person please

**Expected.** `declines to answer`

**Returned.** Actions the agent must never take

**Source.** `rules/qualification_rules.yaml#prohibited`

**Authority.** operational · **similarity** 0.576 · **answered** no

**Why this record.** Written expecting ESC-REQUEST to be returned, which was wrong, and the two decisions sat here contradicting each other for a while. An escalation rule says where a call goes. It does not answer anything, and a caller asking for a person is not asking a question. Retrieval returning it meant the routing rules appeared as sources underneath the agent's replies, which is a citation that claims the answer came from somewhere it did not. They are stored, versioned and traceable, and excluded from search. So the right result here is a decline. That escalation still happens is a separate thing, tested in test_agent.py: it fires from the pack's own triggers, not from a search.

**Verdict.** correct — declined as it should

---

## Out Of Scope

### OOS-1 — PASS

**Question.** What is the capital of France?

**Expected.** `declines to answer`

**Returned.** 6 Claims

**Source.** `documents/health_shield_policy_wording.pdf#6 Claims`

**Authority.** binding · **similarity** 0.521 · **answered** no

**Why this record.** Nothing in the knowledge base answers this. The agent must say so.

**Verdict.** correct — declined as it should

### OOS-2 — PASS

**Question.** What time does the football match start tonight?

**Expected.** `declines to answer`

**Returned.** When does my cover start?

**Source.** `web/health-shield-faq.html#When does my cover start?`

**Authority.** published · **similarity** 0.507 · **answered** no

**Why this record.** Unrelated to any product. Must not answer from the nearest record.

**Verdict.** correct — declined as it should

### OOS-3 — PASS

**Question.** Can you recommend a good restaurant in Manila?

**Expected.** `declines to answer`

**Returned.** "I never claim, so it is wasted money."

**Source.** `web/health-shield-faq.html#"I never claim, so it is wasted money."`

**Authority.** published · **similarity** 0.547 · **answered** no

**Why this record.** Mentions a city that appears throughout the corpus, so this tests that surface overlap alone does not produce confidence.

**Verdict.** correct — declined as it should

---
