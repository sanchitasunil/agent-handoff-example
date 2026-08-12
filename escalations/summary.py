"""
escalations/summary.py — two jobs, both about the human who picks up the handoff:

1. build_summary(...) — assembles the SHORT, human-facing summary that gets
   handed to a person: who needs help, what happened, what the agent
   already checked, how urgent it is, the caller's language, and their
   preferred way to be followed up with. It never includes the full call
   transcript — a human triaging escalations needs the gist in a few
   seconds, not a wall of text to re-read.

2. redact(text) — strips or masks sensitive data (passwords, OTPs, PINs,
   account/card numbers) out of any free text BEFORE it is stored or sent
   anywhere. This matters because escalation summaries often get written
   into tickets, dashboards, or logs with much wider access than the
   original call — a password or full card number that leaks into one of
   those is a real security/compliance incident, not just a UX nit. We
   redact defensively, even if the agent "shouldn't" have collected that
   data in the first place, because callers say things unprompted.

build_summary() always runs every free-text field through redact() before
returning, so nothing sensitive can slip into a stored summary just
because a caller volunteered it mid-conversation.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Optional, Union

from escalations.config import ESCALATION_REASONS

# ---------------------------------------------------------------------------
# Redaction rules
# ---------------------------------------------------------------------------
# Each rule is (name, compiled_pattern, replacement) — the name is just a
# label for anyone reading/debugging the list; the comment above each rule
# explains what kind of data it targets. `replacement` is either a plain
# string (used as-is by re.sub) or a callable that takes the regex Match
# and returns the replacement text (for rules that need to keep part of
# the match, like the last 4 digits of a card number).
#
# Add new rules by appending to REDACTION_RULES — nothing else needs to
# change; redact() just walks the list in order.

def _mask_digits(match: "re.Match[str]") -> str:
    """Keep only the last 4 digits of a long digit run, mask the rest."""
    digits = re.sub(r"\D", "", match.group(0))
    last4 = digits[-4:]
    return f"•••• {last4}"


RedactionRule = tuple[str, "re.Pattern[str]", Union[str, Callable[["re.Match[str]"], str]]]

REDACTION_RULES: list[RedactionRule] = [
    (
        "labeled_secret",
        # Targets: passwords, OTPs (one-time codes), and PINs that are
        # explicitly labeled in the text, e.g. "password: hunter2",
        # "otp is 483920", "my pin 4471". Matches the label plus the token
        # right after it and drops the value entirely — these should never
        # be partially visible, unlike an account number.
        re.compile(
            r"(?i)\b(password|passcode|otp|one[- ]time (?:code|password)|pin)\b"
            r"\s*(?:is|was|[:=])?\s*[\"']?[\w-]+[\"']?"
        ),
        "[redacted]",
    ),
    (
        "long_digit_sequence",
        # Targets: account numbers, card numbers, and similar long numeric
        # identifiers — 8 or more digits, optionally grouped with spaces
        # or dashes (e.g. "4111 1111 1111 1111", "12345678-90"). Masked to
        # the last 4 digits so a human can still cross-reference "the card
        # ending in 4321" without the full number ever being stored.
        re.compile(r"\b(?:\d[ -]?){8,}\d\b"),
        _mask_digits,
    ),
]


def redact(text: Optional[str]) -> Optional[str]:
    """Mask or strip sensitive data out of free text.

    Applies every rule in REDACTION_RULES, in order, and returns the
    resulting text. Safe to call on None or empty strings (returns them
    unchanged). Text with nothing sensitive in it passes through byte-for-
    byte identical.
    """
    if not text:
        return text

    redacted = text
    for _name, pattern, replacement in REDACTION_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _label_for(reason_code: str) -> str:
    for reason in ESCALATION_REASONS:
        if reason["code"] == reason_code:
            return reason["label"]
    return reason_code


def _default_urgency_for(reason_code: str) -> str:
    for reason in ESCALATION_REASONS:
        if reason["code"] == reason_code:
            return reason["default_urgency"]
    return "low"


def build_summary(
    reason_code: str,
    what_happened: str,
    checked: Optional[Iterable[str]] = None,
    urgency: Optional[str] = None,
    language: Optional[str] = None,
    follow_up_method: Optional[str] = None,
    caller: Optional[str] = None,
) -> dict:
    """Build the short, human-facing escalation summary.

    Args:
        reason_code: One of the `code` values in
            escalations.config.ESCALATION_REASONS — identifies WHY this is
            being escalated.
        what_happened: A brief, plain-language description of the request
            or situation. Free text — run through redact().
        checked: What the agent already tried or looked at, so the human
            doesn't repeat that work (e.g. ["checked FAQ", "verified account
            in CRM"]). Each item is free text — run through redact().
        urgency: "low" | "medium" | "high" | "emergency". Defaults to the
            reason's `default_urgency` from config.py when not given.
        language: The caller's spoken/preferred language (e.g. "es",
            "English"), so the human who follows up can plan for it.
        follow_up_method: How the caller wants to be reached back
            (e.g. "callback", "email", "text"). Free text — run through
            redact() in case a caller volunteers contact details here.
        caller: Who needs help — a name, caller ID, or session
            identifier. Free text — run through redact().

    Returns:
        A dict with exactly these keys, ready to pass into store.create():
        reason_code, reason_label, urgency, caller, what_happened, checked,
        language, follow_up_method. No transcript, no raw unredacted text.
    """
    checked_list = list(checked) if checked else []

    return {
        "reason_code": reason_code,
        "reason_label": _label_for(reason_code),
        "urgency": urgency or _default_urgency_for(reason_code),
        "caller": redact(caller),
        "what_happened": redact(what_happened),
        "checked": [redact(item) for item in checked_list],
        "language": language,
        "follow_up_method": redact(follow_up_method),
    }
