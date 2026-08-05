"""
Shared by the knowledge base, the voice agents and the live call analysis.

Nothing here is specific to one part. Keeping config, logging, timing, model
access and embeddings in one place means all four behave the same way when a
service is slow or throttled.
"""

from core.config import PROJECT_ROOT, settings
from core.embeddings import Embedder, SignatureMismatch, get_embedder, require_matching
from core.llm import LanguageModel, Reply, clean_for_speech, live, offline
from core.logging_setup import setup_logging
from core.privacy import Finding, contains_personal_data, redact, scan
from core.timing import RECORDER, Recorder, Span, Stopwatch, track, track_async

__all__ = [
    "PROJECT_ROOT",
    "settings",
    "setup_logging",
    "RECORDER",
    "Recorder",
    "Span",
    "Stopwatch",
    "track",
    "track_async",
    "LanguageModel",
    "Reply",
    "clean_for_speech",
    "live",
    "offline",
    "Embedder",
    "SignatureMismatch",
    "get_embedder",
    "require_matching",
    "Finding",
    "contains_personal_data",
    "redact",
    "scan",
]
