"""
What a turn tells you, extracted while the call is still running.

Two tiers, because they answer different questions and cost different amounts.

The first tier is lexical and runs on the text of the turn. It is measured in
microseconds and it can run on every turn without anybody noticing. Most of
what matters on a collections or qualification call is detectable this way:
somebody saying they cannot pay this month, somebody asking for a person,
somebody repeating a question they have already asked.

The second tier asks a model, and costs the better part of a second. It runs
only where the first tier found something it could not settle, which on the
recorded calls is roughly one turn in four. Running it on every turn would
triple the cost of a call to change almost no decisions.

Nothing here is on the caller's critical path. The reply goes out first; this
runs beside it.

Speaker attribution is free. The call loop already knows which side of the
conversation each piece of text came from, because it either transcribed it
from the caller or generated it for the agent. That is better than acoustic
diarization, which has to infer from one mixed stream what this system knows
outright, and it is never wrong about who spoke.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from core.timing import track

log = logging.getLogger(__name__)


@dataclass
class Signal:
    kind: str
    confidence: float
    evidence: str
    speaker: str = "caller"
    detail: dict = field(default_factory=dict)

    # Whether a model was asked, which is what separates a free signal from
    # one that cost most of a second.
    deliberated: bool = False


# --- Tier one: lexical ------------------------------------------------------

# Phrases are per language because these markets do not translate. An
# Indonesian customer refusing says "belum sempat", not "no". Written as
# phrases rather than single words so "bayar" on its own does not fire the
# payment-promise detector on every sentence containing the word.
PHRASES: dict[str, dict[str, list[str]]] = {
    "hardship": {
        "en": ["cannot afford", "can't afford", "too expensive", "lost my job",
               "out of work", "short of money", "tight this month",
               "no money", "struggling"],
        "fil": ["wala akong pera", "wala po akong pera", "mahal masyado",
                "medyo mahal", "hindi ko kaya", "walang trabaho",
                "nawalan ng trabaho", "kapos", "sakto lang"],
        "id": ["lagi susah", "belum ada uang", "tidak ada uang", "kena phk",
               "di-phk", "belum gajian", "berat", "kesulitan", "nggak ada uang"],
    },
    "payment_promise": {
        "en": ["i will pay", "i'll pay", "pay next week", "pay on friday",
               "after payday", "transfer tomorrow", "pay tomorrow"],
        "fil": ["magbabayad po ako", "babayaran ko po", "sa sweldo",
                "sa susunod na linggo po", "bukas po magbabayad"],
        "id": ["saya bayar", "nanti saya transfer", "setelah gajian",
               "minggu depan bayar", "besok saya bayar", "akan saya lunasi"],
    },
    # A refusal that is not the word no. The whole point of both markets.
    "soft_refusal": {
        "en": ["i'll think about it", "let me think", "maybe later",
               "not right now", "call me back", "some other time"],
        "fil": ["titingnan ko po", "sa susunod na lang po", "mamaya na lang",
                "hindi muna po", "next time na lang po", "nanti"],
        "id": ["nanti aja", "belum sempat", "lain kali", "nanti dulu",
               "pikir-pikir dulu", "belum bisa sekarang"],
    },
    # Not a refusal and not confusion. The caller is undecided, which on a
    # qualification call is the moment to slow down rather than move on. It
    # was missing entirely: "I don't know, maybe" produced nothing at all,
    # because it neither refuses nor asks anything.
    "hesitation": {
        "en": ["i don't know", "i dont know", "i am not sure", "i'm not sure",
               "not really sure", "maybe", "i guess", "possibly", "depends",
               "hard to say", "no idea"],
        "fil": ["hindi ko po alam", "ewan ko po", "siguro po", "baka po",
                "hindi po ako sigurado", "depende po"],
        "id": ["saya tidak tahu", "nggak tahu", "kurang tahu", "mungkin",
               "belum yakin", "tergantung", "kayaknya"],
    },
    "confusion": {
        "en": ["i don't understand", "what do you mean", "come again",
               "sorry what", "you lost me", "say that again"],
        "fil": ["hindi ko po maintindihan", "ano po yun", "paano po yun",
                "pakiulit po", "hindi ko gets"],
        "id": ["saya tidak mengerti", "maksudnya gimana", "kurang paham",
               "bisa diulangi", "gimana maksudnya"],
    },
    "frustration": {
        "en": ["this is ridiculous", "waste of my time", "i already told you",
               "i said already", "not listening", "fed up", "unacceptable"],
        "fil": ["sinabi ko na po", "paulit-ulit", "nakakainis",
                "ang tagal naman", "hindi niyo po ako pinapakinggan"],
        "id": ["sudah saya bilang", "berulang-ulang", "buang waktu",
               "capek deh", "nggak didengerin"],
    },
    "buying_signal": {
        "en": ["how do i sign up", "how much is it", "what do i need",
               "sounds good", "let's do it", "send me the details"],
        "fil": ["paano po mag-apply", "magkano po", "ano pong kailangan",
                "sige po, tuloy natin", "pakisend po"],
        "id": ["gimana caranya", "berapa biayanya", "apa syaratnya",
               "boleh dikirim detailnya", "saya tertarik"],
    },
}

# Something the caller mentioned that is worth more business, if anyone
# notices. A second vehicle, a spouse, a dependant, another policy elsewhere.
#
# This one is different from every other detector here, and the difference is
# the whole design. The others fire on what was said. This one has to fire on
# what was said and then *not* followed up, which means it cannot be judged
# from the caller's words alone — it needs the agent's reply in the same turn.
# An agent who hears "my wife drives it too" and immediately offers to add a
# named driver has not missed anything, and telling them they have is the kind
# of alert that gets the whole panel switched off.
OPPORTUNITY = {
    "second_vehicle": {
        "en": ["another car", "second car", "another vehicle", "second vehicle",
               "my other car", "we have two cars", "also have a motorbike"],
        "fil": ["may isa pa po kaming sasakyan", "dalawa po ang sasakyan namin",
                "may motor din po ako"],
        "id": ["mobil satunya", "ada mobil lagi", "punya dua mobil",
               "motor saya juga", "kendaraan satu lagi"],
    },
    "family_member": {
        "en": ["my wife", "my husband", "my spouse", "my son", "my daughter",
               "my kids", "my children", "for my parents", "my mother",
               "my father"],
        "fil": ["asawa ko po", "anak ko po", "mga anak ko po", "misis ko po",
                "nanay ko po", "tatay ko po"],
        "id": ["istri saya", "suami saya", "anak saya", "orang tua saya",
               "ibu saya", "bapak saya"],
    },
    "existing_cover": {
        "en": ["i already have a policy", "i have insurance with",
               "covered through work", "my company covers", "another provider"],
        "fil": ["may insurance na po ako", "may policy na po ako",
                "sa company po may coverage"],
        "id": ["sudah punya asuransi", "ada asuransi dari kantor",
               "di perusahaan lain"],
    },
}

# What counts as the agent having noticed. Deliberately broad: the cost of
# missing a follow-up that did happen is a false alarm, and the cost of a false
# alarm on this detector is somebody being told to sell to a customer they are
# already selling to.
FOLLOWED_UP = [
    "add", "include", "cover", "also", "too", "second", "additional", "extra",
    "as well", "them", "family", "dependant", "dependent", "spouse", "driver",
    "kasama", "isama", "din po", "dagdag", "tambah", "sekalian", "juga",
    "tambahan", "keluarga",
]


def _agent_followed_up(agent_text: str) -> bool:
    """Whether the agent's reply shows it acted on what was mentioned.

    Matched on whole words, which everything else in this file does not need
    to be. The other phrase lists are multi-word and distinctive; these are
    short and ordinary, and substring matching quietly broke the detector:
    "too" is inside "understood", so "Understood. What is your annual income?"
    counted as a follow-up and suppressed a real miss. "Add" is inside
    "address" and would have done the same to every turn asking for one.
    """
    lowered = agent_text.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered)
               for word in FOLLOWED_UP)


# Said by the agent, not the caller. A promise it is not allowed to make is
# worth catching in the second it is made rather than in a report next month.
AGENT_RISK = {
    "guarantee": ["guaranteed", "i guarantee", "definitely approved",
                  "sigurado po", "garantisado", "pasti disetujui",
                  "dijamin", "siguradong maaprubahan"],
    "pressure": ["last chance", "today only", "you must decide now",
                 "ngayon lang po", "huling pagkakataon", "harus sekarang",
                 "kalau tidak sekarang"],
    "threat": ["we will take the vehicle", "kukunin ang sasakyan",
               "mobilnya kami tarik", "akan kami tarik", "penarikan unit"],
}


# A preliminary decision is not a decision, and saying one without the other
# half is the compliance gap the brief describes as a missing disclosure. In
# both of these markets an eligibility answer on a first call is always subject
# to underwriting, and a caller who is told "you are approved" and later
# declined has been misled even though nobody lied.
DECISION_WORDS = [
    "you are eligible", "you qualify", "you're approved", "you are approved",
    "approved", "qualified", "kwalipikado", "aprubado",
    "anda memenuhi syarat", "disetujui", "layak",
]

# What turns it into a preliminary decision. Any one of these is enough.
DECISION_QUALIFIERS = [
    "subject to", "pending", "preliminary", "initial", "indicative",
    "final approval", "underwriting", "still needs", "once we", "after we",
    "depends on", "provisional",
    "depende", "kailangan pa", "pansamantala", "paunang",
    "tergantung", "sementara", "masih perlu", "awal", "belum final",
]


def _hits(text: str, phrases: dict[str, list[str]]) -> list[str]:
    lowered = f" {text.lower()} "
    return [p for group in phrases.values() for p in group if p in lowered]


@dataclass
class TurnInput:
    """Everything known about one exchange, at the moment it happens."""

    caller: str = ""
    agent: str = ""
    turn_number: int = 0
    business_unit: str = "health_ph_en"
    language: str = "en"
    # Supplied by the call loop, which already knows these.
    agent_refused: bool = False
    grounded: bool = False
    escalated_to: str = ""
    corrections: list[str] = field(default_factory=list)
    asked_before: list[str] = field(default_factory=list)
    silence_ms: float = 0.0
    must_never: list[str] = field(default_factory=list)


def lexical_signals(turn: TurnInput) -> list[Signal]:
    """Everything detectable without asking a model."""
    found: list[Signal] = []

    for kind, phrases in PHRASES.items():
        hits = _hits(turn.caller, phrases)
        if hits:
            # More than one phrase is stronger evidence, but a single clear
            # phrase is already worth acting on, so the floor is high and the
            # ceiling is close to it.
            confidence = 0.72 if len(hits) == 1 else 0.88
            found.append(Signal(kind=kind, confidence=confidence,
                                evidence=hits[0], detail={"matched": hits}))

    # Only when the agent did not already act on it. Judged on the same turn,
    # because that is where the reply to the mention lives.
    if not _agent_followed_up(turn.agent):
        for what, phrases in OPPORTUNITY.items():
            hits = _hits(turn.caller, phrases)
            if hits:
                found.append(Signal(
                    kind="missed_opportunity",
                    # Lower than the other lexical signals on purpose. This one
                    # infers something did not happen, which is a weaker claim
                    # than something did, and it is the detector most likely to
                    # be wrong about an agent who was getting to it.
                    confidence=0.74,
                    evidence=hits[0],
                    detail={"opportunity": what, "matched": hits}))

    for kind, phrases in AGENT_RISK.items():
        hits = _hits(turn.agent, {"any": phrases})
        if hits:
            found.append(Signal(kind=f"agent_{kind}", confidence=0.9,
                                evidence=hits[0], speaker="agent",
                                detail={"matched": hits}))

    # A decision stated as final when it can only be preliminary.
    decision = _hits(turn.agent, {"any": DECISION_WORDS})
    if decision and not _hits(turn.agent, {"any": DECISION_QUALIFIERS}):
        found.append(Signal(kind="missing_disclosure", confidence=0.85,
                            evidence=decision[0], speaker="agent",
                            detail={"matched": decision}))

    # These come from the call loop rather than from the words, and are
    # certain rather than inferred, so they carry full confidence.
    if turn.agent_refused:
        found.append(Signal(kind="knowledge_gap", confidence=1.0,
                            evidence=turn.caller, speaker="agent"))
    if turn.escalated_to:
        found.append(Signal(kind="escalation", confidence=1.0,
                            evidence=turn.escalated_to, speaker="agent"))
    if turn.corrections:
        found.append(Signal(kind="data_conflict", confidence=1.0,
                            evidence=turn.corrections[-1]))
    if turn.silence_ms >= 4000:
        found.append(Signal(kind="dead_air", confidence=1.0,
                            evidence=f"{turn.silence_ms / 1000:.1f}s",
                            detail={"ms": turn.silence_ms}))

    # The caller asking something already asked means the first answer did not
    # land, whatever it said.
    repeated = _repeat_of(turn.caller, turn.asked_before)
    if repeated:
        found.append(Signal(kind="repeated_question", confidence=0.8,
                            evidence=repeated))

    return found


STOPWORDS = {"the", "a", "an", "is", "are", "do", "does", "po", "ang", "ng",
             "ba", "sa", "ko", "yang", "di", "ke", "saya", "apa", "what",
             "how", "and", "or", "to", "of", "in", "on", "for", "my", "i"}


def _repeat_of(text: str, earlier: list[str]) -> str:
    """Whether this question was already asked, by word overlap.

    Matching on the exact string would miss "magkano po ang premium" against
    "magkano po ba ang premium ko", which is the same question. Stopwords are
    dropped first, because two Tagalog questions share "po" and "ang"
    regardless of what they are about.
    """
    words = {w for w in re.findall(r"[a-z']+", text.lower())
             if w not in STOPWORDS and len(w) > 2}
    if len(words) < 2:
        return ""
    for previous in earlier:
        other = {w for w in re.findall(r"[a-z']+", previous.lower())
                 if w not in STOPWORDS and len(w) > 2}
        if not other:
            continue
        overlap = len(words & other) / min(len(words), len(other))
        # Half the shorter question's content words. Set at 0.6 first, which
        # missed "magkano po ang premium ko kada buwan" against "magkano po ba
        # ang premium ko every month" — the same question, where the only
        # words that survive a switch into English are the loanwords.
        if overlap >= 0.5:
            return previous
    return ""


# --- Tier two: the model ----------------------------------------------------

# Asked only about what the first tier could not settle. Kept to one word out
# so the reply is short enough to arrive inside the turn.
DELIBERATION_PROMPT = """Read one exchange from a live phone call and answer \
about the caller only.

Caller: {caller}
Agent: {agent}

Reply with exactly one line, three fields separated by |:
sentiment(positive/neutral/negative) | intent(one or two words) | \
at_risk_of_hanging_up(yes/no)

Nothing else."""

# When to bother. An exchange with no lexical signal and a cooperative
# customer does not need a model to confirm it is fine.
def needs_deliberation(turn: TurnInput, found: list[Signal]) -> bool:
    kinds = {s.kind for s in found}
    if kinds & {"frustration", "soft_refusal", "hardship", "repeated_question"}:
        return True
    # A long caller turn with nothing matched is the case the phrase lists
    # are worst at: an unusual objection in words nobody wrote down.
    return len(turn.caller.split()) >= 12 and not kinds


def deliberate(turn: TurnInput, model=None, trace: str = "") -> list[Signal]:
    """Ask the model what the phrases could not settle."""
    # The live model, not the offline one. This has to land inside the turn,
    # so a slower and better answer is the wrong trade here.
    from core.llm import live

    model = model or live
    prompt = DELIBERATION_PROMPT.format(caller=turn.caller or "(nothing)",
                                        agent=turn.agent or "(nothing)")
    try:
        with track("insight_model", trace=trace):
            reply = model.generate(prompt, temperature=0.0, max_tokens=40,
                                   thinking_budget=0, trace=trace)
    except Exception as exc:
        log.warning("live analysis unavailable", extra={"reason": str(exc)[:120]})
        return []

    parts = [p.strip().lower() for p in (reply.text or "").split("|")]
    if len(parts) < 3:
        log.info("live analysis returned an unusable line",
                 extra={"got": (reply.text or "")[:80]})
        return []

    sentiment, intent, at_risk = parts[0], parts[1], parts[2]
    out = [Signal(kind="sentiment", confidence=0.7, evidence=sentiment,
                  detail={"value": sentiment}, deliberated=True),
           Signal(kind="intent", confidence=0.7, evidence=intent,
                  detail={"value": intent}, deliberated=True)]
    if at_risk.startswith("yes"):
        out.append(Signal(kind="churn_risk", confidence=0.75,
                          evidence="model judged the caller likely to hang up",
                          deliberated=True))
    return out


def extract(turn: TurnInput, model=None, trace: str = "",
            allow_model: bool = True) -> list[Signal]:
    """Both tiers, second one only where the first left a question open."""
    with track("insight_lexical", trace=trace):
        found = lexical_signals(turn)

    if allow_model and needs_deliberation(turn, found):
        found.extend(deliberate(turn, model=model, trace=trace))
    return found
