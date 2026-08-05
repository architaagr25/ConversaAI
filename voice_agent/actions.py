"""
What happens after the call: a lead, an eligibility decision, a summary and,
where it applies, a handover.

Two copies of every lead are written. The working copy keeps the contact
details, because an adviser cannot ring somebody back without them. The
evidence copy has them removed, and that is the one that can be shared, shown
in a demo or committed.

The summary is written by a model, and the model never sees the contact
details. Redaction happens before the transcript is sent, not after the
summary comes back, because a summary that has to be cleaned up afterwards has
already left the machine intact.

The eligibility decision is not written by the model at all. It comes from the
rules engine, and the summary is told what was decided rather than asked to
work it out.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, settings
from core.llm import clean_for_speech, offline
from core.privacy import redact

log = logging.getLogger(__name__)

WORKING = PROJECT_ROOT / "data" / "leads"
EVIDENCE = PROJECT_ROOT / "results" / "leads"
WEBHOOKS = PROJECT_ROOT / "results" / "webhooks"

# Kept out of the evidence copy and never sent anywhere.
CONTACT_FIELDS = ("full_name", "contact_number", "email")


@dataclass
class Lead:
    lead_id: str
    created_at: str
    business_unit: str
    pack_id: str

    collected: dict = field(default_factory=dict)
    corrections: list[str] = field(default_factory=list)

    eligible: bool | None = None
    decided: bool = False
    decline_reason: str = ""
    decided_by_rule: str = ""
    requirements: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    escalated_to: str = ""
    unanswered: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    summary: str = ""
    next_action: str = ""
    turns: int = 0

    @property
    def outcome(self) -> str:
        if self.escalated_to:
            return "escalated"
        if not self.decided:
            return "incomplete"
        return "qualified" if self.eligible else "declined"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_lead(lead: Lead) -> dict:
    """The shareable copy, with the person taken out of it.

    Age, income and city stay. They are what the qualification rules run on,
    and without them the record says nothing useful about why a decision went
    the way it did.
    """
    data = asdict(lead)
    collected = dict(data["collected"])

    for name in CONTACT_FIELDS:
        if name in collected:
            collected[name] = "[redacted]"

    data["collected"] = collected
    data["summary"] = redact(data["summary"], detect_names=True)[0]
    data["contact_held"] = any(
        name in lead.collected for name in CONTACT_FIELDS)
    return data


def build_summary(transcript: str, lead: Lead) -> str:
    """A short note for whoever picks this up next.

    The transcript is redacted first, including names, because this is the one
    place in the call flow where a whole conversation is sent away at once.
    The outcome is stated rather than asked for, since it was already decided
    by the rules.
    """
    safe, findings = redact(transcript, detect_names=True)
    if findings:
        log.info("removed personal data before summarising",
                 extra={"count": len(findings),
                        "kinds": sorted({f.kind for f in findings})})

    outcome = {
        "qualified": "The caller meets the criteria on the information given.",
        "declined": f"The caller does not qualify. {lead.decline_reason}",
        "escalated": f"The call was handed to a person, trigger {lead.escalated_to}.",
        "incomplete": f"Not enough was collected to decide. Missing: "
                      f"{', '.join(lead.missing) or 'unknown'}.",
    }[lead.outcome]

    prompt = (
        "Write a handover note for the adviser who picks this lead up.\n\n"
        "Three or four sentences, no bullet points, no headings. Say what the "
        "caller wants, what was established, and what to do next. Plain "
        "language, no sales tone.\n\n"
        f"The eligibility decision has already been made: {outcome}\n"
        "Do not re-decide it and do not soften it.\n\n"
        f"Anything the agent could not answer: "
        f"{'; '.join(lead.unanswered) or 'nothing'}\n\n"
        "Names and numbers have been removed from the transcript. Write around "
        "the placeholders rather than inventing details to fill them.\n\n"
        f"Transcript:\n{safe}"
    )

    # Deliberation off, and a generous token limit. A handover note needs no
    # reasoning, and thinking tokens count against the output budget: with it
    # left on and the limit set for a short note, what came back was fragments
    # of reasoning rather than a note.
    reply = offline.generate(prompt, temperature=0.3, max_tokens=500,
                             thinking_budget=0)
    # It is a written note, but markdown in a CRM field is noise.
    return clean_for_speech(reply.text).strip()


def next_action(lead: Lead) -> str:
    """What should happen, decided here rather than by the model."""
    if lead.escalated_to:
        return "Handed to a person during the call. No callback needed."
    if not lead.decided:
        missing = ", ".join(lead.missing) or "the remaining questions"
        return f"Call back to complete qualification. Still needed: {missing}."
    if not lead.eligible:
        return ("Do not quote. Send the declined-applicant letter and offer "
                "the alternatives.")
    if lead.requirements:
        readable = ", ".join(r.replace("_", " ") for r in lead.requirements)
        return f"Quote from the rate table, then arrange: {readable}."
    return "Quote from the rate table and call back within one working day."


def create_lead(session, transcript: str = "", write_summary: bool = True) -> Lead:
    """Turn a finished call into a record somebody can act on."""
    conversation = session.agent.conversation
    assessment = conversation.assessment

    lead = Lead(
        lead_id=f"LD-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}",
        created_at=_now(),
        business_unit=conversation.business_unit,
        pack_id=conversation.pack_id,
        collected=dict(conversation.slots),
        corrections=list(conversation.corrections),
        escalated_to=conversation.escalated_to,
        unanswered=list(conversation.unanswered),
        citations=list(conversation.citations),
        turns=len(conversation.turns),
    )

    if assessment:
        lead.eligible = assessment.eligible
        lead.decided = assessment.decided
        lead.decline_reason = assessment.decline_reason
        lead.requirements = list(assessment.requirements)
        lead.missing = list(assessment.missing)
        if assessment.blocking:
            lead.decided_by_rule = assessment.blocking[0].rule_id

    lead.next_action = next_action(lead)

    if write_summary and transcript.strip():
        try:
            lead.summary = build_summary(transcript, lead)
        except Exception as exc:
            # A summary is useful, not essential. Losing the lead because the
            # model was throttled would be the wrong trade.
            log.warning("could not write the summary",
                        extra={"reason": str(exc)[:120]})
            lead.summary = "(summary unavailable)"

    return lead


def save_lead(lead: Lead) -> tuple[Path, Path]:
    """Write both copies and return where they went."""
    WORKING.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    working = WORKING / f"{lead.lead_id}.json"
    working.write_text(json.dumps(asdict(lead), indent=2, ensure_ascii=False),
                       encoding="utf-8")

    evidence = EVIDENCE / f"{lead.lead_id}.json"
    evidence.write_text(json.dumps(redact_lead(lead), indent=2, ensure_ascii=False),
                        encoding="utf-8")

    log.info("lead recorded", extra={"lead": lead.lead_id, "outcome": lead.outcome})
    return working, evidence


def send_escalation(lead: Lead) -> dict:
    """Hand the call on.

    With no webhook configured the payload is written to disk instead of being
    dropped. A handover that silently goes nowhere is worse than one that
    obviously did not happen.
    """
    payload = {
        "event": "escalation",
        "lead_id": lead.lead_id,
        "at": _now(),
        "trigger": lead.escalated_to,
        "business_unit": lead.business_unit,
        "collected": redact_lead(lead)["collected"],
        "unanswered": lead.unanswered,
        "summary": redact(lead.summary, detect_names=True)[0],
        "next_action": lead.next_action,
    }

    url = settings.escalation_webhook_url.strip()
    if not url:
        WEBHOOKS.mkdir(parents=True, exist_ok=True)
        path = WEBHOOKS / f"{lead.lead_id}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        log.info("no webhook configured, payload written to disk",
                 extra={"path": str(path)})
        return {"delivered": False, "written_to": str(path), "payload": payload}

    import httpx

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=payload)
        delivered = 200 <= response.status_code < 300
        log.info("escalation posted",
                 extra={"status": response.status_code, "delivered": delivered})
        return {"delivered": delivered, "status": response.status_code,
                "payload": payload}
    except Exception as exc:
        log.error("escalation webhook failed", extra={"reason": str(exc)[:120]})
        return {"delivered": False, "error": str(exc)[:160], "payload": payload}


def finish_call(session, transcript: str = "") -> dict:
    """Everything that happens once the caller has hung up."""
    lead = create_lead(session, transcript)
    working, evidence = save_lead(lead)

    result = {
        "lead_id": lead.lead_id,
        "outcome": lead.outcome,
        "next_action": lead.next_action,
        "working_copy": str(working),
        "evidence_copy": str(evidence),
    }

    if lead.escalated_to:
        result["escalation"] = send_escalation(lead)

    return result


def contact_of(text: str) -> dict:
    """Pull a name or a number out of what the caller said.

    Deliberately narrow. A caller saying "my name is" or reading a phone
    number is the reliable case; guessing at a name from ordinary speech is
    not, and a wrong name on a lead is worse than none.
    """
    found: dict = {}

    name = re.search(
        r"\b(?:my name is|this is|it'?s|i am|i'm)\s+"
        r"([A-Z][a-z']+(?:\s+[A-Z][a-z']+){0,2})\b", text)
    if name:
        found["full_name"] = name.group(1).strip()

    number = re.search(r"(\+?\d[\d\s\-]{8,15}\d)", text)
    if number:
        digits = re.sub(r"\D", "", number.group(1))
        if 9 <= len(digits) <= 15:
            found["contact_number"] = number.group(1).strip()

    return found
