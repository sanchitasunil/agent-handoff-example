"""Drive the routing over text, so you can see handoffs without making a call.

Runs the real AgentSession with the real agents and the real function tools,
feeding typed turns instead of audio. No STT, no TTS, no LiveKit room.

    python simulate.py

Only the LLM key matters here. DEEPGRAM_API_KEY, MURF_API_KEY and the
LIVEKIT_* variables are for voice and are not read by this script.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

import observability
from agent import TriageAgent
from handoff_context import HandoffContext

load_dotenv()

# Edit these to try your own routing. Each conversation gets a fresh session
# starting at triage, so a handoff in one doesn't bleed into the next.
SCRIPT = [
    {
        "name": "general question",
        "expect": "stays with triage, no handoff",
        "turns": ["What are your hours?"],
    },
    {
        "name": "account request",
        "expect": "hands off to accounts; specialist opens with the carried context",
        "turns": ["I was charged twice for my subscription this month and I want a refund."],
    },
    {
        "name": "nothing in the config covers this",
        "expect": "stays with triage, no invented specialist",
        "turns": ["Do you know a good recipe for sourdough?"],
    },
    {
        "name": "specialist then back",
        "expect": "triage -> accounts -> triage",
        "turns": [
            "There's a duplicate charge on my invoice.",
            "Thanks. Separately, where are you based?",
        ],
    },
]


def describe(events) -> list[str]:
    """Turn RunResult events into one readable line each."""
    lines = []
    for ev in events:
        kind = getattr(ev, "type", "")
        if kind == "function_call":
            lines.append(f"    tool called   : {ev.item.name}({ev.item.arguments})")
        elif kind == "function_call_output":
            out = str(ev.item.output).replace("\n", " ")
            lines.append(f"    tool returned : {out[:110]}")
        elif kind == "agent_handoff":
            old = type(ev.old_agent).__name__ if ev.old_agent else "None"
            new = getattr(ev.new_agent, "name", type(ev.new_agent).__name__)
            lines.append(f"    HANDOFF       : {old} -> {new}")
        elif kind == "message":
            content = ev.item.content
            text = " ".join(c for c in content if isinstance(c, str)) if content else ""
            if text.strip():
                lines.append(f"    said          : {text.strip()[:160]}")
    return lines


def active_agent(session) -> str:
    current = session.current_agent
    return getattr(current, "name", type(current).__name__)


async def main() -> int:
    from livekit.agents import AgentSession

    if not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Export it (or put it in .env) and re-run.",
            file=sys.stderr,
        )
        return 1

    from livekit.plugins import openai

    model = openai.LLM(model="gpt-4o-mini")
    print("model: gpt-4o-mini\n")

    routes = []

    for convo in SCRIPT:
        print("=" * 72)
        print(f"conversation: {convo['name']}")
        print(f"expected    : {convo['expect']}")

        context = HandoffContext()
        session = AgentSession[HandoffContext](llm=model, userdata=context)
        await session.start(agent=TriageAgent())

        try:
            for turn in convo["turns"]:
                print(f"\n  caller: {turn}")
                result = await session.run(user_input=turn)
                for line in describe(result.events):
                    print(line)
                print(f"    active agent  : {active_agent(session)}")
        finally:
            await session.aclose()

        route = observability.session_summary(context)
        routes.append((convo["name"], route, context.caller_intent))
        print(f"\n  route: {route}")
        print(f"  carried intent: {context.caller_intent or '(none)'}")

    print("=" * 72)
    print("handoff chains")
    for name, route, intent in routes:
        print(f"  {name:<38} {route}")
        if intent:
            print(f"  {'':<38} carried: {intent[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
