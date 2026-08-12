"""Tests for escalations.summary — redact() and build_summary()."""

from escalations.summary import build_summary, redact


def test_card_number_is_masked_to_last_four():
    text = "My card number is 4111 1111 1111 1111, please charge it."
    result = redact(text)

    assert "4111 1111 1111 1111" not in result
    assert "•••• 1111" in result


def test_otp_is_removed():
    text = "Sure, the otp is 483920, use that to verify me."
    result = redact(text)

    assert "483920" not in result
    assert "[redacted]" in result


def test_normal_message_passes_through_unchanged():
    text = "The website shows the wrong shipping estimate for my order."
    result = redact(text)

    assert result == text


def test_build_summary_redacts_free_text_fields():
    summary = build_summary(
        reason_code="cannot_resolve",
        what_happened="Caller's password is hunter2 and it isn't working.",
        checked=["Looked up account, card ending 4111111111111111 on file."],
        caller="Card number 4111 1111 1111 1111",
        follow_up_method="call me back, my pin is 4471",
    )

    assert "hunter2" not in summary["what_happened"]
    assert "[redacted]" in summary["what_happened"]
    assert "4111111111111111" not in summary["checked"][0]
    assert "•••• 1111" in summary["checked"][0]
    assert "4111 1111 1111 1111" not in summary["caller"]
    assert "4471" not in summary["follow_up_method"]
    assert "[redacted]" in summary["follow_up_method"]


def test_build_summary_has_no_transcript_field():
    summary = build_summary(
        reason_code="needs_human_decision",
        what_happened="Caller wants a refund outside policy window.",
    )

    assert set(summary.keys()) == {
        "reason_code",
        "reason_label",
        "urgency",
        "caller",
        "what_happened",
        "checked",
        "language",
        "follow_up_method",
    }
    assert "transcript" not in summary


def test_build_summary_uses_default_urgency_from_config():
    summary = build_summary(
        reason_code="needs_human_decision",
        what_happened="Needs a manager to approve an exception.",
    )

    assert summary["urgency"] == "medium"
    assert summary["reason_label"] == (
        "The caller needs a decision the agent isn't allowed to make"
    )
