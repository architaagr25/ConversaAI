# Indonesia: what was localised, and what could not be

Solara Multifinance Indonesia, installment reminder and collections support.
Formal and colloquial Bahasa Indonesia, finance loanwords, and regional speech
outside Jakarta.

---

## 1. Six adaptations, not translations

### 1.1 Two registers, not two politeness levels

Indonesian formal and casual are closer to two languages than to two tones.
Formal is *Bapak* or *Ibu* with *Anda*, *mohon*, *silakan*. Casual is *Kak*,
*Mas* or *Mbak* with *nggak*, *udah*, *gimana*, *aja*, and the particles *sih*,
*dong*, *kok*, *nih*.

Mixing them reads as sarcasm. *"Mohon Bapak segera bayar, cicilan kamu telat"*
is grammatically fine and openly rude.

The agent starts formal and follows the customer down. Formal is safe with
everyone; casual with an older customer is not. *Lo* and *gue* are excluded
entirely: that is Jakarta street register, not service.

Twelve replies tested, twelve register-consistent.

### 1.2 Ask when, not whether

*"Can you pay?"* invites a yes that is not true, because refusing directly is
uncomfortable. The slot asks *"kira-kira kapan bisa dibayarkan"* — roughly when
can this be paid — which is a question about timing that a person can answer
honestly.

### 1.3 A refusal is almost never the word no

*"Belum sempat"* — I have not had the chance. *"Nanti aja ya"* — later. *"Lagi
susah"* — things are hard at the moment. All three are no. A script written in
English waits for a refusal it will never hear, and asks again.

Tested:

> **Caller.** Belum sempat pak, nanti aja ya.
> **Agent.** Baik Bapak, saya mengerti Bapak sedang sibuk. Karena Bapak sedang
> mengalami kesulitan, apakah Bapak ingin saya bantu informasikan mengenai opsi
> restrukturisasi agar cicilannya bisa lebih ringan?

It hears the refusal, stops asking for payment, and moves to what is actually
possible.

### 1.4 The loanwords are the vocabulary

*Cicilan*, *angsuran*, *tenor*, *denda*, *DP*, *jatuh tempo*, *BPKB*,
*leasing*, *plafon*, *restrukturisasi*. Several are already borrowed, and no
customer uses an alternative. Translating *tenor* into something more
Indonesian would produce a word nobody would recognise on a finance call.

> **Caller.** Sisa tenornya berapa dan DP saya kemarin sudah masuk?
> **Agent.** Untuk informasi mengenai sisa tenor dan detail pembayaran DP
> Bapak, saya belum ada datanya di sini, saya cek dulu ke sistem ya Bapak.

The terms survive intact, and the agent declines rather than inventing a
balance.

### 1.5 Amounts are said in juta and ribu

*"Dua juta tiga ratus"*, not *"2.300.000"* and not *"dua koma tiga juta"*.
Rupiah is usually dropped altogether in speech. Dates are *"tanggal lima
belas"*, and the greeting changes with the hour: *pagi*, *siang*, *sore*,
*malam*.

Getting these wrong is the fastest way to sound like a translated script even
when every word is correct.

### 1.6 Collections is regulated and delicate at once

Both push the same way. The pack prohibits threatening withdrawal of the
vehicle, mentioning *penarikan* unless the customer raises it, shaming the
customer, mentioning their family, suggesting they borrow elsewhere, and
promising that a restructuring will be approved.

The agent also may not say when a vehicle would be taken. That is in the
agreement, and quoting it on a reminder call turns a reminder into a threat.

---

## 2. Regional speech outside Jakarta

Two varieties are handled: **Javanese** (Central and East Java) and
**Sundanese** (West Java). Together those are the first languages of well over
half the country.

Detection is from the customer's own words, never from their name. One marker
is enough — somebody who answers *nggih* or opens with *punten* has told you
where they are from.

| Variety | Markers detected | Greeting | Thanks |
| --- | --- | --- | --- |
| Javanese | monggo, nggih, matur nuwun, kulo, panjenengan, nuwun sewu, mboten, sampun | Sugeng siang | Matur nuwun |
| Sundanese | punten, muhun, hatur nuhun, abdi, sae, mangga | Wilujeng siang | Hatur nuhun |

Only the greeting and the politeness words change. The finance vocabulary stays
as it is, because there is no regional equivalent anybody uses.

Tested:

> **Caller.** Nuwun sewu, kulo dereng saget mbayar cicilan niki.
> **Agent.** Sugeng siang, Bapak. Nuwun sewu, saya mengerti kondisi Bapak yang
> saat ini masih belum bisa melakukan pembayaran...

> **Caller.** Nggih, monggo dijelaske malih.
> **Agent.** Nggih, matur nuwun Bapak. Jadi, restrukturisasi ini adalah proses
> penyesuaian kembali jadwal angsuran...

**A bug found while testing this.** The first version made the region sticky
once detected, so it never changed. A Sundanese caller after a Javanese one was
answered in Javanese. Sticky against *absence* is right — a customer who says
one plain sentence has not moved to Jakarta — but sticky against *evidence* is
not, and one wrong early guess would then follow the customer for the whole
call. Markers for a different variety now switch it.

---

## 3. The lines said when something breaks

Two lines are said by the system rather than by the business: when the
recogniser returns nothing, and when the model cannot be reached. Both were
English literals inside the call loop, so an Indonesian call would switch to
English at the moment the caller was already having trouble being understood.
Both now come from the pack:

| Situation | Said |
| --- | --- |
| Nothing recognised | Mohon maaf, kurang terdengar. Bisa diulangi, Pak atau Bu? |
| Model unreachable | Mohon maaf, ada kendala di sistem kami. Mohon tunggu sebentar, lalu silakan diulangi. |

Both are formal. This is the one moment the agent speaks without knowing
anything about who it is talking to, and formal is safe with everybody where
casual is not — the same reason the call opens formal and follows the customer
down.

A pack without them is refused at load rather than discovered on a live call.

The handover note for the collections team is in Bahasa Indonesia, quoting the
customer verbatim, so *"lagi susah bulan ini"* reaches the person who picks it
up in the words it was said in.

---

## 4. What could not be done

**Regional accent in the audio, as opposed to regional language.** The brief
asks for a regional accent outside standard Jakarta speech. The available
Indonesian voices are standard Jakarta Indonesian, and no free provider offers
a Javanese or Sundanese-accented Indonesian voice.

So the agent speaks *regional language* — greeting, politeness, register — in a
*standard accent*. That is a genuine partial answer, not a full one, and it is
the clearest gap in the Indonesian work. Closing it needs either a paid voice
with regional options or a fine-tuned voice model, and neither fits inside a
free tier.

Recognition of regional speech is better than production of it: the recogniser
handled Javanese Krama and Sundanese input correctly in every probe, which is
the half that matters more for understanding a caller.

**No native speaker has reviewed this.** The same gap as the Philippine pack
and for the same reason. Mechanical checks prove nothing is obviously broken.
Javanese Krama in particular has speech levels that a non-speaker cannot
evaluate, and the agent could be using the wrong one throughout without any of
the checks noticing.

**Compliance has not been reviewed.** OJK regulates collections conduct in
Indonesia, including contact hours and what may be said. The prohibitions in
the pack are engineering judgement about what is obviously wrong, not a
compliance position.

**Only two regional varieties.** Batak, Minang, Bugis and Balinese speakers get
standard Indonesian, which is understood everywhere but is not the same thing
as being met in your own language.
