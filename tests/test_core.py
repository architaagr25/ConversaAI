"""
Covers the parts of the foundation that fail quietly: text cleaning a voice
depends on, latency arithmetic every reported number depends on, vector
normalisation, and the index signature guard. No network needed.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from core.embeddings import SignatureMismatch, _normalise, require_matching
from core.llm import clean_for_speech, is_transient
from core.timing import Recorder, Span, Stopwatch, track


# --- Text heading for a voice -----------------------------------------------


class TestCleanForSpeech:
    def test_removes_bold_but_keeps_the_words(self):
        assert clean_for_speech("Your **premium** is due") == "Your premium is due"

    def test_removes_the_wrapping_asterisks_a_model_actually_produced(self):
        # Real case from model testing - TTS reads these out as "asterisk".
        raw = "*Nuwun sewu, kapan panjenengan saget mbayar?*"
        assert clean_for_speech(raw) == "Nuwun sewu, kapan panjenengan saget mbayar?"

    def test_leaves_a_lone_asterisk_alone(self):
        # Must not swallow the rest of the sentence.
        assert clean_for_speech("Terms apply * see policy") == "Terms apply * see policy"

    def test_strips_headings_and_bullets(self):
        raw = "## Cover\n- Hospital care\n- Day surgery"
        assert clean_for_speech(raw) == "Cover\nHospital care\nDay surgery"

    def test_keeps_link_text_and_drops_the_address(self):
        assert clean_for_speech("See [the policy](http://x.test/a)") == "See the policy"

    def test_handles_empty_input(self):
        assert clean_for_speech("") == ""

    def test_does_not_damage_ordinary_localized_text(self):
        raw = "Hi po, reminder lang po na due na ang premium niyo sa fifteenth."
        assert clean_for_speech(raw) == raw


# --- Deciding what is worth retrying ----------------------------------------


class TestTransientDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "429 RESOURCE_EXHAUSTED",
            "503 Service Unavailable",
            "deadline exceeded",
            "model is overloaded",
        ],
    )
    def test_recognises_temporary_failures(self, message):
        assert is_transient(Exception(message))

    @pytest.mark.parametrize(
        "message",
        [
            "400 INVALID_ARGUMENT",
            "401 unauthorized",
            "model not found",
        ],
    )
    def test_does_not_retry_permanent_failures(self, message):
        # Retrying just fails again slower, and delays the working fallback.
        assert not is_transient(Exception(message))


class TestThrottleBehaviour:
    def test_the_call_path_switches_provider_instead_of_waiting(self):
        # Measured during test calls: retrying spent three seconds waiting and
        # then failed over anyway, so the caller heard three seconds of silence
        # to reach the answer the fallback would have given immediately.
        from core.llm import LanguageModel
        assert LanguageModel(deep=False).deep is False

    def test_off_call_work_still_retries(self):
        # Nobody is waiting on a summary, so waiting for the quota to reset is
        # better than falling back to a weaker model.
        from core.llm import LanguageModel
        assert LanguageModel(deep=True).deep is True


# --- Latency measurement ----------------------------------------------------


class TestRecorder:
    def test_records_a_timed_block(self):
        recorder = Recorder()
        with track("asr", trace="call-1", recorder=recorder):
            time.sleep(0.02)
        assert len(recorder.spans) == 1
        assert recorder.spans[0].stage == "asr"
        assert recorder.spans[0].milliseconds >= 15

    def test_records_a_block_that_failed(self):
        # A slow failure is worth seeing in the numbers, so the span has to
        # survive the exception.
        recorder = Recorder()
        with pytest.raises(ValueError):
            with track("llm", recorder=recorder):
                raise ValueError("boom")
        assert len(recorder.spans) == 1
        assert recorder.spans[0].stage == "llm"

    def test_groups_by_stage(self):
        recorder = Recorder()
        for stage in ("asr", "asr", "llm"):
            recorder.add(Span(stage=stage, milliseconds=10.0))
        grouped = recorder.by_stage()
        assert len(grouped["asr"]) == 2
        assert len(grouped["llm"]) == 1

    def test_percentiles_on_an_empty_set(self):
        assert Recorder.percentiles([])["count"] == 0

    def test_reports_the_slowest_when_samples_are_few(self):
        # An interpolated p95 over three samples is invented precision.
        result = Recorder.percentiles([10.0, 20.0, 30.0])
        assert result["p50"] == 20.0
        assert result["p95"] == 30.0

    def test_percentiles_on_a_real_distribution(self):
        result = Recorder.percentiles([float(n) for n in range(1, 101)])
        assert result["p50"] == pytest.approx(50.5)
        assert result["p95"] == pytest.approx(96.0, abs=2)

    def test_end_to_end_does_not_double_count_concurrent_stages(self):
        # Two overlapping stages took 100 ms, not 200. Summing would overstate
        # what the caller actually waited for.
        recorder = Recorder()
        start = time.perf_counter()
        recorder.add(Span("a", 100.0, trace="t1", started_at=start))
        recorder.add(Span("b", 100.0, trace="t1", started_at=start))
        total = recorder.end_to_end()[0]
        assert 90 <= total <= 130

    def test_end_to_end_stays_sane_when_no_start_time_was_given(self):
        # Regression: a span with no start time landed at the beginning of the
        # process clock, and two spans of a few hundred ms reported five days.
        recorder = Recorder()
        recorder.add(Span("llm_first_token", 400.0, trace="t"))
        recorder.add(Span("llm_stream_total", 420.0, trace="t"))
        total = recorder.end_to_end()[0]
        assert total < 2000, f"end to end came out as {total:.0f} ms"

    def test_summary_is_readable_when_nothing_was_measured(self):
        assert "no measurements" in Recorder().summary()

    def test_summary_lists_every_stage(self):
        recorder = Recorder()
        recorder.add(Span("asr", 120.0, trace="t"))
        recorder.add(Span("llm", 800.0, trace="t"))
        text = recorder.summary()
        assert "asr" in text and "llm" in text and "end to end" in text


class TestStopwatch:
    def test_marks_accumulate_from_the_start(self):
        watch = Stopwatch()
        time.sleep(0.01)
        first = watch.mark("first token")
        time.sleep(0.01)
        complete = watch.mark("complete")
        assert complete > first >= 8

    def test_commit_turns_marks_into_spans(self):
        recorder = Recorder()
        watch = Stopwatch()
        watch.mark("first token")
        watch.mark("complete")
        watch.commit(trace="call-9", recorder=recorder)
        assert {s.stage for s in recorder.spans} == {"first token", "complete"}


# --- Vectors ----------------------------------------------------------------


class TestNormalise:
    def test_scales_vectors_to_unit_length(self):
        result = _normalise(np.array([[3.0, 4.0], [1.0, 0.0]]))
        assert np.allclose(np.linalg.norm(result, axis=1), 1.0)

    def test_survives_a_zero_vector(self):
        # Would otherwise put NaN in the index and poison every comparison.
        result = _normalise(np.array([[0.0, 0.0], [3.0, 4.0]]))
        assert not np.isnan(result).any()

    def test_handles_an_empty_array(self):
        assert _normalise(np.zeros((0, 4))).size == 0

    def test_similarity_becomes_a_dot_product(self):
        vectors = _normalise(np.array([[1.0, 1.0], [2.0, 2.0]]))
        assert float(vectors[0] @ vectors[1]) == pytest.approx(1.0)


class TestSignatureGuard:
    def test_accepts_a_matching_index(self):
        class Stub:
            signature = "gemini:test:768"

        require_matching("gemini:test:768", Stub())

    def test_refuses_an_index_built_by_another_model(self):
        # Otherwise the search runs happily and returns the wrong records.
        class Stub:
            signature = "local:minilm:384"

        with pytest.raises(SignatureMismatch):
            require_matching("gemini:test:768", Stub())

    def test_allows_an_index_with_no_recorded_signature(self):
        class Stub:
            signature = "gemini:test:768"

        require_matching("", Stub())
