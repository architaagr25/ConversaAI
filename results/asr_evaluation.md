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
| English, Philippines | numbers | 100% | 100% |
| English, Philippines | domain terms | 100% | 100% |
| Taglish, Philippines | plain tagalog | 0% | 100% |
| Taglish, Philippines | code switch | 22% | 100% |
| Taglish, Philippines | heavy code switch | 36% | 93% |
| Taglish, Philippines | domain terms | 27% | 100% |
| Taglish, Philippines | english heavy | 67% | 100% |
| Taglish, Philippines | amounts | 0% | 100% |
| Bahasa Indonesia | plain | 100% | 100% |
| Bahasa Indonesia | loanwords | 100% | 100% |
| Bahasa Indonesia | colloquial | 100% | 100% |
| Bahasa Indonesia | formal | 100% | 100% |
| Bahasa Indonesia | amounts | 100% | 100% |
| Bahasa Indonesia | javanese greeting | 75% | 88% |
| Bahasa Indonesia | javanese reply | 88% | 100% |
| Bahasa Indonesia | sundanese greeting | 57% | 71% |
| Bahasa Indonesia | sundanese reply | 75% | 100% |

## Summary

| Provider | Model | Mean accuracy | Median latency |
| --- | --- | --- | --- |
| deepgram | `nova-2` | 69% | 4481 ms |
| groq | `whisper-large-v3-turbo` | 97% | 559 ms |

## Standard against regional speech

Averaging these together hides the case that matters. Javanese and Sundanese are first languages for well over half of Indonesia, and neither provider handles them as well as it handles Jakarta Indonesian.

| Provider | Standard | Regional |
| --- | --- | --- |
| deepgram | 68% (14 cases) | 74% (4 cases) |
| groq | 99% (14 cases) | 90% (4 cases) |

## Observed errors

Every word that did not survive, and why. Grouped by kind rather than listed, because the kind is what can be acted on.

**deepgram**

| Kind | Count | Examples |
| --- | --- | --- |
| one language dropped | 20 | magkano (code switch), po (code switch), ang (code switch), ko (code switch) |
| whole utterance lost | 15 | magkano (plain tagalog), po (plain tagalog), ang (plain tagalog), hulog (plain tagalog) |
| dropped | 6 | sino (domain terms), po (domain terms), at (domain terms), pwede (domain terms) |
| regional word, dropped | 3 | kulo (javanese greeting), can (sundanese greeting), tiasa (sundanese greeting) |
| heard as beneficiary | 1 | benepisyaryo (domain terms) |
| heard as ang | 1 | bang (domain terms) |
| regional word, heard as nun | 1 | nuwun (javanese greeting) |
| regional word, heard as gih | 1 | nggih (javanese reply) |
| regional word, heard as ayuna | 1 | ayeuna (sundanese greeting) |
| regional word, heard as abdi | 1 | bade (sundanese reply) |
| regional word, heard as meyar | 1 | mayar (sundanese reply) |

**groq**

| Kind | Count | Examples |
| --- | --- | --- |
| regional word, dropped | 2 | dereng (javanese greeting), can (sundanese greeting) |
| heard as malaps | 1 | lapse (heavy code switch) |
| regional word, heard as ayuna | 1 | ayeuna (sundanese greeting) |


## Where words were lost

**Taglish, Philippines / plain tagalog / deepgram**

- said: Magkano po ang hulog ko kada buwan?
- heard: (nothing)
- lost: magkano (whole utterance lost), po (whole utterance lost), ang (whole utterance lost), hulog (whole utterance lost), ko (whole utterance lost), kada (whole utterance lost), buwan (whole utterance lost)
- error: silence

**Taglish, Philippines / code switch / deepgram**

- said: Magkano po ang premium ko kung monthly ang bayad?
- heard: Premium monthly
- lost: magkano (one language dropped), po (one language dropped), ang (one language dropped), ko (one language dropped), kung (one language dropped), ang (one language dropped), bayad (one language dropped)

**Taglish, Philippines / heavy code switch / groq**

- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Ano po ang mangyayari kung malaps ang policy? May grace period po ba?
- lost: lapse (heard as malaps)

**Taglish, Philippines / heavy code switch / deepgram**

- said: Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?
- heard: Policy by grace period.
- lost: ano (one language dropped), ang (one language dropped), mangyayari (one language dropped), kung (one language dropped), ma (one language dropped), lapse (one language dropped), ang (one language dropped), may (one language dropped), ba (one language dropped)

**Taglish, Philippines / domain terms / deepgram**

- said: Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?
- heard: Sinupu ang beneficiary.
- lost: sino (dropped), po (dropped), benepisyaryo (heard as beneficiary), at (dropped), pwede (dropped), po (dropped), bang (heard as ang), palitan (dropped)

**Taglish, Philippines / english heavy / deepgram**

- said: Ang beneficiary po ba pwede more than one, o isa lang po?
- heard: An beneficiary POBAP already more than one
- lost: ang (one language dropped), pwede (one language dropped), isa (one language dropped), lang (one language dropped)

**Taglish, Philippines / amounts / deepgram**

- said: Isang libo dalawang daan po ang kaya kong bayaran kada buwan.
- heard: (nothing)
- lost: 1200 (whole utterance lost), po (whole utterance lost), ang (whole utterance lost), kaya (whole utterance lost), kong (whole utterance lost), bayaran (whole utterance lost), kada (whole utterance lost), buwan (whole utterance lost)
- error: silence

**Bahasa Indonesia / javanese greeting / groq**

- said: Nuwun sewu, kulo dereng saget mbayar cicilan niki.
- heard: nuwun sewu, kulodreng saget embayar cicilaniki
- lost: dereng (regional word, dropped)

**Bahasa Indonesia / javanese greeting / deepgram**

- said: Nuwun sewu, kulo dereng saget mbayar cicilan niki.
- heard: Nun Sewu, Pulau Dereng Saget membayar Cicilaniki.
- lost: nuwun (regional word, heard as nun), kulo (regional word, dropped)

**Bahasa Indonesia / javanese reply / deepgram**

- said: Nggih monggo pak, kulo sampun mbayar wingi sonten.
- heard: Gih monggopak, kulo sampun membayar wingi sonten.
- lost: nggih (regional word, heard as gih)

**Bahasa Indonesia / sundanese greeting / groq**

- said: Punten, abdi teh can tiasa mayar ayeuna.
- heard: Punten, Abditeh Kentiasa Mayar Ayuna
- lost: can (regional word, dropped), ayeuna (regional word, heard as ayuna)

**Bahasa Indonesia / sundanese greeting / deepgram**

- said: Punten, abdi teh can tiasa mayar ayeuna.
- heard: Punten Abdi Teh Kentyasa Mayar Ayuna.
- lost: can (regional word, dropped), tiasa (regional word, dropped), ayeuna (regional word, heard as ayuna)

**Bahasa Indonesia / sundanese reply / deepgram**

- said: Muhun, abdi bade mayar minggu payun, hatur nuhun.
- heard: Muhun, Abdi Badai Meyar Minggu Payun, Hatur nuhun.
- lost: bade (regional word, heard as abdi), mayar (regional word, heard as meyar)


## Code switching

What each provider returned for the sentences that move between languages mid-sentence.

- **deepgram** kept both languages in 1 of 4 switching sentences.
- **groq** kept both languages in 4 of 4 switching sentences.

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
- heard: Sinupu ang beneficiary.
- languages: switched between tagalog 50%, english 50%

**english heavy / groq**
- said: Ang beneficiary po ba pwede more than one, o isa lang po?
- heard: Ang beneficiary po ba pwede more than one o isa lang po?
- languages: switched between tagalog 83%, english 17%

**english heavy / deepgram**
- said: Ang beneficiary po ba pwede more than one, o isa lang po?
- heard: An beneficiary POBAP already more than one
- languages: english throughout  <- one language lost

**amounts / groq**
- said: Isang libo dalawang daan po ang kaya kong bayaran kada buwan.
- heard: Isang libo dalawang daan po ang kaya kong bayaran kada buwan
- languages: tagalog throughout

**amounts / deepgram**
- said: Isang libo dalawang daan po ang kaya kong bayaran kada buwan.
- heard: (nothing)
- languages: nothing recognisable
