# Speech recognition evaluation

Two providers, three markets, twelve utterances each. The same audio goes to both, so what is compared is the recognisers rather than the recordings.

Speech is synthesised, which makes it cleaner than a caller on a laptop microphone in a room with a fan. These are upper bounds.

## Configuration

| Market | Language hint | Why |
| --- | --- | --- |
| English, Philippines | `en` | Callers use English throughout on this product, so the hint is safe and slightly improves the handling of Filipino place names. |
| Taglish, Philippines | **none, deliberately** | Taglish switches language inside a sentence. Forcing Tagalog makes the recogniser render the English words phonetically, and forcing English does the reverse. Letting it decide per segment is the only setting that handles both halves. |
| Bahasa Indonesia | `id` | The speech is one language even when it borrows English finance words, so the hint helps. Regional politeness markers are in the prompt because they are short, unusual and easily lost. |

## Word accuracy

| Market | Case | deepgram | groq |
| --- | --- | --- | --- |
| English, Philippines | plain | 100% | 100% |
| English, Philippines | numbers | 92% | 92% |
| English, Philippines | domain terms | 100% | 100% |
| Taglish, Philippines | plain tagalog | 0% | 100% |
| Taglish, Philippines | code switch | 22% | 100% |
| Taglish, Philippines | heavy code switch | 36% | 93% |
| Taglish, Philippines | domain terms | 73% | 100% |
| Bahasa Indonesia | plain | 100% | 100% |
| Bahasa Indonesia | loanwords | 100% | 100% |
| Bahasa Indonesia | colloquial | 100% | 100% |
| Bahasa Indonesia | javanese | 75% | 88% |
| Bahasa Indonesia | sundanese | 57% | 71% |

## Summary

| Provider | Model | Mean accuracy | Median latency |
| --- | --- | --- | --- |
| deepgram | `nova-2` | 71% | 3247 ms |
| groq | `whisper-large-v3-turbo` | 95% | 392 ms |

## Where words were lost

**English, Philippines / numbers / groq**

- said: I am thirty five years old and I earn sixty thousand a month.
- heard: I am 35 years old and I earn 60,000 a month.
- lost: 1000

**English, Philippines / numbers / deepgram**

- said: I am thirty five years old and I earn sixty thousand a month.
- heard: I am 35 years old and I earn 60,000 a month.
- lost: 1000

**Taglish, Philippines / plain tagalog / deepgram**

- said: Magkano po ang hulog ko kada buwan?
- heard: (nothing)
- lost: magkano, po, ang, hulog, ko, kada, buwan
- error: silence

**Taglish, Philippines / code switch / deepgram**

- said: Magkano po ang premium ko kung monthly ang bayad?
- heard: Premium monthly
- lost: magkano, po, ang, ko, kung, ang, bayad

**Taglish, Philippines / heavy code switch / groq**

- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Ano po ang mangyayari kung malaps ang policy? May grace period po ba?
- lost: lapse

**Taglish, Philippines / heavy code switch / deepgram**

- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Policy by grace period.
- lost: ano, ang, mangyayari, kung, ma, lapse, ang, may, ba

**Taglish, Philippines / domain terms / deepgram**

- said: Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?
- heard: Sinupu ang beneficiary at kwedipo bang palitan ang beneficiary.
- lost: sino, benepisyaryo, pwede

**Bahasa Indonesia / javanese / groq**

- said: Nuwun sewu, kulo dereng saget mbayar cicilan niki.
- heard: nuwun sewu, kulodreng saget embayar cicilaniki
- lost: dereng

**Bahasa Indonesia / javanese / deepgram**

- said: Nuwun sewu, kulo dereng saget mbayar cicilan niki.
- heard: Nun Sewu, Pulau Dereng Saget membayar cicilan Niki
- lost: nuwun, kulo

**Bahasa Indonesia / sundanese / groq**

- said: Punten, abdi teh can tiasa mayar ayeuna.
- heard: Punten, Abditeh Kentiasa Mayar Ayuna
- lost: can, ayeuna

**Bahasa Indonesia / sundanese / deepgram**

- said: Punten, abdi teh can tiasa mayar ayeuna.
- heard: Punten Abdi Teh Kent kiyasa Mayar Ayuna.
- lost: can, tiasa, ayeuna


## Code switching

What each provider returned for the sentences that move between languages mid-sentence.

- **deepgram** kept both languages in 1 of 3 switching sentences.
- **groq** kept both languages in 3 of 3 switching sentences.

**plain tagalog / groq**
- said: Magkano po ang hulog ko kada buwan?
- heard: Magkano po ang hulog ko kada buwan?
- languages: tagalog throughout

**plain tagalog / deepgram**
- said: Magkano po ang hulog ko kada buwan?
- heard: (nothing)
- languages: nothing recognisable

**code switch / groq**
- said: Magkano po ang premium ko kung monthly ang bayad?
- heard: Magkano po ang premium ko kung monthly ang bayad?
- languages: switched between tagalog 75%, english 25%

**code switch / deepgram**
- said: Magkano po ang premium ko kung monthly ang bayad?
- heard: Premium monthly
- languages: english throughout  <- one language lost

**heavy code switch / groq**
- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Ano po ang mangyayari kung malaps ang policy? May grace period po ba?
- languages: switched between tagalog 67%, english 33%

**heavy code switch / deepgram**
- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Policy by grace period.
- languages: english throughout  <- one language lost

**domain terms / groq**
- said: Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?
- heard: Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?
- languages: switched between tagalog 86%, english 14%

**domain terms / deepgram**
- said: Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?
- heard: Sinupu ang beneficiary at kwedipo bang palitan ang beneficiary.
- languages: switched between english 50%, tagalog 50%
