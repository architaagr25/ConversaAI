"""
Shared foundation used by the knowledge base, the voice agents and the live
call analysis.

Nothing here is specific to one part of the system. Configuration, logging,
latency measurement, model access and embeddings live here so that all four
parts behave the same way when a service is slow, throttled or unavailable.
"""

from core.config import PROJECT_ROOT, settings
from core.embeddings import Embedder, SignatureMismatch, get_embedder, require_matching
from core.llm import LanguageModel, Reply, clean_for_speech, live, offline
from core.logging_setup import setup_logging
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
]
