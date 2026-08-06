# Native language calls

Four calls, two per market, placed over the same WebSocket interface as the
English ones. The caller's speech is synthesised in the market's own voice and
streamed in real time, so the endpointer, the recogniser and the reply loop run
as they would with a person on the line.

| Call | Market | Covers | Turns | Grounded | Escalated |
| --- | --- | --- | --- | --- | --- |
| `ph_taglish` | Philippines, Taglish | Objection, then a lapse question | 5 | 4 | no |
| `ph_escalation` | Philippines, Taglish | Asks for a person, in Tagalog | 2 | 1 | ESC-REQUEST |
| `id_collections` | Indonesia | Indirect refusal, formal register | 4 | 3 | no |
| `id_javanese` | Indonesia, Javanese | Regional greeting outside Jakarta | 2 | 2 | no |

Recordings, transcripts and lead records are in `results/calls/`.

Median response time across the four was 4.9 to 5.4 seconds, measured from the
moment the caller stops talking. That is slower than the English calls and the
reason is the free tier rather than the language.

---

## Philippines: three things that are localised, not translated

### 1. An indirect refusal is treated as a refusal

> **Caller.** Medyo mahal po yata para sa akin ngayon.
> **Agent.** Naiintindihan ko po ang inyong sitwasyon. Maaari ko po bang
> malaman kung kaya niyo po itong mabayaran bago matapos ang grace period na
> tatlumpu't isang araw?

*"Medyo mahal po yata"* is a no. The agent acknowledges first, drops the
selling, and asks about timing rather than asking again for a decision.

### 2. Register holds across the whole call

Every agent line above uses *po* and *ninyo*. Not one mixes in *mo* or *ka*.
That mixing is the failure mode the model reaches for unprompted, and it reads
as sarcasm rather than as informality.

### 3. Technical terms stay in English inside Tagalog grammar

> **Agent.** Kung ma-lapse po ang inyong policy, hihinto ang inyong coverage,
> ngunit kung may cash value na po ito, maaari itong ituloy gamit ang automatic
> premium loan o kaya ay i-reinstate sa loob ng tatlong taon.

*Lapse*, *policy*, *coverage*, *cash value*, *automatic premium loan*,
*reinstate*. Translating any of them produces a word no customer would
recognise on an insurance call. The sentence around them is Tagalog.

The escalation call shows the same rule on the way out: *"Pwede po bang
makausap ang tao"* is matched as a request for a person, and the handover line
stays in Taglish rather than switching to English at the moment of transfer.

---

## Indonesia: three things that are localised, not translated

### 1. The refusal is never the word no

> **Caller.** Nanti aja ya, lagi susah bulan ini.
> **Agent.** Saya mengerti kondisi Bapak. Kalau boleh tahu, apakah Bapak sudah
> tahu kalau cicilannya sudah jatuh tempo?

*"Nanti aja"* and *"lagi susah"* are both refusals. The agent stops asking for
payment and moves to what is actually possible.

### 2. Regional speech is answered in kind

> **Caller.** Nuwun sewu, kulo dereng saget mbayar cicilan niki.
> **Agent.** Sugeng siang, nuwun sewu Bapak...

> **Caller.** Nggih, monggo dijelaske malih.
> **Agent.** Nggih, matur nuwun Bapak...

The greeting becomes *Sugeng siang* rather than *Selamat siang*, and the
politeness words follow. Detection is from the caller's own words, never from
their name. The finance vocabulary stays standard, because there is no regional
equivalent anybody uses.

### 3. Amounts and rates are spoken, not written

> **Agent.** ...denda sebesar nol koma lima persen per hari... tidak akan
> melebihi tiga puluh persen dari nilai satu angsuran Bapak.

*Nol koma lima persen*, not *0,5%*. *Tiga puluh persen*, not *30%*. Reading
figures the written way is the fastest way to sound like a translated script
even when every word is correct.

The register is formal throughout, and the answer is grounded in the agreement
document rather than invented.

---

## What went wrong on these calls

**Short affirmations are the weak point.** *"Opo, pwede po"* came back as
*"Oppo, bayadepo"*. *"Kwarenta y singko po"* came back as *"40-5 po"*.
*"Iya, silakan"* came back as *"Sosan"*, which the agent then treated as the
customer's name and used for the rest of the call.

The recogniser handles a full sentence well and a two-word reply badly, and a
two-word reply is most of a collections call. That is the opposite of the
weighting the evaluation set has, so the measured accuracy of 97% is not the
accuracy a real caller experiences.

**The confirmation question repeats.** In both the Taglish and the Indonesian
call the agent asks whether it is speaking to the policyholder three times,
because the mangled replies never fill the slot. There is a give-up counter
after two attempts on a knowledge question, and no equivalent on a slot.

Neither of these is a localisation problem. Both are worse in the localised
markets because the recogniser has less to work with.
