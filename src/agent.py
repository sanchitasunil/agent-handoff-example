import logging
from dataclasses import dataclass, field

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    room_io,
    tokenize,
)
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Change this prompt to change what your voice agent does.
# See README.md for example prompts (customer support, language tutor, receptionist).
SYSTEM_PROMPT = """You are a friendly and efficient customer support agent for a tech company. Help users with account issues, billing questions, and product troubleshooting. Be concise, empathetic, and solution-oriented. If you don't know something, say so honestly and offer to escalate. Your responses are concise and without complex formatting, emojis, or symbols."""

# Rules every agent follows, triage and specialists alike. A specialist gets its
# own instructions and does NOT inherit SYSTEM_PROMPT, so anything that must
# survive a transfer belongs here. If your prompt already has a LANGUAGE & SCRIPT
# block, move it here or the specialist will answer a Hindi caller in English.
SHARED_RULES = """
LANGUAGE & SCRIPT
Reply in the same language the caller is speaking. If they write in English,
answer in English. Never switch languages on your own.
When you do write a non-English language, use its native script:
- Hindi in Devanagari (नमस्ते), never romanized (never "namaste").
- Same rule for all other non-English languages.
"""

# Change this list to change who your agent can transfer to.
# Each entry becomes a specialist agent. `when_to_use` is what triage routes on,
# so keep it concrete and keep the two entries from overlapping.
# `voice` is optional: a different Murf voice makes the transfer audible.
SPECIALISTS = [
    {
        "name": "accounts",
        "when_to_use": "Billing, payments, refunds, subscriptions, or login problems.",
        "persona": "You handle billing and account questions. Never ask for a password or a full card number.",
        "voice": "Samar",
    },
    {
        "name": "technical",
        "when_to_use": "Something is broken: errors, crashes, setup, or connection problems.",
        "persona": "You handle technical troubleshooting. Give one step at a time and wait for the result.",
        "voice": "Pooja",
    },
]

# Stop after this many transfers so two agents can't pass a caller back and forth.
MAX_HANDOFFS = 3

# Routing rules appended to SYSTEM_PROMPT so triage knows when to hand off.
ROUTING_PROMPT = "\n\n".join(
    [
        "\n\nYou are the first agent the caller reaches. Answer general questions yourself.",
        "Transfer only when the request matches one of these:",
        "\n".join(f"- {s['name']}: {s['when_to_use']}" for s in SPECIALISTS),
        "Say which team you are connecting them to before you call transfer_to_specialist. "
        "Once you call it the specialist takes over, so anything you meant to say next is lost. "
        "If the request matches nothing above, stay and help.",
    ]
)


@dataclass
class HandoffContext:
    """What triage learned, carried to the specialist so the caller repeats nothing."""

    caller_intent: str = ""
    facts: list[str] = field(default_factory=list)
    handoffs: int = 0


class Specialist(Agent):
    """One specialist, built from a SPECIALISTS entry."""

    def __init__(self, spec: dict[str, str]) -> None:
        self.spec = spec
        super().__init__(
            instructions=f"{spec['persona']} The caller was just transferred to you. "
            "You will be told what they already explained. Do not make them repeat it. "
            "Keep replies short and spoken-friendly." + SHARED_RULES,
            # Want the caller to HEAR the transfer? Give the specialist its own
            # Murf voice by adding this argument (see HANDOFF.md, Step 8):
            #     tts=murf.TTS(voice=spec["voice"], style="Conversation"),
        )

    async def on_enter(self) -> None:
        # Greet using what triage gathered, so the caller does not start over.
        ctx = self.session.userdata
        known = f"They want: {ctx.caller_intent}."
        if ctx.facts:
            known += " Already established: " + "; ".join(ctx.facts) + "."
        await self.session.generate_reply(
            instructions=f"Greet the caller, show you already know why they were "
            f"transferred, and ask one useful next question. {known}"
        )

    @function_tool
    async def return_to_triage(self, context: RunContext, reason: str) -> Agent:
        """Hand the caller back to the general agent.

        Use this when the caller asks about something outside your area.

        Args:
            reason: Why you are handing back, in a few words.
        """
        print(f"[handoff] {self.spec['name']} -> triage ({reason})")
        return Assistant()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT + ROUTING_PROMPT + SHARED_RULES)

    @function_tool
    async def transfer_to_specialist(
        self,
        context: RunContext,
        specialist: str,
        caller_intent: str,
        facts: list[str],
        reason: str,
    ) -> Agent | str:
        """Transfer the caller to a specialist team.

        Only use this when the request matches a specialist's description. Tell the
        caller which team you are connecting them to before calling this.

        Args:
            specialist: Which team, spelled exactly as listed in your instructions.
            caller_intent: One sentence on what the caller is trying to do.
            facts: Details you already have, so the specialist need not ask again.
            reason: Why this team, in a few words.
        """
        spec = next((s for s in SPECIALISTS if s["name"] == specialist), None)
        if spec is None:
            names = ", ".join(s["name"] for s in SPECIALISTS)
            return f"There is no specialist called {specialist}. Options: {names}. Help the caller yourself."

        ctx = context.userdata
        if ctx.handoffs >= MAX_HANDOFFS:
            return "Transfer limit reached. Stay and help the caller yourself."

        # In production, strip anything sensitive from caller_intent/facts here
        # before storing it, since this text is carried and logged.
        ctx.caller_intent = caller_intent
        ctx.facts.extend(facts)
        ctx.handoffs += 1

        print(f"[handoff] triage -> {specialist} ({reason})")
        return Specialist(spec)

    # To add more tools, use the @function_tool decorator.
    # Here's an example that adds a simple weather tool.
    # (function_tool and RunContext are already imported at the top of this file.)
    # @function_tool
    # async def lookup_weather(self, context: RunContext, location: str):
    #     """Use this tool to look up current weather information in the given location.
    #
    #     If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.
    #
    #     Args:
    #         location: The location to look up weather information for (e.g. city name)
    #     """
    #
    #     logger.info(f"Looking up weather for {location}")
    #
    #     return "sunny with a temperature of 70 degrees."


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    # userdata carries what triage learns across a handoff, so the specialist inherits it
    session = AgentSession[HandoffContext](
        userdata=HandoffContext(),
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=deepgram.STT(model="nova-3"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        llm=google.LLM(
            model="gemini-2.5-flash",
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
