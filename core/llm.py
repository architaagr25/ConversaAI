"""
Model access, with retry, failover and output cleaning.

Free tiers throttle, so requests retry and then fall back to a second provider -
a rate limit mid-call should cost a pause, not the call. Replies get markdown
stripped before they reach speech synthesis, since one model wrapped a whole
sentence in asterisks and TTS reads those aloud.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from core.config import settings
from core.timing import RECORDER, Span, Stopwatch, track, track_async

log = logging.getLogger(__name__)

# Signals a request failed for a reason worth retrying: throttling, or the
# service having a bad moment. A malformed request is not retried, because
# sending it again will fail the same way.
_TRANSIENT = ("429", "resource_exhausted", "500", "502", "503", "504",
              "unavailable", "deadline", "timeout", "overloaded")


def is_transient(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT)


# --- Text cleaning -----------------------------------------------------------

_MARKDOWN_PATTERNS = [
    (re.compile(r"```.*?```", re.DOTALL), " "),      # fenced code
    (re.compile(r"`([^`]*)`"), r"\1"),               # inline code
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),  # bold
    (re.compile(r"(?<!\w)\*(.+?)\*(?!\w)", re.DOTALL), r"\1"),  # italic
    (re.compile(r"(?<!\w)_(.+?)_(?!\w)", re.DOTALL), r"\1"),    # underscore italic
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),       # headings
    (re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE), ""),        # bullets
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),   # links, keep the words
]


def clean_for_speech(text: str) -> str:
    """Strip formatting that speech synthesis would read aloud.

    Single asterisks and underscores only go when they wrap a phrase, so a stray
    one doesn't swallow the rest of the sentence.
    """
    if not text:
        return ""
    cleaned = text
    for pattern, replacement in _MARKDOWN_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    # Collapse the whitespace the substitutions leave behind.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return cleaned.strip()


# --- Requests ----------------------------------------------------------------


@dataclass(slots=True)
class Reply:
    """A model response, with enough context to explain where it came from."""

    text: str
    provider: str
    model: str
    milliseconds: float
    fell_back: bool = False

    @property
    def spoken(self) -> str:
        return clean_for_speech(self.text)


class LanguageModel:
    """Primary provider with automatic failover to a second one."""

    def __init__(self, deep: bool = False) -> None:
        # "Deep" means off the call path, where a slower and more careful answer
        # is affordable: end of call summaries, batch analysis.
        self.deep = deep
        self.model = settings.gemini_deep_model if deep else settings.gemini_model
        self._gemini = None
        self._groq = None

    # -- clients, created on first use so importing this module is cheap ------

    @property
    def gemini(self):
        if self._gemini is None:
            from google import genai

            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            self._gemini = genai.Client(api_key=settings.gemini_api_key)
        return self._gemini

    @property
    def groq(self):
        if self._groq is None:
            from groq import Groq

            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY is not set")
            self._groq = Groq(api_key=settings.groq_api_key)
        return self._groq

    def warmup(self) -> float:
        """Build the client and open the connection before anyone is waiting.

        First request of a process measured ~3s against ~1s for the rest, the
        difference being client construction and the TLS handshake. Call this
        while the line is still connecting. Returns milliseconds taken.
        """
        watch = Stopwatch()
        try:
            self.gemini.models.generate_content(
                model=self.model,
                contents="hi",
                config=self._config(None, 0.0, 4),
            )
        except Exception as exc:
            log.warning("warm-up failed, the first turn will be slower",
                        extra={"reason": str(exc)[:120]})
        return watch.elapsed_ms

    def _config(self, system: str | None, temperature: float,
                max_tokens: int | None, thinking_budget: int | None = None):
        from google.genai import types

        thinking = None
        budget = settings.gemini_thinking_budget if thinking_budget is None \
            else thinking_budget
        # A negative budget means "leave the model on its own default", which is
        # the only way to get deliberation back without editing code.
        #
        # Worth knowing: deliberation is charged against max_output_tokens. A
        # short answer limit with thinking left on produced fragments of
        # reasoning instead of an answer, which looks like the model failing
        # rather than the budget being wrong.
        if budget >= 0 and (thinking_budget is not None or not self.deep):
            thinking = types.ThinkingConfig(thinking_budget=budget)

        return types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            thinking_config=thinking,
        )

    # -- synchronous ---------------------------------------------------------

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = 600,
        attempts: int = 3,
        trace: str = "",
        thinking_budget: int | None = None,
    ) -> Reply:
        """Ask for a complete answer, retrying and then failing over."""
        delay = 1.0
        last: Exception | None = None

        for attempt in range(attempts):
            try:
                with track("llm", trace=trace, detail=self.model) as span:
                    response = self.gemini.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=self._config(system, temperature, max_tokens,
                                            thinking_budget),
                    )
                return Reply(
                    text=(response.text or "").strip(),
                    provider="gemini",
                    model=self.model,
                    milliseconds=span.milliseconds,
                )
            except Exception as exc:
                last = exc
                if not is_transient(exc) or attempt == attempts - 1:
                    break
                log.warning(
                    "model request throttled, retrying",
                    extra={"attempt": attempt + 1, "wait_s": delay},
                )
                time.sleep(delay)
                delay *= 2

        log.warning(
            "primary model unavailable, using fallback",
            extra={"reason": str(last)[:120], "fallback": settings.groq_llm_model},
        )
        return self._fallback(prompt, system, temperature, max_tokens, trace)

    def _fallback(
        self,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int | None,
        trace: str,
    ) -> Reply:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        with track("llm_fallback", trace=trace, detail=settings.groq_llm_model) as span:
            completion = self.groq.chat.completions.create(
                model=settings.groq_llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or 600,
            )
        return Reply(
            text=(completion.choices[0].message.content or "").strip(),
            provider="groq",
            model=settings.groq_llm_model,
            milliseconds=span.milliseconds,
            fell_back=True,
        )

    def stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = 600,
        trace: str = "",
    ) -> Iterator[str]:
        """Yield the answer in pieces so speech can start on the first sentence."""
        first = True
        watch = Stopwatch()
        try:
            stream = self.gemini.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config=self._config(system, temperature, max_tokens),
            )
            for chunk in stream:
                piece = chunk.text or ""
                if not piece:
                    continue
                if first:
                    # Tracked separately from the total: this is what the caller
                    # hears as silence. The rest arrives while speech is playing.
                    elapsed = watch.mark("llm_first_token")
                    RECORDER.add(
                        Span("llm_first_token", elapsed, trace=trace,
                             started_at=time.perf_counter() - elapsed / 1000,
                             detail=self.model)
                    )
                    log.debug("first token received",
                              extra={"trace": trace, "ms": round(elapsed)})
                    first = False
                yield piece

            total = watch.elapsed_ms
            RECORDER.add(
                Span("llm_stream_total", total, trace=trace,
                     started_at=time.perf_counter() - total / 1000,
                     detail=self.model)
            )
        except Exception as exc:
            if first:
                # Nothing sent yet, so the fallback is invisible to the caller.
                log.warning("stream failed before output, falling back",
                            extra={"reason": str(exc)[:120]})
                yield self._fallback(prompt, system, temperature, max_tokens, trace).text
            else:
                # Mid-sentence. Retrying would repeat what was already spoken.
                log.error("stream failed part way through", extra={"reason": str(exc)[:120]})

    # -- asynchronous, for the call loop -------------------------------------

    async def agenerate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = 600,
        attempts: int = 3,
        trace: str = "",
    ) -> Reply:
        import asyncio

        delay = 1.0
        last: Exception | None = None

        for attempt in range(attempts):
            try:
                async with track_async("llm", trace=trace, detail=self.model) as span:
                    response = await self.gemini.aio.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=self._config(system, temperature, max_tokens),
                    )
                return Reply(
                    text=(response.text or "").strip(),
                    provider="gemini",
                    model=self.model,
                    milliseconds=span.milliseconds,
                )
            except Exception as exc:
                last = exc
                if not is_transient(exc) or attempt == attempts - 1:
                    break
                await asyncio.sleep(delay)
                delay *= 2

        log.warning("primary model unavailable, using fallback",
                    extra={"reason": str(last)[:120]})
        return await asyncio.to_thread(
            self._fallback, prompt, system, temperature, max_tokens, trace
        )

    async def astream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = 600,
        trace: str = "",
    ) -> AsyncIterator[str]:
        first = True
        watch = Stopwatch()
        try:
            stream = await self.gemini.aio.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config=self._config(system, temperature, max_tokens),
            )
            async for chunk in stream:
                piece = chunk.text or ""
                if not piece:
                    continue
                if first:
                    elapsed = watch.mark("llm_first_token")
                    RECORDER.add(
                        Span("llm_first_token", elapsed, trace=trace,
                             started_at=time.perf_counter() - elapsed / 1000,
                             detail=self.model)
                    )
                    first = False
                yield piece

            total = watch.elapsed_ms
            RECORDER.add(
                Span("llm_stream_total", total, trace=trace,
                     started_at=time.perf_counter() - total / 1000,
                     detail=self.model)
            )
        except Exception as exc:
            log.error("async stream failed",
                      extra={"reason": str(exc)[:120], "trace": trace})


# Ready-made instances for the two situations that come up everywhere.
live = LanguageModel(deep=False)
offline = LanguageModel(deep=True)
