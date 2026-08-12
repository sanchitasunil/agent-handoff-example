"""
escalations/config.py — the ONLY file you should need to edit per track.

This repo is meant to be forked once per use case (health hotline, disaster
response, customer support, tutoring, ...). Everything about *when* the
agent hands a caller off to a human — the list of reasons, their default
urgency, and whether an emergency notice gets spoken first — lives here as
plain data. The rest of the codebase (escalations/summary.py, the agent's
tool-calling logic, etc.) reads from this file and should not need to
change when you adapt the agent to a new track.

If you're forking this repo for a new track: edit this file, and probably
only this file.
"""

# ---------------------------------------------------------------------------
# Example swaps per track
# ---------------------------------------------------------------------------
# ESCALATION_REASONS below ships with two generic placeholders so the agent
# runs out of the box. Replace or extend them with reasons specific to your
# track. A few worked examples:
#
#   Finance / trading track (missing or stale market data):
#     {
#         "code": "missing_market_data",
#         "label": "Market data is missing or out of date for this request",
#         "default_urgency": "medium",
#     }
#
#   Health track (red-flag symptom the agent isn't qualified to triage):
#     {
#         "code": "red_flag_symptom",
#         "label": "Caller described a symptom that needs clinical judgment",
#         "default_urgency": "high",
#     }
#
#   Support / billing track (payment or refund dispute):
#     {
#         "code": "payment_dispute",
#         "label": "Caller is disputing a charge or refund",
#         "default_urgency": "medium",
#     }
#
#   Health / disaster track (caller may be in danger):
#     {
#         "code": "caller_in_danger",
#         "label": "Caller may be in immediate physical danger",
#         "default_urgency": "emergency",
#     }
#
#   Tutoring / education track (learner is upset or distressed):
#     {
#         "code": "learner_upset",
#         "label": "Learner is upset or distressed and needs a person",
#         "default_urgency": "medium",
#     }
# ---------------------------------------------------------------------------

ESCALATION_REASONS = [
    {
        # snake_case id — referenced in code, logs, and stored records.
        # Keep it stable once a track ships; renaming it breaks history.
        "code": "cannot_resolve",
        # Short human-readable text shown to the human who picks up the
        # handoff (dashboard, ticket, page, etc.).
        "label": "The agent tried and could not answer or resolve the request",
        # Urgency assumed when the agent doesn't (or can't) pick one itself.
        # One of: "low", "medium", "high", "emergency".
        "default_urgency": "low",
    },
    {
        "code": "needs_human_decision",
        "label": "The caller needs a decision the agent isn't allowed to make",
        "default_urgency": "medium",
    },
]

# ---------------------------------------------------------------------------
# High-risk mode
# ---------------------------------------------------------------------------
# When True, the agent speaks EMERGENCY_NOTICE (below) before escalating.
# Turn this on for tracks where a caller's situation could be time-critical
# (health, disaster response, crisis lines). Leave it False for tracks
# where an emergency framing would be confusing or alarming (e.g. a coding
# tutor or a billing bot).
HIGH_RISK_MODE = False

# The line the agent speaks before escalating when HIGH_RISK_MODE is True.
# Deliberately generic and track-agnostic — it does not claim to BE
# emergency services, it points the caller to real ones. Override this per
# track if you have a more specific local number or resource to point to.
EMERGENCY_NOTICE = (
    "If you are in immediate danger, please contact your local emergency "
    "number or someone you trust nearby right now."
)
