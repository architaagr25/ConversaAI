"""The unit of extracted content, shared by every source type."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Section:
    """One heading and the content beneath it, with enough detail to cite it.

    source_ref is what a caller hears when the agent says where an answer came
    from, so it names the exact section rather than the whole document.
    """

    source_type: str
    source_ref: str
    source_origin: str
    title: str
    content: str
    heading_level: int
    language: str
    retrieved_at: str
    extraction_method: str
    char_count: int = 0
    quality_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.char_count = len(self.content)


@dataclass
class Record:
    """A section after classification and cleaning, ready to be indexed.

    Everything the agent needs to decide whether it may use this text, and to
    say where it came from, travels with the text itself.
    """

    record_id: str
    title: str
    content: str

    category: str
    business_unit: str
    authority: str

    source_type: str
    source_ref: str
    source_origin: str
    source_retrieved_at: str

    version: str = "1.0"
    content_hash: str = ""
    language: str = "en"

    pii: bool = False
    pii_types: list[str] = field(default_factory=list)

    terminology_variants: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    duplicate_of: str = ""
    quality_flags: list[str] = field(default_factory=list)

    char_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.content)


# Ranked so a conflict can be settled by which source outranks which.
AUTHORITY_RANK = {"binding": 1, "operational": 2, "published": 3, "promotional": 4}
