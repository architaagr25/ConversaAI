"""
The agent brain: one caller turn in, one grounded reply out.

Three things happen on every turn, and the order matters for latency.
Escalation is checked first because it is a keyword match costing nothing and
it can end the turn without a model call at all. Slot extraction runs next, in
plain code, so a caller saying "I'm thirty five" has that recorded whether or
not the model notices. Retrieval starts in the background before either, since
it is the slowest part and the reply cannot be written without it.

The grounding is structural rather than instructed. The model is given the
records for this turn and nothing else, the records are rebuilt each turn so
nothing carries over, and when retrieval finds nothing confident the model is
told explicitly that it has nothing to answer from. Telling a model not to make
things up is a request; giving it nothing to make things up with is a design.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from core.llm import LanguageModel, clean_for_speech
from core.timing import Stopwatch
from knowledge_base.retrieve import get_retriever
from voice_agent.pack import Pack, build_system_prompt, build_turn_context, load_pack
from voice_agent.qualify import Assessment, assess, outcome_wording

log = logging.getLogger(__name__)

# How many past turns the model sees. Enough to hold a thread, short enough
# that a long call does not slow every reply down.
HISTORY_TURNS = 8

# Utterances shorter than this are acknowledgements, not questions.
MIN_QUESTION_CHARS = 8


@dataclass
class Turn:
    caller: str
    agent: str
    citations: list[str] = field(default_factory=list)
    grounded: bool = False
    retrieved: int = 0
    # Whether this turn went looking for knowledge at all. A slot answer does
    # not, and reporting those as ungrounded makes an ordinary call look like
    # it failed on every turn.
    sought_knowledge: bool = False
    escalated_to: str = ""
    slots_filled: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)

    @property
    def said_it_did_not_know(self) -> bool:
        """Whether this turn was a refusal rather than an answer.

        Both conditions are needed. Phrasing alone is not enough: "I do not
        have a plan available for you at sixty-five" is a grounded answer that
        happens to contain the same words as a refusal, and counting it as one
        put a correctly answered question on the list of failures.
        """
        if self.grounded or not self.sought_knowledge:
            return False
        lowered = self.agent.lower()
        return any(phrase in lowered for phrase in
                   ("do not have that", "don't have that", "do not have this",
                    "rather not guess", "wrong information", "cannot answer",
                    "do not have any information", "no information on"))


@dataclass
class Conversation:
    pack_id: str
    business_unit: str
    slots: dict = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    escalated_to: str = ""
    assessment: Assessment | None = None
    corrections: list[str] = field(default_factory=list)
    # How many times each slot has been asked. A question that keeps coming
    # back because the answer was not recognised is the most irritating thing
    # a phone agent does, so after two attempts it moves on.
    asked: dict = field(default_factory=dict)
    gave_up_on: list[str] = field(default_factory=list)
    region: str = "standard"
    started_at: float = field(default_factory=time.time)

    @property
    def unanswered(self) -> list[str]:
        return [t.caller for t in self.turns if t.said_it_did_not_know]

    @property
    def citations(self) -> list[str]:
        seen: list[str] = []
        for turn in self.turns:
            for citation in turn.citations:
                if citation not in seen:
                    seen.append(citation)
        return seen


# --- Reading what the caller said -------------------------------------------

YES = re.compile(r"\b(yes|yeah|yep|sure|correct|that is right|thats right|opo|oo|iya|ya|betul)\b", re.I)
NO = re.compile(r"\b(no|nope|not really|hindi|tidak|bukan|belum)\b", re.I)

WORD_NUMBERS = {
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9,
}

# The trailing punctuation matters. A recogniser writes "I meant 65." with a
# full stop, and anchoring on end-of-string alone missed the correction while
# happily parsing the same sentence without it.
AGE = re.compile(r"\b(\d{1,3})\s*(?:years?\s*old|yo\b|[.!?]*\s*$)"
                 r"|\bi am\s+(\d{1,3})\b"
                 r"|\bage\s+(?:is\s+)?(\d{1,3})\b", re.I)

# A bare number counts as an age only when one was asked for or is being
# corrected. Outside that, "I have 2 children" is not an age.
BARE_NUMBER = re.compile(r"\b(\d{2,3})\b")

# People correct themselves mid-call, and an agent that keeps the first answer
# ends up assessing somebody who no longer exists.
CORRECTION = re.compile(
    r"\b(actually|sorry|i mean|i meant|no wait|correction|scratch that|"
    r"my mistake|let me correct|that was wrong|hindi pala|maaf|salah)\b", re.I)


def read_age(text: str, expecting: bool = False) -> int | None:
    """Pull an age out of ordinary speech.

    Done in code rather than by the model because a number the caller said and
    the agent then forgets is the most obvious way for a call to feel broken.

    expecting widens what counts, and should be set only when an age was just
    asked for or is being corrected. Without that guard "I have 2 children"
    becomes an age.
    """
    match = AGE.search(text)
    if match:
        value = next((g for g in match.groups() if g), None)
        if value and 10 <= int(value) <= 110:
            return int(value)

    # "thirty five", which is how people usually say it out loud.
    words = re.findall(r"[a-z]+", text.lower())
    for index, word in enumerate(words):
        if word in WORD_NUMBERS and WORD_NUMBERS[word] >= 18:
            total = WORD_NUMBERS[word]
            following = words[index + 1] if index + 1 < len(words) else ""
            if following in WORD_NUMBERS and WORD_NUMBERS[following] < 10:
                total += WORD_NUMBERS[following]
            if 18 <= total <= 110:
                return total

    lone = re.fullmatch(r"\s*(\d{1,3})[.!?]*\s*", text)
    if lone and 10 <= int(lone.group(1)) <= 110:
        return int(lone.group(1))

    if expecting:
        for match in BARE_NUMBER.finditer(text):
            value = int(match.group(1))
            if 16 <= value <= 100:
                return value
    return None


def read_yes_no(text: str) -> bool | None:
    has_yes, has_no = bool(YES.search(text)), bool(NO.search(text))
    if has_yes == has_no:
        return None
    return has_yes


SCALES = {"k": 1_000, "thousand": 1_000, "ribu": 1_000,
          "m": 1_000_000, "million": 1_000_000, "juta": 1_000_000}


def read_money(text: str) -> int | None:
    """Pull an amount out of speech, written or spoken.

    People say amounts aloud as words far more often than as digits. "Sixty
    thousand a month" arriving as no answer at all means the agent asks again,
    which is the single most irritating thing a phone agent does.
    """
    cleaned = re.sub(r"[,\.](?=\d{3}\b)", "", text)

    match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\s*(k|thousand|ribu|m|million|juta)\b",
                      cleaned, re.I)
    if match:
        return int(float(match.group(1)) * SCALES[match.group(2).lower()])

    match = re.search(r"\b(\d{4,9})\b", cleaned)
    if match:
        return int(match.group(1))

    # "sixty thousand", "two hundred fifty thousand"
    words = re.findall(r"[a-z]+", cleaned.lower())
    total = 0
    for index, word in enumerate(words):
        if word not in SCALES:
            continue
        # Everything counting up to the scale word: "two hundred fifty" before
        # "thousand" is 250, not 2, 100 and 50 separately.
        running = 0
        chunk = 0
        for previous in words[max(0, index - 4):index]:
            if previous in WORD_NUMBERS:
                chunk += WORD_NUMBERS[previous]
            elif previous == "hundred":
                chunk = max(chunk, 1) * 100
            elif chunk:
                running, chunk = running + chunk, 0
        running += chunk
        if running:
            total += running * SCALES[word]

    return total or None


AFFIRMATION = re.compile(
    r"\b(yes|yeah|yep|no|nope|ok|okay|sure|right|fine|alright|thanks|"
    r"got it|opo|oo|sige|iya|ya|baik|oke|hindi|tidak)\b", re.I)

# Short replies built around a yes or a no: "no I'm not", "yes I do",
# "yeah that's right". Written as a length limit plus a marker rather than a
# list of phrasings, because there are too many phrasings to enumerate and the
# cost of getting one wrong is only half a second.
ACKNOWLEDGEMENT_CHARS = 22

SLOT_ANSWER = re.compile(
    r"^\s*(i am|i'm|im|about|around|roughly|its|it's|just)?\s*[\d\s,]*"
    r"(years?\s*old|k|thousand|ribu|juta|pesos?|php|rp)?[\s.,!]*$", re.I)


def needs_knowledge(text: str) -> bool:
    """Whether this turn should be answered from the knowledge base.

    Written as an exclusion rather than a list of question words, which is how
    it started and which quietly broke objection handling. "This sounds more
    expensive than I expected" contains no question word and no question mark,
    so nothing was retrieved and the agent improvised where an approved
    response existed.

    Objections, doubts and comparisons all need the knowledge base as much as
    questions do. What genuinely does not is a yes, a no, or a number, so those
    are the only things skipped. Retrieving unnecessarily costs half a second;
    not retrieving costs a grounded answer.
    """
    stripped = text.strip()
    if len(stripped) < MIN_QUESTION_CHARS:
        return False
    if "?" in stripped:
        return True
    if len(stripped) <= ACKNOWLEDGEMENT_CHARS and AFFIRMATION.search(stripped):
        return False
    if SLOT_ANSWER.match(stripped) and any(c.isdigit() for c in stripped):
        return False
    return True


# Kept for readability at the call site and in tests.
looks_like_a_question = needs_knowledge


class Agent:
    """Holds one call."""

    def __init__(self, pack_id: str = "health_shield_en") -> None:
        self.pack: Pack = load_pack(pack_id)
        self.system_prompt = build_system_prompt(self.pack)
        self.conversation = Conversation(pack_id=pack_id,
                                         business_unit=self.pack.business_unit)
        self.model = LanguageModel()
        self._retriever = None
        self._pool = ThreadPoolExecutor(max_workers=2)

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = get_retriever()
        return self._retriever

    def warmup(self) -> dict:
        """Pay the connection costs before anyone is waiting on them."""
        watch = Stopwatch()
        _ = self.retriever
        watch.mark("index")
        self.model.warmup()
        watch.mark("model")
        return watch.marks

    def greeting(self) -> str:
        return clean_for_speech(self.pack.opening)

    # -- one turn ------------------------------------------------------------

    def _check_escalation(self, text: str) -> str:
        lowered = text.lower()
        for trigger, phrases in (self.pack.escalation.get("detect") or {}).items():
            if any(phrase in lowered for phrase in phrases):
                return trigger

        if self._asked_again_after_no_answer(text):
            return "ESC-UNKNOWN-REPEAT"
        return ""

    @staticmethod
    def _same_question(first: str, second: str) -> bool:
        """Whether two utterances are asking the same thing.

        Word overlap rather than exact text, because somebody who did not get
        an answer rephrases rather than repeats. "Do you cover physiotherapy"
        and "is physiotherapy covered or not" are the same question asked
        twice, and matching them needs both the filler removed and the endings
        trimmed, or cover and covered count as different words.
        """
        filler = {"what", "is", "the", "a", "an", "do", "does", "you", "your",
                  "i", "my", "me", "can", "could", "would", "please", "tell",
                  "about", "for", "of", "to", "and", "how", "it", "that",
                  "so", "or", "not", "then", "just", "still", "again", "any",
                  "are", "was", "be", "have", "has", "on", "in", "at", "with"}

        def stems(text: str) -> set[str]:
            words = []
            for word in re.findall(r"[a-z]+", text.lower()):
                if word in filler or len(word) < 3:
                    continue
                for ending in ("ing", "ed", "es", "s"):
                    if word.endswith(ending) and len(word) - len(ending) >= 4:
                        word = word[: -len(ending)]
                        break
                words.append(word)
            return set(words)

        left, right = stems(first), stems(second)
        if not left or not right:
            return False
        return len(left & right) / min(len(left), len(right)) >= 0.5

    def _asked_again_after_no_answer(self, text: str) -> bool:
        """The rule is the same question twice, not two unanswered questions.

        A caller who asks two different things the agent cannot answer is
        having a normal call. A caller who asks the same thing twice and gets
        nothing both times is stuck, and a third attempt is worse than a person.
        """
        unanswered = self.conversation.unanswered
        return any(self._same_question(previous, text) for previous in unanswered)

    def _extract_slots(self, text: str) -> dict:
        found: dict = {}
        slots = self.conversation.slots
        correcting = bool(CORRECTION.search(text))

        # A correction has to overwrite. Without this the model reads the
        # correction out of the transcript and answers on the new age while the
        # recorded value stays at the old one, so the spoken outcome and the
        # recorded assessment disagree.
        last_asked = self._last_question_asked()
        if "age" not in slots or correcting:
            age = read_age(text, expecting=correcting or last_asked == "age")
            if age is not None and age != slots.get("age"):
                found["age"] = age

        # Only read a yes or no against a question that has actually been asked,
        # or "no" in "no problem" fills a slot nobody asked about.
        if last_asked in ("residency", "currently_admitted") and last_asked not in slots:
            answer = read_yes_no(text)
            if answer is not None:
                found[last_asked] = "PH" if last_asked == "residency" else answer

        if "monthly_income" not in slots and re.search(
                r"\b(income|earn|salary|month|monthly|sweldo|gaji|penghasilan|"
                r"thousand|ribu|k\b)\b", text, re.I):
            amount = read_money(text)
            # A plausible monthly figure, so an age or a policy number is not
            # mistaken for a salary.
            if amount and amount >= 1000:
                found["monthly_income"] = amount

        for level in ("Essential", "Plus", "Max"):
            if re.search(rf"\b{level}\b", text) and "plan_interest" not in slots:
                found["plan_interest"] = level

        from voice_agent.actions import contact_of

        for name, value in contact_of(text).items():
            if name not in slots:
                found[name] = value

        return found

    def _last_question_asked(self) -> str:
        """Which slot the agent's previous turn was asking about.

        Matched on subject words declared in the pack rather than on the
        scripted wording. The model rewrites every question it asks, so
        comparing against the script loses the answer about as often as it
        finds it, and a lost answer means asking the same thing twice.
        """
        if not self.conversation.turns:
            return ""
        previous = self.conversation.turns[-1].agent.lower()

        for slot in self.pack.slots:
            if any(cue in previous for cue in slot.expects):
                return slot.name

        for slot in self.pack.slots:
            stem = slot.ask.lower().split("?")[0][-40:].strip()
            if stem and stem[:24] in previous:
                return slot.name
        return ""

    def _history(self) -> str:
        recent = self.conversation.turns[-HISTORY_TURNS:]
        return "\n".join(f"Caller: {t.caller}\nYou: {t.agent}" for t in recent)

    def respond(self, caller_text: str, trace: str = "") -> Turn:
        """Answer one caller turn."""
        watch = Stopwatch()
        caller_text = caller_text.strip()

        # Retrieval is the slowest step, so it starts before anything else and
        # runs while the cheap deterministic work happens.
        pending = None
        if needs_knowledge(caller_text):
            pending = self._pool.submit(
                self.retriever.search, caller_text,
                self.conversation.business_unit, None, trace)

        self._note_region(caller_text)
        trigger = self._check_escalation(caller_text)
        filled = self._extract_slots(caller_text)
        for name, value in filled.items():
            if name in self.conversation.slots:
                log.info("caller corrected an answer",
                         extra={"slot": name, "was": self.conversation.slots[name],
                                "now": value})
                self.conversation.corrections.append(
                    f"{name}: {self.conversation.slots[name]} -> {value}")
        self.conversation.slots.update(filled)
        watch.mark("local")

        if trigger:
            if pending:
                pending.cancel()
            return self._escalate(trigger, caller_text, filled, watch)

        outcome = None
        if pending:
            outcome = pending.result()
        watch.mark("retrieval")

        records = outcome.results if outcome else []
        confident = bool(outcome and outcome.confident)
        context = build_turn_context(records, confident,
                                     self.pack.grounding["unsupported"])

        prompt = self._build_prompt(caller_text, context)
        reply = self.model.generate(prompt, system=self.system_prompt,
                                    temperature=0.5, max_tokens=220, trace=trace)
        watch.mark("model")

        turn = Turn(
            caller=caller_text,
            agent=clean_for_speech(reply.text),
            citations=[r.source_ref for r in records] if confident else [],
            grounded=confident,
            retrieved=len(records),
            sought_knowledge=pending is not None,
            slots_filled=filled,
            timings=watch.marks,
        )
        self.conversation.turns.append(turn)
        self._note_what_was_asked()
        self._reassess()
        return turn

    def _note_region(self, text: str) -> None:
        """Track the regional variety the customer is speaking.

        Sticky against absence, not against evidence. A customer who opened
        with "punten" and then says a sentence with no markers has not moved to
        Jakarta, so silence never resets it. But markers for a different
        variety are a real signal and do change it: without that, one wrong
        early guess follows the customer for the rest of the call, and a
        Sundanese speaker gets answered in Javanese, which is worse than not
        trying to match at all.
        """
        variants = self.pack.regional_variants
        if not variants:
            return

        from voice_agent.localisation import detect_region

        found = detect_region(text, variants)
        if found == "standard" or found == self.conversation.region:
            return

        if self.conversation.region != "standard":
            log.info("customer switched regional variety",
                     extra={"was": self.conversation.region, "now": found})
        else:
            log.info("regional speech detected", extra={"region": found})
        self.conversation.region = found

    def _note_what_was_asked(self) -> None:
        """Count how many times each slot has been asked, and give up at two.

        A caller who has answered twice and is asked a third time concludes
        nobody is listening, which is worse than proceeding without the answer.
        """
        asked = self._last_question_asked()
        if not asked:
            return

        counts = self.conversation.asked
        counts[asked] = counts.get(asked, 0) + 1

        if (counts[asked] >= 2 and asked not in self.conversation.slots
                and asked not in self.conversation.gave_up_on):
            self.conversation.gave_up_on.append(asked)
            log.info("asked twice without an answer, moving on",
                     extra={"slot": asked})

    def _build_prompt(self, caller_text: str, context: str) -> str:
        parts = []
        history = self._history()
        if history:
            parts.append(f"The call so far:\n{history}")

        known = {k: v for k, v in self.conversation.slots.items() if v is not None}
        if known:
            parts.append(
                "Already known, do not ask again:\n"
                + "\n".join(f"- {k}: {v}" for k, v in known.items()))

        outstanding = [s for s in self.pack.required_slots
                       if s.name not in self.conversation.slots
                       and s.name not in self.conversation.gave_up_on]
        if outstanding:
            parts.append(f"Still needed: {outstanding[0].name}. "
                         f"Ask it once the current point is dealt with.")

        # Naming what has already been asked matters more than naming what is
        # left. Without it the model re-asks a question whose answer was not
        # recognised, and the caller repeats themselves into a loop.
        already = [name for name, count in self.conversation.asked.items()
                   if count >= 1]
        if already:
            parts.append(
                "You have already asked about: " + ", ".join(already)
                + ". Do not ask any of these again in the same words. If you "
                  "did not catch an answer, ask once more differently, then "
                  "move on without it.")

        if self.conversation.assessment and self.conversation.assessment.decided:
            parts.append("Eligibility outcome to convey when the moment fits: "
                         + outcome_wording(self.conversation.assessment))

        region = self.conversation.region
        variant = self.pack.regional_variants.get(region)
        if variant and region != "standard":
            parts.append(
                f"This customer speaks {region}. Greet with "
                f"\"{variant.get('greeting')}\", thank with "
                f"\"{variant.get('thanks')}\", and use "
                f"{', '.join(variant.get('politeness', []))} where they fit. "
                f"Keep the finance vocabulary unchanged.")

        parts.append(context)
        parts.append(f"Caller just said: {caller_text}\n\nYour reply:")
        return "\n\n".join(parts)

    def _escalate(self, trigger: str, caller_text: str, filled: dict,
                  watch: Stopwatch) -> Turn:
        """Hand over without a model call.

        The wording is fixed and the decision is already made, so there is
        nothing for a model to add and every reason not to give it the chance
        to try answering once more.
        """
        self.conversation.escalated_to = trigger
        turn = Turn(
            caller=caller_text,
            agent=clean_for_speech(self.pack.closing["escalated"]),
            escalated_to=trigger,
            slots_filled=filled,
            timings=watch.marks,
        )
        self.conversation.turns.append(turn)
        log.info("escalating", extra={"trigger": trigger})
        return turn

    def _reassess(self) -> None:
        facts = self.pack.to_facts(self.conversation.slots)
        try:
            self.conversation.assessment = assess(self.pack.business_unit, facts)
        except ValueError:
            self.conversation.assessment = None

    # -- closing -------------------------------------------------------------

    def closing_line(self) -> str:
        conversation = self.conversation
        if conversation.escalated_to:
            key = "escalated"
        elif conversation.assessment and conversation.assessment.decided:
            key = "qualified" if conversation.assessment.eligible else "declined"
        else:
            key = "callback"
        return clean_for_speech(self.pack.closing[key])
