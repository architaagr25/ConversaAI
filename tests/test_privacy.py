"""
Personal data tests.

Two failure directions matter and they pull against each other. Missing
something means personal data leaves the machine. Firing on everything means
policy text gets rewritten into tokens and the detector becomes noise nobody
acts on. Both are covered here.
"""

from __future__ import annotations

import pytest

from core.privacy import contains_personal_data, redact, scan, token


class TestDetection:
    @pytest.mark.parametrize("text,kind", [
        ("write to maria.santos@example.ph today", "EMAIL"),
        ("call +63 917 555 0142 tomorrow", "PHONE_PH"),
        ("reachable on 09175550388", "PHONE_PH"),
        ("call +62 812 5550 3311 tomorrow", "PHONE_ID"),
        ("his TIN 284-551-903 was given", "GOV_ID_PH"),
        ("KTP 3273051203880004 on file", "GOV_ID_ID"),
        ("policy HS-2026-88412 renewed", "ACCOUNT"),
        ("born 14/05/1991 in Manila", "DOB"),
    ])
    def test_each_identifier_type_is_found(self, text, kind):
        assert kind in {f.kind for f in scan(text)}

    def test_both_markets_addresses_are_found(self):
        ph = scan("lives at 142 Mabini Street, Quezon City")
        idn = scan("tinggal di Jl. Merdeka No. 45, Bandung")
        assert "ADDRESS" in {f.kind for f in ph}
        assert "ADDRESS" in {f.kind for f in idn}

    def test_an_address_is_taken_past_the_street(self):
        # Removing the street and leaving "Quezon City, Metro Manila" still
        # identifies someone once combined with anything else.
        findings = scan("at 142 Mabini Street, Quezon City, Metro Manila today")
        address = next(f for f in findings if f.kind == "ADDRESS")
        assert "Quezon City" in address.text

    def test_a_submission_date_is_not_a_birth_date(self):
        # These sit in adjacent columns of the export and were being redacted
        # together, which removes real data for no gain.
        assert not any(f.kind == "DOB" for f in scan("submitted 01/03/2026"))
        assert any(f.kind == "DOB" for f in scan("born 01/03/1991"))

    def test_a_name_is_found(self):
        assert "NAME" in {f.kind for f in scan("Maria Clara Santos called")}

    @pytest.mark.parametrize("phrase", [
        "Solara Health Shield", "Bonifacio Global City", "Pembiayaan Mobil Baru",
        "Critical Illness", "Kartu Keluarga",
    ])
    def test_company_and_product_names_are_not_people(self, phrase):
        assert "NAME" not in {f.kind for f in scan(phrase)}

    def test_policy_text_is_left_alone(self):
        text = ("A Waiting Period of twenty-four (24) months applies to any "
                "Pre-existing Condition declared at application.")
        assert scan(text, detect_names=False) == []

    def test_nothing_is_found_in_ordinary_text(self):
        assert scan("The office car park closes at eight.", detect_names=False) == []


class TestRedaction:
    def test_the_value_is_gone_and_the_sentence_survives(self):
        result, findings = redact("Email maria.santos@example.ph for details")
        assert "maria.santos@example.ph" not in result
        assert result.startswith("Email ") and result.endswith(" for details")
        assert len(findings) == 1

    def test_the_same_value_always_becomes_the_same_token(self):
        first, _ = redact("call +63 917 555 0142")
        second, _ = redact("also +63 917 555 0142 please")
        assert first.split()[-1] == second.split()[-2]

    def test_different_values_get_different_tokens(self):
        assert token("EMAIL", "a@x.test") != token("EMAIL", "b@x.test")

    def test_the_token_does_not_carry_the_original(self):
        result, _ = redact("TIN 284-551-903 on file")
        assert "284" not in result and "551" not in result

    def test_several_kinds_in_one_line(self):
        line = ("Maria Clara Santos, maria.santos@example.ph, +63 917 555 0142, "
                "TIN 284-551-903, policy HS-2026-88412")
        result, findings = redact(line)
        assert {f.kind for f in findings} >= {
            "NAME", "EMAIL", "PHONE_PH", "GOV_ID_PH", "ACCOUNT"}
        for leak in ("Maria", "example.ph", "0142", "284-551", "88412"):
            assert leak not in result

    def test_overlapping_matches_are_claimed_once(self):
        result, findings = redact("Contact TIN 284-551-903 now")
        assert result.count("[") == len(findings)

    def test_empty_input(self):
        assert redact("") == ("", [])


class TestOutboundGuard:
    def test_a_transcript_with_a_phone_number_is_flagged(self):
        assert contains_personal_data("my number is +63 917 555 0142")

    def test_policy_text_is_not_flagged(self):
        assert not contains_personal_data(
            "Illnesses are covered after 30 days from the commencement date.")

    def test_names_do_not_trip_the_guard_by_default(self):
        # The name detector is cautious but not precise, and a false positive
        # here would interrupt a live call.
        assert not contains_personal_data("Maria Clara Santos called today")
        assert contains_personal_data("Maria Clara Santos called today",
                                      detect_names=True)
