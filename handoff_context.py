"""Context that survives an agent switch.

A handoff is only worth doing if the caller doesn't have to start over, so the
facts live on the session rather than on any one agent. LiveKit keeps
`session.userdata` across agent transitions, so a `HandoffContext` attached at
session start is the same object every specialist sees.

What gets carried is deliberately small: the intent, a handful of facts, and
the language. Not the transcript — a specialist that re-reads the whole call
spends its first turn summarising instead of helping, and any secret the caller
said out loud rides along with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# One redaction implementation for the whole repo. Duplicating the patterns
# here would mean two sets of rules to keep in sync, and the one that drifts is
# the one that leaks.
from escalations.summary import redact

__all__ = ["HandoffContext", "build_handoff_summary", "redact"]


@dataclass
class HandoffContext:
    """Session-scoped state shared by triage and every specialist."""

    caller_intent: str = ""
    facts_gathered: list[str] = field(default_factory=list)
    language: str = "English"
    handoff_history: list[dict[str, str]] = field(default_factory=list)

    def record_fact(self, fact: str) -> None:
        """Add a fact, redacted, skipping duplicates."""
        clean = redact(fact)
        if clean and clean not in self.facts_gathered:
            self.facts_gathered.append(clean)


def build_handoff_summary(userdata: HandoffContext) -> str:
    """Render the briefing a specialist reads on arrival.

    Everything here has already been through redact() — on the way in via
    `record_fact`, and again on the way out below, because `caller_intent` can
    be assigned directly by a tool that skipped the setter.

    Returns a placeholder line when nothing has been gathered yet, so a
    specialist that somehow starts cold still has something coherent to say.
    """
    if not userdata.caller_intent and not userdata.facts_gathered:
        return "No context was captured before the transfer — ask the caller what they need."

    parts = []
    if userdata.caller_intent:
        parts.append(f"Caller wants: {redact(userdata.caller_intent)}")
    if userdata.facts_gathered:
        parts.append("Already established: " + "; ".join(userdata.facts_gathered))
    if userdata.language and userdata.language.lower() != "english":
        parts.append(f"Caller's language: {userdata.language}")
    return " | ".join(parts)
