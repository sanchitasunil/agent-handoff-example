"""
agent.py — the triage agent and the LiveKit worker entrypoint.

What this file does, end to end:
  1. Defines the triage persona: it answers general questions itself and
     transfers to a specialist only when the request matches one.
  2. Registers the tools triage can call — an example `get_time`,
     `create_escalation` for handing off to a human, and
     `transfer_to_specialist` for handing off to another agent.
  3. Defines an `entrypoint` function that LiveKit calls when a new job
     (i.e. a new room the agent should join) is dispatched to this worker.
  4. Inside the entrypoint, wires up an AgentSession with speech-to-text
     (Deepgram), a language model (OpenAI), text-to-speech (Murf), and
     voice activity detection (Silero), plus the shared HandoffContext that
     survives an agent switch.

Credentials come from environment variables; see .env.example.

Run it locally with the LiveKit CLI dev/console commands — see the README
for exact commands.
"""

import logging

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import deepgram, murf, openai, silero

import observability
import specialists
from escalations import notify, store
from escalations.config import EMERGENCY_NOTICE, ESCALATION_REASONS, HIGH_RISK_MODE
from escalations.summary import build_summary
from handoff_config import MAX_HANDOFFS, SPECIALISTS
from handoff_context import HandoffContext

# TTS provider is imported separately below, right above where it's used,
# with a comment block showing how to swap it out.

# Load variables from a local .env file (if present) into the process
# environment. In production you'd typically set these via your deployment
# platform instead of a .env file, but load_dotenv() is a no-op if the file
# doesn't exist, so it's safe to leave in for both cases.
load_dotenv()

logger = logging.getLogger("voice-agent")


# ---------------------------------------------------------------------------
# Example tool
# ---------------------------------------------------------------------------
# @function_tool turns a plain async function into something the LLM can
# call mid-conversation. The docstring and type hints become the tool's
# description and parameter schema — the LLM reads them to decide when and
# how to call it. This one is deliberately trivial (returns the current
# time) so the *pattern* is obvious; add more tools the same way (e.g. a
# lookup against your own API or database).
@function_tool()
async def get_time(context: RunContext) -> str:
    """Get the current server time. Use this if the user asks what time it is."""
    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"The current server time is {now}."


_VALID_REASON_CODES = {reason["code"] for reason in ESCALATION_REASONS}


@function_tool()
async def create_escalation(
    context: RunContext,
    reason_code: str,
    urgency: str,
    who: str,
    what: str,
    checked: list[str],
    language: str,
    followup_method: str,
    followup_contact: str,
) -> str:
    """Hand this request off to a human. Only call this after you have

    (1) actually tried to help the caller yourself and could not, or the
    request needs a decision you are not allowed to make, AND
    (2) told the caller — out loud, in plain language — exactly what you
    are about to send (the reason, a short description of what happened,
    what you already checked, the urgency, and how they'd like to be
    followed up with), AND
    (3) the caller has clearly said yes to filing it.

    Do NOT call this tool:
      - for anything you can resolve yourself
      - before you've asked the caller for permission
      - if the caller declines or seems unsure — in that case, do not call
        this tool at all; acknowledge their answer and offer an
        alternative instead

    If this is a high-risk track (see escalations/config.py HIGH_RISK_MODE)
    and the caller may be in immediate danger, you must speak the
    emergency notice and point them to real emergency help BEFORE calling
    this tool — filing a request is not the same as getting them help now.

    Args:
        reason_code: One of the `code` values from
            escalations.config.ESCALATION_REASONS — e.g. "cannot_resolve"
            or "needs_human_decision". Must match exactly; if you're not
            sure which reason fits, pick the closest one and explain the
            situation in `what`.
        urgency: How urgent this is: "low", "medium", "high", or
            "emergency". If you're unsure, it's fine to under-specify —
            the system will fall back to the reason's default urgency.
        who: Who needs help — the caller's name, or however they've
            identified themselves in this conversation.
        what: A brief, plain-language description of the situation or
            request. Do not include passwords, PINs, one-time codes, or
            full card/account numbers here even if the caller says them —
            summarize around them (e.g. "caller wants to verify their
            card on file" rather than repeating the number).
        checked: A short list of what you already tried or looked at,
            so the human doesn't repeat that work (e.g. ["checked FAQ",
            "confirmed account is active"]). Pass an empty list if you
            didn't check anything yet.
        language: The caller's spoken or preferred language, so whoever
            follows up can plan for it (e.g. "English", "Spanish").
        followup_method: How the caller wants to be reached back, in
            their own words (e.g. "callback", "text message", "email").
        followup_contact: The contact detail to use for that method (a
            phone number, email address, etc.), exactly as the caller
            gave it to you. It will be redacted before storage.

    Returns:
        A short status string with the reference id and whether the
        notification was delivered — read the reference id back to the
        caller and tell them a human will follow up; do not promise an
        instant reply.
    """
    # --- validate -----------------------------------------------------
    if reason_code not in _VALID_REASON_CODES:
        return (
            f"Could not create escalation: '{reason_code}' is not a known "
            f"reason_code. Valid options are: {', '.join(sorted(_VALID_REASON_CODES))}."
        )

    # Permission itself is handled conversationally, in the instructions
    # (the model must ask and get a yes before this tool is ever called —
    # see the Assistant instructions below). If the caller declines, the
    # model simply never calls this function: no row is written, nothing
    # to undo. There is no separate "decline" branch here on purpose.

    # Fold the contact detail into the follow-up method string so it
    # flows through build_summary()'s existing redact() pass on
    # follow_up_method, without changing that function's signature.
    follow_up = (
        f"{followup_method} — {followup_contact}"
        if followup_contact
        else followup_method
    )

    # --- duplicate guard ------------------------------------------------
    existing = store.find_open_duplicate(who, reason_code)

    # --- build (redaction happens inside build_summary) -----------------
    summary = build_summary(
        reason_code=reason_code,
        what_happened=what,
        checked=checked,
        urgency=urgency,
        language=language,
        follow_up_method=follow_up,
        caller=who,
    )

    # --- store (source of truth) -----------------------------------------
    if existing:
        row = store.update(existing["id"], summary)
        action = "updated"
    else:
        row = store.create(summary)
        action = "created"

    # --- notify (best-effort; never affects the stored row) --------------
    delivered = notify.send(row)
    store.mark_notified(row["id"], delivered)

    prefix = f"{EMERGENCY_NOTICE} " if HIGH_RISK_MODE else ""
    return (
        f"{prefix}Escalation {action}: reference {row['id']}, "
        f"urgency {row['urgency']}. Notification delivered: "
        f"{'yes' if delivered else 'no — the request is still saved and will be reviewed'}. "
        "Read the reference id back to the caller, confirm a human will "
        "follow up via their chosen method, and do not promise an instant reply."
    )


_SPECIALIST_NAMES = {spec["name"] for spec in SPECIALISTS}


@function_tool()
async def transfer_to_specialist(
    context: RunContext[HandoffContext],
    specialist: str,
    caller_intent: str,
    facts: list[str],
    reason: str,
) -> object:
    """Transfer the live conversation to a specialist team.

    Only use this when the caller's request clearly matches one of the
    specialist descriptions you were given. If nothing matches, keep helping
    the caller yourself — do not transfer on the chance that someone else
    might be a better fit, and never name a specialist that isn't on your list.

    Tell the caller which team you are connecting them to before you call this.
    The transfer happens immediately, so anything you meant to say afterwards
    will not be heard.

    Args:
        specialist: Which team to transfer to. Must be one of the names in
            your specialist list, spelled exactly.
        caller_intent: One sentence on what the caller is trying to achieve.
            The specialist reads this instead of asking them to start over.
        facts: Specifics you have already established that the specialist
            would otherwise have to ask for — an order number, the error they
            saw, what they already tried. Leave empty if you have none. Never
            include passwords, PINs, one-time codes, or full card numbers.
        reason: Why this specialist, in a few words. For the log, not the caller.
    """
    userdata = context.userdata

    if specialist not in _SPECIALIST_NAMES:
        return (
            f"There is no specialist called '{specialist}'. Available: "
            f"{', '.join(sorted(_SPECIALIST_NAMES))}. Stay on the line and help "
            "the caller yourself."
        )

    # Refuse rather than raise: the caller is mid-conversation and an exception
    # here would drop the call over what is really just a routing loop.
    if len(userdata.handoff_history) >= MAX_HANDOFFS:
        return (
            f"Transfer limit of {MAX_HANDOFFS} reached, so you are staying with "
            "this caller. Tell them you'll help them directly, and offer to "
            "arrange a callback if you genuinely cannot."
        )

    userdata.caller_intent = caller_intent
    for fact in facts:
        userdata.record_fact(fact)

    target = specialists.get_specialist(specialist)
    observability.log_handoff(userdata, "triage", specialist, reason)

    # Only the Agent is returned, with no accompanying message. Returning a
    # string as well makes the framework ask triage for one more reply, which
    # the caller hears on top of the specialist's greeting.
    return target


# BASE_INSTRUCTIONS covers every track. HIGH_RISK_ADDENDUM is appended only
# when escalations.config.HIGH_RISK_MODE is True, so the safety language
# actually reflects how this deployment is configured rather than reading
# as a hypothetical.
BASE_INSTRUCTIONS = """
You are a helpful, friendly voice assistant. Keep your responses concise
and conversational, since they will be spoken aloud. Ask clarifying
questions when a request is ambiguous. If you don't know something, say so
plainly rather than guessing.

Try to resolve the caller's request yourself first. Only escalate to a
human when you hit one of the reasons defined in escalations/config.py
(for example: you've genuinely tried and can't answer or solve the
request, or the caller needs a decision you aren't allowed to make). Do
not escalate for things you are able to handle directly.

Before creating an escalation, you must:
  1. Tell the caller, in plain language, exactly what you are about to
     send to a human: the reason, a short description of what happened,
     what you already checked, how urgent it is, and how they'd like to
     be followed up with.
  2. Ask the caller for permission to send it.
  3. If they decline, do NOT create the request. Acknowledge their answer
     and offer an alternative (try a different approach yourself, wait,
     or let them decide what happens next).
  4. Only call create_escalation once the caller has clearly agreed.

After an escalation is created, tell the caller the reference id, let them
know a real person will review it and follow up using the method they
chose, and do NOT promise an instant reply or that a human is available
right now — you don't know their availability.
""".strip()

# The specialist list is injected rather than hardcoded so that editing
# handoff_config.py is genuinely all it takes to re-route this agent.
ROUTING_INSTRUCTIONS = """
You are the first agent the caller reaches. Answer general questions yourself
— hours, what the company does, where to find things, anything you can settle
in a turn or two.

Transfer only when the request clearly matches one of these teams:

{routing_guide}

When it does: say which team you're connecting them to, then call
transfer_to_specialist. Pass along what they've told you so far, so they don't
have to repeat it. Announce the transfer first — once you call the tool the
specialist takes over and anything you were about to say is lost.

When it doesn't match any of them, stay and help. There is no general
specialist to fall back on, and transferring a caller to the wrong team costs
them more time than a slightly slower answer from you.
""".strip()

# OPTIONAL — delete this block for domains where it doesn't apply.
#
# For health, crisis, or disaster lines, a transfer is a queue position, not
# help. This is gated on the same escalations.config.HIGH_RISK_MODE flag as the
# escalation notice so one switch governs both, and so a billing bot's prompt
# never mentions emergencies at all.
HANDOFF_SAFETY_ADDENDUM = """
Before any transfer, consider whether the caller may be in immediate danger.
If they might be, say this first: "{emergency_notice}" A transfer is not
emergency help — the specialist is another line to wait on. Point them to
local emergency services or someone they trust nearby before you route them
anywhere, and say plainly that you're doing so because it will reach them
faster than you can.
""".strip().format(emergency_notice=EMERGENCY_NOTICE)

HIGH_RISK_ADDENDUM = """
SAFETY NOTE: this track handles situations that can be time-critical. A
saved escalation request is NOT the same as emergency help — it will be
reviewed by a person later, not necessarily right away. If the caller
describes anything suggesting they may be in immediate danger or at
serious risk, say this before doing anything else: "{emergency_notice}"
Only continue with the normal permission-and-escalation flow above after
you've said that, and even then, encourage them to also reach out to real
emergency help directly rather than relying on this request alone.
""".strip().format(emergency_notice=EMERGENCY_NOTICE)


class TriageAgent(Agent):
    """The agent callers start with, and return to after a specialist finishes."""

    async def on_enter(self) -> None:
        """Greet the caller on the way back from a specialist.

        Only on the way back. On a fresh call this agent is already speaking —
        entrypoint sends the opening greeting — and an on_enter reply there
        would talk over it. A non-empty handoff history is what distinguishes
        the two, since it can only be non-empty after a transfer.
        """
        # session.userdata raises when a session was built without it. That's a
        # wiring mistake, not a reason to drop a live call, so treat it as
        # "first entry" and stay quiet.
        try:
            userdata = self.session.userdata
        except (ValueError, RuntimeError):
            return

        if not getattr(userdata, "handoff_history", None):
            return

        self.session.generate_reply(
            instructions=(
                "The caller has just been passed back to you from a specialist. "
                "In one short sentence, let them know they're back with you and "
                "ask what else they need. Don't re-introduce yourself at length."
            )
        )

    def __init__(self) -> None:
        parts = [
            BASE_INSTRUCTIONS,
            ROUTING_INSTRUCTIONS.format(routing_guide=specialists.routing_guide()),
        ]
        if HIGH_RISK_MODE:
            parts.append(HIGH_RISK_ADDENDUM)
            parts.append(HANDOFF_SAFETY_ADDENDUM)

        super().__init__(
            instructions="\n\n".join(parts),
            tools=[get_time, create_escalation, transfer_to_specialist],
        )


# The pre-handoff name, kept so anything importing Assistant still works.
Assistant = TriageAgent


# ---------------------------------------------------------------------------
# Worker-level setup (runs once per worker process, before any job)
# ---------------------------------------------------------------------------
def prewarm(proc: JobProcess) -> None:
    # Loading the VAD model here (once, at worker startup) instead of inside
    # entrypoint means every subsequent job reuses the already-loaded model
    # instead of reloading it per call, which would add latency.
    proc.userdata["vad"] = silero.VAD.load()


# ---------------------------------------------------------------------------
# Job entrypoint (runs once per room the agent is dispatched to)
# ---------------------------------------------------------------------------
async def entrypoint(ctx: JobContext) -> None:
    # Connect to the LiveKit room this job was dispatched for.
    await ctx.connect()

    # One context object for the whole call. LiveKit keeps userdata across agent
    # transitions, so this is what lets a specialist pick up mid-conversation.
    handoff_context = HandoffContext()

    session = AgentSession[HandoffContext](
        userdata=handoff_context,
        # Speech-to-text: transcribes the caller's audio into text.
        # Reads DEEPGRAM_API_KEY from the environment.
        stt=deepgram.STT(model="nova-3"),
        # Language model: generates the assistant's text responses.
        # Reads OPENAI_API_KEY from the environment.
        llm=openai.LLM(model="gpt-4o-mini"),
        # Text-to-speech: turns the LLM's text response back into audio.
        # Reads MURF_API_KEY from the environment. Pick a different voice with
        # murf.TTS(voice="..."); the default is en-US-matthew.
        tts=murf.TTS(),
        # Voice activity detection: figures out when the caller is
        # speaking vs. silent, so the agent knows when to start listening
        # and when the caller has finished a turn. Reused from prewarm()
        # so it's loaded once per worker, not once per job.
        vad=ctx.proc.userdata["vad"],
    )

    async def print_route(_reason: str | None = None) -> None:
        print(f"[session] route: {observability.session_summary(handoff_context)}")

    ctx.add_shutdown_callback(print_route)

    # Start the session: this attaches the pipeline to the room, begins
    # listening to the caller's audio track, and makes the agent join as
    # a participant that can be heard and can hear.
    await session.start(
        room=ctx.room,
        agent=TriageAgent(),
    )

    # Have the agent greet the caller once it has joined.
    await session.generate_reply(
        instructions="Greet the user warmly and ask how you can help today."
    )


if __name__ == "__main__":
    # cli.run_app() gives you the `console`, `dev`, `start`, and `download-files`
    # subcommands for free (see the run instructions for exact usage).
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
