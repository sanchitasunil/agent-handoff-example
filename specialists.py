"""Specialist agents, generated from handoff_config.SPECIALISTS.

One class does for every specialist because they differ only in prompt text and
routing description. Adding a specialist is a config edit, not a new subclass.
"""

from __future__ import annotations

from livekit.agents import Agent, RunContext, function_tool

import observability
from handoff_config import SPECIALISTS
from handoff_context import HandoffContext, build_handoff_summary


class SpecialistAgent(Agent):
    def __init__(self, spec: dict[str, str]) -> None:
        self.spec = spec
        super().__init__(
            instructions=(
                f"{spec['persona']}\n\n"
                "You are speaking with a caller who was just transferred to you by a "
                "triage agent. You will be told what they already explained. Do not "
                "make them repeat any of it — acknowledge what you were told and "
                "carry on from there.\n\n"
                "Keep replies short and conversational; they are spoken aloud. If the "
                "caller raises something outside your area, use return_to_triage "
                "rather than guessing."
            ),
        )

    @property
    def name(self) -> str:
        return self.spec["name"]

    async def on_enter(self) -> None:
        """Open with the carried context so the caller doesn't start over.

        The briefing is injected as instructions rather than spoken verbatim so
        the specialist paraphrases it naturally instead of reciting a summary
        back at the caller.
        """
        summary = build_handoff_summary(self.session.userdata)
        self.session.generate_reply(
            instructions=(
                f"Greet the caller with: \"{self.spec['greeting']}\" Then, in one "
                "short sentence, show you already know why they were transferred, "
                "and ask the single most useful next question.\n\n"
                f"What you were told: {summary}"
            )
        )

    @function_tool()
    async def return_to_triage(self, context: RunContext[HandoffContext], reason: str) -> object:
        """Hand the caller back to the general triage agent.

        Use this when the caller's request has moved outside your area, or when
        you are finished and they have a new, unrelated question. Do not use it
        to avoid a question that is genuinely yours to answer.

        Args:
            reason: Why you are handing back, in a few words. The caller does
                not hear this; it goes into the session log.
        """
        # Imported here rather than at module scope: agent.py imports this
        # module to build its specialist list, so a top-level import would
        # close the loop.
        from agent import TriageAgent

        observability.log_handoff(context.userdata, self.name, "triage", reason)

        # Bare Agent, no accompanying message. Returning a string too makes the
        # framework ask this agent for one more reply, which the caller hears on
        # top of the greeting TriageAgent.on_enter is about to produce.
        return TriageAgent()


def get_specialist(name: str) -> SpecialistAgent:
    """Build the named specialist.

    A new instance per call, deliberately. An Agent holds per-conversation
    state, so a module-level singleton would leak one caller's conversation
    into another's. Nothing is lost by rebuilding: everything worth carrying
    lives in session userdata.

    Raises:
        KeyError: if `name` isn't in SPECIALISTS. Callers are expected to
            validate against the config first and handle the miss themselves.
    """
    for spec in SPECIALISTS:
        if spec["name"] == name:
            return SpecialistAgent(spec)
    raise KeyError(name)


def routing_guide() -> str:
    """The specialist menu, formatted for the triage agent's prompt."""
    lines = [f"- {spec['name']}: {spec['when_to_use']}" for spec in SPECIALISTS]
    return "\n".join(lines)
