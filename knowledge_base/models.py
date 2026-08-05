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
