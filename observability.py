"""Handoff logging and the end-of-session recap.

Routing bugs are invisible in a voice demo — the call sounds fine while the
caller is quietly sent to the wrong team. These two functions make the path
the session actually took readable after the fact.
"""

from __future__ import annotations

import logging

from handoff_context import HandoffContext

logger = logging.getLogger("handoff")


def log_handoff(userdata: HandoffContext, from_agent: str, to_agent: str, reason: str) -> None:
    """Append a switch to the session's handoff history and log it.

    This is the only place `handoff_history` is written, so the cap in
    agent.py and the recap below can both trust its length.
    """
    userdata.handoff_history.append({"from": from_agent, "to": to_agent, "reason": reason})
    logger.info("handoff %s -> %s (%s)", from_agent, to_agent, reason)
    print(f"[handoff] {from_agent} -> {to_agent}: {reason}")


def session_summary(userdata: HandoffContext) -> str:
    """One-line recap of the route, e.g. "triage -> accounts -> triage"."""
    if not userdata.handoff_history:
        return "triage (no handoffs)"

    chain = [userdata.handoff_history[0]["from"]]
    chain.extend(hop["to"] for hop in userdata.handoff_history)
    return " -> ".join(chain)
