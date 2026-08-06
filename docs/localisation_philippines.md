# Philippines: what was localised, and what was only translated

Solara Life Philippines, premium reminder calls. English, Tagalog and the
Taglish people actually speak on the phone.

The distinction that matters is between translating a script and writing one
for the market. A translated script is grammatically correct and immediately
recognisable as foreign. Everything below is a place where the correct
translation would have been the wrong thing to say.

---

## 1. Six adaptations, not translations

### 1.1 A refusal that is not the word no

Filipino callers rarely refuse directly. *"Titingnan ko po muna"* — I will look
into it first — is a no. So is *"sa susunod na linggo na lang po"*, and so is
a long pause.

A script translated from English waits for the word no, does not hear one, and
treats a refusal as a maybe. It then pushes again, which is exactly what makes
a foreign-run call feel rude.

The pack instructs the agent to treat these as refusals and stop, not to
retrieve another objection response. Tested:

> **Caller.** Titingnan ko po muna, tatawag na lang po ako.
> **Agent.** Naiintindihan ko po iyan, Sir. Saan po kayo mas madaling
> magbayad, sa bank, sa branch, o online, sakaling magdesisyon na po kayong
> magbayad?

It acknowledges, stops selling, and leaves a door open without pressing.

### 1.2 Politeness is grammar, not decoration

*Po* is not "please". It marks formal address, and it carries a set of pronouns
with it: *kayo*, *niyo*, *inyo*. Putting *mo* or *ikaw* in the same sentence is
like switching between "sir" and "mate" mid-sentence.

This is the single most common thing a model gets wrong. Measured when the
models were chosen, before any of this was written:

> Gets ko po na medyo mabigat sa budget ang life insurance, pero may mga
> options naman tayo na pasok sa presyong **kaya mo**.

Formal and informal, four words apart. The pack states the rule explicitly and
`voice_agent/localisation.py` checks every sentence for it. Eight replies out
of eight now pass.

### 1.3 Technical terms stay in English

Nobody says *panahon ng paghihintay* for waiting period, or *seguro sa buhay*
for life insurance. They say waiting period and life insurance.

Translating these produces something a Filipino insurance customer has never
heard from a Filipino insurance company. The pack lists the terms that stay in
English: premium, policy, due date, grace period, lapse, beneficiary, rider,
coverage, reinstatement.

The English share of replies measured 42 to 60 per cent, which is where real
Taglish sits. Below that it reads as a translation exercise; above it, it is
not Taglish at all.

### 1.4 Dates and money are said, not written

Days of the month are Spanish-derived in speech. The 15th is *kinse*, not
*ika-labinlima*, which is correct written Tagalog and sounds like a
proclamation read aloud. Months stay English: *September kinse*.

Amounts go the other way. Pesos are counted in English — "one thousand two
hundred pesos" — not *isang libo dalawang daan*.

Encoded in the pack as speech conventions, because a script that gets the
grammar right and the numbers wrong still sounds foreign.

### 1.5 Money is a face-losing subject

Asking directly whether somebody can afford something causes *hiya*, and the
answer stops being reliable. The pack asks whether they can pay *before the
grace period ends*, which is a question about timing rather than about means,
and it offers the payment channels before asking about money a second time.

The rule "never make somebody feel judged for being short of money" is in the
pack, and fear-selling — telling someone their family will be left with
nothing — is prohibited outright.

### 1.6 A lapse is not yet a lapse

A policy inside its grace period has not lapsed, and saying it has would be
both wrong and frightening. The agent is instructed never to say a policy has
lapsed unless a record says so, and never to promise reinstatement, which is
subject to underwriting.

> **Caller.** Medyo tight po ngayon ang budget namin.
> **Agent.** Naiintindihan ko po iyan, Sir. Huwag po kayong mag-alala dahil
> mayroon tayong grace period na 31 days mula sa inyong due date kung saan
> tuloy pa rin po ang inyong coverage.

The 31 days came from the knowledge base, not from the prompt.

---

## 2. Vocabulary

Canonical term on the left, what callers actually say on the right. Both are
carried on the knowledge base records, so a caller asking about their *hulog*
reaches a record written about premiums.

| Canonical | What callers say |
| --- | --- |
| premium | hulog, bayad, contribution, monthly due |
| due date | takdang araw, due date |
| lapse | ma-lapse, nahinto, na-cancel |
| grace period | grace period |
| beneficiary | benepisyaryo, beneficiary |
| rider | rider, add-on |
| sum assured | sum assured, coverage |
| bank referral | bank partner, bancassurance |

Two of these were added because the recogniser got them wrong rather than
because they seemed likely. *Magkano* came back as *magkana* and *ma-lapse* as
*malapsi* until both were added to the recogniser's domain hints, which took
Taglish word accuracy from 84 to 100 per cent on the test set.

---

## 3. Escalation in the caller's language

Handover triggers are matched on Tagalog phrases, not English ones. A caller
saying *"pwede po bang makausap ang tao"* is asking for a person, and an
English-only trigger list would miss it entirely.

Six trigger groups are localised: asking for a person, complaints, claim
disputes, medical questions, legal threats, and distress. The last matters
most: *"namatay ang asawa ko"* has to stop the call immediately, and it does.

The handover line itself stays in Taglish. Switching to English at the moment
of handing over is the point where a caller most notices they have been talking
to a machine.

---

## 4. What is not localised, and known gaps

**No native speaker has reviewed this.** The mechanical checks pass, and
mechanical checks only prove nothing is obviously broken. Whether it sounds
like a person is a judgement that needs somebody who grew up speaking it. This
is the largest gap in the Philippine work and it is not closeable from here.

**Regional languages are not handled.** Cebuano, Ilocano and Hiligaynon are
first languages for a large share of the country. The agent would answer a
Cebuano speaker in Taglish, which is understandable in most of the Philippines
but not correct.

**The voice is one of two available.** `fil-PH-BlessicaNeural` is a genuine
Filipino voice rather than an English voice reading Tagalog, which was the
minimum bar. It has no regional accent options, so a Visayan caller hears
Manila Tagalog.

**Compliance wording is not reviewed.** The Insurance Commission has
requirements about what may be said on a servicing call. Nothing here has been
through a compliance review, and the prohibitions in the pack are engineering
judgement rather than a legal position.

**Numbers spoken by the caller are read by an English-first recogniser.**
Amounts said in Tagalog forms are less reliably transcribed than English ones.
Not measured yet; worth measuring before this went anywhere real.
