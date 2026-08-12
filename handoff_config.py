"""Specialist roster for in-session handoff.

Adapting this repo to a new domain should mean editing this file and nothing
else. The triage agent reads `when_to_use` to decide where to route, and the
specialist agents in specialists.py are generated from these entries at import
time, so adding a specialist here is enough to make it real.
"""

# Swap the two placeholders below for your own domain. Some shapes that work:
#
# Bank / fintech:
#   cards      "Lost or stolen cards, disputed charges, limits"
#   lending    "Loan applications, payoff quotes, rate questions"
#
# Health line (see the safety note in README before shipping one of these):
#   scheduling "Booking, moving, or cancelling an appointment"
#   pharmacy   "Refills, dosage timing, interactions between prescriptions"
#
# Retail:
#   orders     "Where is my order, delivery dates, returns"
#   sizing     "Fit, measurements, product comparisons"
#
# Internal IT desk:
#   access     "Password resets, MFA, permissions, locked accounts"
#   hardware   "Laptops, monitors, peripherals, RMA"
#
# Keep `when_to_use` concrete and mutually exclusive. It goes into the triage
# agent's prompt verbatim, so vague descriptions ("general help") produce vague
# routing, and two entries that overlap will route inconsistently.

SPECIALISTS = [
    {
        # Stable id used in code, logs, and the handoff chain. Renaming it
        # invalidates historical logs, so pick one you can live with.
        "name": "accounts",
        # What the triage agent calls this team out loud.
        "display_name": "the accounts team",
        # The routing rule. Triage matches the caller's request against this.
        "when_to_use": (
            "Billing, payments, refunds, subscription changes, login and "
            "password problems, or anything about the caller's account details."
        ),
        # Appended to the specialist's system prompt.
        "persona": (
            "You handle account and billing questions. You are precise about "
            "money and careful about identity: never read back full card or "
            "account numbers, and never ask for a password or one-time code."
        ),
        # Spoken on arrival, before the specialist starts working. Keep it to
        # one sentence; build_handoff_summary supplies the context around it.
        "greeting": "Hi, you're through to accounts.",
    },
    {
        "name": "technical",
        "display_name": "technical support",
        "when_to_use": (
            "Something is broken or not working: errors, crashes, setup and "
            "installation problems, connectivity, or unexpected behaviour in "
            "the product."
        ),
        "persona": (
            "You handle technical troubleshooting. Work one step at a time and "
            "confirm the result of each step before moving to the next, since "
            "the caller is following along by ear."
        ),
        "greeting": "Hi, technical support here.",
    },
]

# Ceiling on agent switches per session, counting transfers to a specialist and
# hand-backs to triage alike. Without a cap, a caller whose problem spans two
# teams can bounce between them indefinitely — each agent sees a request that
# looks like someone else's job and routes it onward. Three is enough for
# triage -> specialist -> triage -> a second specialist, which covers the
# realistic cases; past that a human should take over.
MAX_HANDOFFS = 3
