"""
Checks on localised output that a native speaker would make instinctively.

Politeness in Filipino is grammar. "Po" marks formal address and carries a set
of pronouns with it: kayo, niyo, inyo. Putting "mo" or "ikaw" in the same
sentence is not a small inconsistency, it is the equivalent of switching
between "sir" and "mate" mid-sentence, and it is the single most common thing
a model gets wrong when asked to write Taglish.

This was measured back when the models were chosen: asked for a Taglish
objection response, the model produced "Gets ko po na medyo mabigat sa budget
ang life insurance... pasok sa presyong kaya mo", which is formal and informal
four words apart.

Indonesian has the same problem in a different shape. "Bapak" and "Ibu" belong
with "Anda"; "kamu" belongs with neither.

None of this is a substitute for a native speaker reviewing the output. It
catches the mistakes that are mechanical, which is most of them but not all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Formal Filipino address. "Po" and "opo" are the markers; the rest are the
# pronouns that belong with them.
FIL_FORMAL = re.compile(r"\b(po|opo|kayo|niyo|ninyo|inyo|inyong)\b", re.I)

# Informal address. Fine on their own, wrong beside "po".
FIL_INFORMAL = re.compile(r"\b(ka|mo|ikaw|iyo|kita|mong|iyong)\b", re.I)

# Formal Indonesian address.
ID_FORMAL = re.compile(r"\b(bapak|ibu|anda|pak|bu|silakan|mohon)\b", re.I)

# Casual Indonesian address, which is normal in a friendly call and wrong
# alongside "Anda" or "Bapak".
ID_INFORMAL = re.compile(r"\b(kamu|lo|lu|gue|gua|elo)\b", re.I)

# English that should not appear in a reply meant to be in the local language.
# Technical terms are excluded because they genuinely belong.
KEEP_IN_ENGLISH = {
    "premium", "policy", "due", "date", "grace", "period", "lapse", "lapsed",
    "beneficiary", "rider", "coverage", "cover", "reinstatement", "claim",
    "branch", "bank", "online", "agent", "adviser", "insurance", "plan",
    "cicilan", "tenor", "denda", "dp", "bpkb", "leasing", "customer",
    "service", "ok", "okay", "sir", "maam", "ma", "am",
}

ENGLISH_FUNCTION_WORDS = re.compile(
    r"\b(the|is|are|was|were|and|but|for|with|that|this|there|have|has|will|"
    r"would|could|should|please|thank|you|your|we|our|they|them|from|about|"
    r"because|however|therefore|regarding)\b", re.I)


@dataclass
class RegisterCheck:
    text: str
    language: str
    mixed: bool = False
    formal_found: list[str] = field(default_factory=list)
    informal_found: list[str] = field(default_factory=list)
    english_drift: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mixed and not self.english_drift

    def explain(self) -> str:
        if self.ok:
            return "consistent"
        parts = []
        if self.mixed:
            parts.append(f"mixes formal {sorted(set(self.formal_found))} with "
                         f"informal {sorted(set(self.informal_found))}")
        if self.english_drift:
            parts.append(f"drifts into English: {sorted(set(self.english_drift))}")
        return "; ".join(parts)


def check_register(text: str, language: str = "fil") -> RegisterCheck:
    """Look for mixed politeness and unwanted English, sentence by sentence.

    Checked per sentence rather than per reply. A whole reply can legitimately
    move from formal to a softer close; a single sentence cannot.
    """
    formal_pattern, informal_pattern = (
        (FIL_FORMAL, FIL_INFORMAL) if language in ("fil", "tl")
        else (ID_FORMAL, ID_INFORMAL)
    )

    result = RegisterCheck(text=text, language=language)

    for sentence in re.split(r"(?<=[.!?])\s+", text):
        formal = formal_pattern.findall(sentence)
        informal = informal_pattern.findall(sentence)
        if formal and informal:
            result.mixed = True
            result.formal_found.extend(formal)
            result.informal_found.extend(informal)

    # Drift means the reply left the target language, not that it contains
    # English. In Taglish English is the point: "si Ella po ito from Solara" is
    # exactly how people speak, and an earlier version of this check flagged
    # "from" as a fault. What is worth catching is a reply that has gone
    # almost entirely English, which is no longer Taglish at all.
    if language in ("fil", "tl"):
        if taglish_balance(text) > 0.85 and len(text.split()) > 6:
            result.english_drift = ["reply is essentially all English"]
    else:
        for word in ENGLISH_FUNCTION_WORDS.findall(text):
            if word.lower() not in KEEP_IN_ENGLISH:
                result.english_drift.append(word.lower())

    return result


def taglish_balance(text: str) -> float:
    """Roughly what share of the words are English.

    Real Taglish sits somewhere in the middle. All Tagalog reads as a
    translation exercise; all English is not Taglish at all. This is a blunt
    instrument, and it is here to catch a reply that has gone entirely one way.
    """
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return 0.0

    tagalog = re.compile(
        r"^(po|opo|ang|ng|sa|na|ay|mga|ito|iyan|iyon|kayo|niyo|ninyo|inyo|ako|"
        r"namin|natin|kami|tayo|hindi|oo|wala|meron|mayroon|pwede|puwede|"
        r"kung|para|dahil|kasi|pero|at|o|salamat|magandang|araw|umaga|hapon|"
        r"gabi|maganda|mabuti|sige|lang|din|rin|naman|nga|ba|pa|nyo|ninyong|"
        r"tayong|bayad|hulog|bago|matapos|tulong|makakatulong|naiintindihan|"
        r"ipapaabot|ingat|oras|tawag|tawagan|balik|check|sandali)$")

    local = sum(1 for w in words if tagalog.match(w))
    return 1 - (local / len(words))
