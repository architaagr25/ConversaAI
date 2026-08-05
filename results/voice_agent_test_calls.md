# Voice agent test calls

Five calls placed over the WebSocket interface, one per situation the agent has to handle. The caller's speech is synthesised and streamed in real time, so the endpointer, the recogniser and the reply loop all run exactly as they would with a person on the line.

Every call has a recording, a transcript with sources, and a lead.

| Call | Covers | Turns | Grounded | Declined to answer | Escalated |
| --- | --- | --- | --- | --- | --- |
| `conflicting` | Conflicting details, the caller corrects an earlier answer | 5 | 2 | 0 | no |
| `cooperative` | Cooperative customer, straight through qualification | 7 | 6 | 0 | no |
| `escalation` | The caller asks for a person | 3 | 0 | 0 | ESC-REQUEST |
| `objection` | Objection handling, answered from approved responses | 6 | 3 | 0 | no |
| `out_of_scope` | A question the knowledge base cannot answer | 3 | 1 | 1 | no |

## Response time

Measured from the moment the caller stops talking to the first audio of the reply. That is the silence they actually experience, and it includes the endpointer waiting to be sure the turn is over, transcription, retrieval, the model, and speech synthesis of the first sentence.

- turns measured: 24
- median: 3664 ms
- fastest: 1786 ms
- slowest: 7308 ms

The slowest figures come from free tier throttling rather than from the work itself.

---

## conflicting

**Conflicting details, the caller corrects an earlier answer**

Recording `results/calls/conflicting.wav` &middot; transcript `results/calls/conflicting.txt` &middot; detail `results/calls/conflicting.json`

**Agent.** Hi, this is Maya from Solara Health Shield. I help people find out which health plan fits them. Is now an alright time for a few quick questions?

**Caller.** Sure, go ahead.

**Agent.** Thanks. What made you look into health cover just now?

**Caller.** I am 28 years old.

**Agent.** Got it, thanks for sharing that. Are you currently living in the Philippines?

**Caller.** Actually sorry, I meant 65.

**Agent.** I appreciate you clarifying that for me. Since our entry age limit is sixty, I’m afraid I cannot offer you a policy today. Would you like me to get a colleague to see if there is anything else we can do for you?
> answered from `web/health-shield-faq.html#Can I add my family?`, `web/health-shield-faq.html#"This is more expensive than I expected."`, `documents/health_shield_policy_wording.pdf#5 Eligibility`

**Caller.** Yes, I live here.

**Agent.** Thanks for confirming that. Since our entry age limit is sixty, I am afraid I cannot offer you a policy today. Would you like me to get a colleague to see if there is anything else we can do for you?

**Caller.** Am I still eligible for this plan?

**Agent.** I understand you are asking about your eligibility. Because our entry age limit for this plan is sixty, I am afraid I cannot offer you a policy today. 
Would you like me to get a colleague to see if there is anything else we can do for you?
> answered from `documents/health_shield_policy_wording.pdf#Health Shield Policy Wording`, `rules/qualification_rules.yaml#health_ph_en.hard_rules`, `documents/health_shield_policy_wording.pdf#3 General exclusions`

### Outcome

- collected: `{'age': 65}`
- corrections: age: 28 -> 65
- eligibility: declined, Entry age for Health Shield is 18 to 60.
- lead: `LD-20260805-95ea7a` (declined)
- next action: Do not quote. Send the declined-applicant letter and offer the alternatives.


---

## cooperative

**Cooperative customer, straight through qualification**

Recording `results/calls/cooperative.wav` &middot; transcript `results/calls/cooperative.txt` &middot; detail `results/calls/cooperative.json`

**Agent.** Hi, this is Maya from Solara Health Shield. I help people find out which health plan fits them. Is now an alright time for a few quick questions?

**Caller.** Yes, now is a good time.

**Agent.** Great. I'm Maya from Solara Health Shield. What made you look into health cover just now?
> answered from `documents/health_shield_policy_wording.pdf#5 Eligibility`, `rules/qualification_rules.yaml#health_ph_en.hard_rules`, `documents/health_shield_policy_wording.pdf#1 Definitions`

**Caller.** I am looking for cover for myself and my wife.

**Agent.** That makes sense. It is good to have that extra peace of mind for both of you. And how old are you?
> answered from `web/health-shield-faq.html#Can I add my family?`, `documents/health_shield_policy_wording.pdf#5 Eligibility`, `web/health-shield-faq.html#"I am already covered through my employer."`

**Caller.** I am 35 years old.

**Agent.** Thanks. Are you currently living in the Philippines?

**Caller.** Yes, I live in the Philippines.

**Agent.** That is good to know. Just to check, are you in hospital or receiving treatment right now?
> answered from `documents/health_shield_policy_wording.pdf#5 Eligibility`, `web/health-shield-plans.html#Who can apply`, `documents/health_shield_rate_table.pdf#Health Shield Rate Table`

**Caller.** No, I am not in hospital

**Agent.** Understood. Since you mentioned earlier that you are looking for cover for your wife as well, would this be just for the two of you, or for other family members too?
> answered from `documents/health_shield_policy_wording.pdf#6 Claims`, `documents/health_shield_policy_wording.pdf#1 Definitions`, `web/health-shield-faq.html#Can I use any hospital?`

**Caller.** About 60,000 a month

**Agent.** Got it, thank you for sharing that. Before I forget, can I take your name?
> answered from `documents/health_shield_rate_table.pdf#Dependant monthly premium`, `documents/health_shield_rate_table.pdf#Standard monthly premium by entry age`, `documents/health_shield_rate_table.pdf#Health Shield Rate Table`

**Caller.** What does the Plus Plan cover?

**Agent.** The Plus plan offers an annual benefit limit of seven hundred fifty thousand pesos. It includes eight out-patient consultations per year and an annual physical examination. Would you like me to go over the room and board or emergency accident limits for that plan?
> answered from `web/health-shield-plans.html#Compare plans`, `documents/health_shield_policy_wording.pdf#3 General exclusions`, `web/health-shield-plans.html#What is not covered`

### Outcome

- collected: `{'age': 35, 'residency': 'PH', 'currently_admitted': False, 'monthly_income': 60000, 'plan_interest': 'Plus'}`
- eligibility: eligible
- lead: `LD-20260805-af9a08` (qualified)
- next action: Quote from the rate table and call back within one working day.


---

## escalation

**The caller asks for a person**

Recording `results/calls/escalation.wav` &middot; transcript `results/calls/escalation.txt` &middot; detail `results/calls/escalation.json`

**Agent.** Hi, this is Maya from Solara Health Shield. I help people find out which health plan fits them. Is now an alright time for a few quick questions?

**Caller.** Yes okay

**Agent.** That sounds good. What made you look into health cover just now?

**Caller.** I am 30 years old.

**Agent.** Got it. Thanks for sharing that with me. Are you currently living in the Philippines?

**Caller.** Actually, can I speak to a real person please?

**Agent.** I am putting you through to a colleague now. They will have everything you have told me.

### Outcome

- collected: `{'age': 30}`
- eligibility: undecided, still needs currently_admitted, residency
- escalated: ESC-REQUEST
- lead: `LD-20260805-27c471` (escalated)
- next action: Handed to a person during the call. No callback needed.


---

## objection

**Objection handling, answered from approved responses**

Recording `results/calls/objection.wav` &middot; transcript `results/calls/objection.txt` &middot; detail `results/calls/objection.json`

**Agent.** Hi, this is Maya from Solara Health Shield. I help people find out which health plan fits them. Is now an alright time for a few quick questions?

**Caller.** Yes, go ahead.

**Agent.** Great. What made you look into health cover just now?

**Caller.** I am 42.

**Agent.** Thanks for sharing that. Are you currently living in the Philippines?

**Caller.** Yes, I am based in Manila.

**Agent.** That is good to know. Just to check, are you in hospital or receiving treatment right now?
> answered from `documents/health_shield_rate_table.pdf#Health Shield Rate Table`, `web/health-shield-plans.html#Compare plans`, `web/health-shield-plans.html#Who can apply`

**Caller.** No, I am not.

**Agent.** That is good to hear. Would this be just for you, or for family as well?

**Caller.** Honestly, this sounds more expensive than I was expecting.

**Agent.** I understand that. Cover can be adjusted to fit your budget. Moving from Plus to Essential lowers the monthly contribution considerably, and you can also pay annually to reduce the total by about five percent. Does that sound like a better way to look at it?
> answered from `web/health-shield-faq.html#"This is more expensive than I expected."`, `documents/health_shield_rate_table.pdf#Health Shield Rate Table`, `documents/health_shield_policy_wording.pdf#Health Shield Policy Wording`

**Caller.** I already have insurance through my employer anyway.

**Agent.** That is common. Employer cover usually ends when your employment does, and it rarely covers dependants at the same level. A personal policy stays with you regardless of where you work. Would this be just for you, or for family as well?
> answered from `web/health-shield-faq.html#"I am already covered through my employer."`, `documents/health_shield_policy_wording.pdf#6 Claims`, `web/health-shield-faq.html#"I never claim, so it is wasted money."`

### Outcome

- collected: `{'age': 42, 'residency': 'PH', 'currently_admitted': False}`
- eligibility: eligible
- lead: `LD-20260805-13ab2f` (qualified)
- next action: Quote from the rate table and call back within one working day.


---

## out_of_scope

**A question the knowledge base cannot answer**

Recording `results/calls/out_of_scope.wav` &middot; transcript `results/calls/out_of_scope.txt` &middot; detail `results/calls/out_of_scope.json`

**Agent.** Hi, this is Maya from Solara Health Shield. I help people find out which health plan fits them. Is now an alright time for a few quick questions?

**Caller.** Yes that is fine.

**Agent.** That is great to hear. What made you look into health cover just now?

**Caller.** Does the plan cover dental treatment?

**Agent.** Dental treatment is not covered on any of our plans. The only exception is if you need reconstructive dental work following an accident.
And how old are you?
> answered from `documents/health_shield_policy_wording.pdf#3 General exclusions`, `web/health-shield-faq.html#Is dental included?`, `web/health-shield-plans.html#What is not covered`

**Caller.** What is the capital of France?

**Agent.** I do not have that detail here. I can have someone check and come back to you. 
And how old are you?
> nothing matched, and the agent said so

### Outcome

- eligibility: undecided, still needs age, currently_admitted, residency
- declined to answer: "What is the capital of France?"
- lead: `LD-20260805-c15174` (incomplete)
- next action: Call back to complete qualification. Still needed: age, currently_admitted, residency.
