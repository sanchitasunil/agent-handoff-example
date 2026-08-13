# Agent Handoff

One agent answers general questions. When the caller asks about something
specific, it hands the **live conversation** to a second agent that specialises
in it — and passes along what it already learned, so the caller never repeats
themselves.

```
caller -> triage -> matches a specialist? -> yes -> specialist takes over
                                          -> no  -> triage keeps helping
```

This is different from escalating to a human. The call keeps going, only the
agent changes.

---

## Run it

```bash
uv sync
cp .env.example .env.local     # fill in your keys
uv run python src/agent.py download-files
uv run python src/agent.py console
```

You need `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `MURF_API_KEY`,
`DEEPGRAM_API_KEY`, and `GOOGLE_API_KEY`. All six are in `.env.example`.

### Try it

**Ask something general:**

> What are your hours?

Triage answers. Nothing prints — no transfer happened.

**Ask something billing-related:**

> I was charged twice this month and I want a refund.

Triage says it is connecting you to accounts, the specialist takes over and
already knows about the double charge. Your terminal prints:

```
[handoff] triage -> accounts (billing dispute)
```

**Ask the specialist something off-topic:**

> Actually, where are you based?

```
[handoff] accounts -> triage (not a billing question)
```

**Now try the same in Hindi.** The specialist replies in Devanagari, because the
language rules are shared across both agents.

---

## Make it yours

Edit one list at the top of `src/agent.py`:

```python
SPECIALISTS = [
    {
        "name": "accounts",
        "when_to_use": "Billing, payments, refunds, subscriptions, or login problems.",
        "persona": "You handle billing and account questions. Never ask for a password or a full card number.",
        "voice": "Samar",
    },
    ...
]
```

`when_to_use` **is** the routing logic — triage reads it to decide where to send
someone. Keep each one concrete, and keep entries from overlapping. Add a third
entry and it works immediately; the prompt and the agent class are both built
from this list.

---

## How it works

Five pieces, all in `src/agent.py`.

### 1. Shared context

Carries what triage learned across the switch. LiveKit keeps `session.userdata`
when the agent changes, so this survives the handoff.

```python
@dataclass
class HandoffContext:
    caller_intent: str = ""
    facts: list[str] = field(default_factory=list)
    handoffs: int = 0
```

### 2. Shared rules

A specialist gets its own `instructions` and does **not** inherit
`SYSTEM_PROMPT`. Anything that must survive a transfer goes here — most
importantly the language rules, or your specialist will answer a Hindi caller
in English.

```python
SHARED_RULES = """
LANGUAGE & SCRIPT
Reply in the same language the caller is speaking. If they write in English,
answer in English. Never switch languages on your own.
When you do write a non-English language, use its native script:
- Hindi in Devanagari (नमस्ते), never romanized (never "namaste").
- Same rule for all other non-English languages.
"""
```

Order matters. Lead with "reply in the same language" — if the Hindi example
comes first, the model over-indexes on it and greets English callers in Hindi.

### 3. The specialist

One class covers every entry in `SPECIALISTS`. `on_enter` runs the moment it
takes over, and reads the carried context instead of asking again.

```python
    async def on_enter(self) -> None:
        ctx = self.session.userdata
        known = f"They want: {ctx.caller_intent}."
        if ctx.facts:
            known += " Already established: " + "; ".join(ctx.facts) + "."
        await self.session.generate_reply(
            instructions=f"Greet the caller, show you already know why they were "
            f"transferred, and ask one useful next question. {known}"
        )
```

It also has `return_to_triage`, for when the caller asks something outside its
area.

### 4. The transfer tool

**Returning an `Agent` from a function tool is what makes LiveKit switch.** That
single line is the whole mechanism; the rest is bookkeeping.

```python
        print(f"[handoff] triage -> {specialist} ({reason})")
        return Specialist(spec)
```

Two things worth copying:

- It returns a **string** if the name is wrong or the cap is hit. The model
  reads that as an ordinary tool result and keeps talking. Nothing crashes.
- It writes to `context.userdata` **before** returning, so the specialist's
  `on_enter` has something to read.

`MAX_HANDOFFS` stops two agents passing a caller back and forth forever.

### 5. The session

```python
    session = AgentSession[HandoffContext](
        userdata=HandoffContext(),
        ...
    )
```

Without `userdata`, the transfer tool has nowhere to write and errors mid-call.

---

## Adding this to an agent you already built

Five edits, roughly sixty lines:

1. Add the `HandoffContext` dataclass.
2. Add `SHARED_RULES` and the `SPECIALISTS` list.
3. Add the `Specialist` class.
4. Add `transfer_to_specialist` to your existing agent, and append
   `ROUTING_PROMPT + SHARED_RULES` to its instructions.
5. Change `AgentSession(...)` to `AgentSession[HandoffContext](userdata=HandoffContext(), ...)`.

Keep your existing class named `Assistant`, keep your tools, keep your prompt.
Nothing else changes.

---

## Give each specialist its own voice

The caller *hears* the transfer, which is the clearest way to show it working.
Add one argument to `Specialist.__init__`:

```python
        super().__init__(
            instructions=...,
            tts=murf.TTS(voice=spec["voice"], style="Conversation"),
        )
```

Use voices different from your triage voice. Browse them in the
[Murf voice library](https://murf.ai/api/docs/voices-styles/voice-library).

One caveat: this makes `uv run pytest` print an http-session warning, because
tests build the agent outside a running job. Tests still pass and real calls are
unaffected — that is why it is off by default.

---

## Tests

```bash
uv run pytest
```

Five tests. Three check the agent's general behaviour. Two cover handoff: a
general question produces a message and no function call, and a billing request
calls `transfer_to_specialist` and hands off to `Specialist`.

```python
        # Triage sometimes announces the transfer out loud first, sometimes not
        result.expect.skip_next_event_if(type="message", role="assistant")

        result.expect.next_event().is_function_call(name="transfer_to_specialist")
        result.expect.skip_next_event_if(type="function_call_output")
        result.expect.next_event().is_agent_handoff(new_agent_type=Specialist)
```

Those `skip_next_event_if` lines matter. Whether the model speaks before calling
the tool varies run to run, so asserting a fixed event order makes the test
flaky.

The handoff tests need `userdata=HandoffContext()` on the session, because the
tool reads it.

---

## If something goes wrong

| What you see | Why | Fix |
|---|---|---|
| Specialist answers a Hindi caller in English | It did not get your language rules | Add `+ SHARED_RULES` to the specialist's instructions |
| Specialist answers an English caller in Hindi | `SHARED_RULES` leads with the Hindi example | Put "reply in the same language" first |
| `ValueError: AgentSession userdata is not set` | Session has no context object | Add `userdata=HandoffContext()` |
| Never transfers | `when_to_use` too vague, or entries overlap | Rewrite those sentences — that text is the routing logic |
| Transfers when it shouldn't | Same cause | Make `when_to_use` narrower |
| Specialist asks the caller to repeat | `caller_intent` and `facts` arrived empty | Check the tool writes to `ctx` before returning |
| Caller bounces between agents | Working as designed | Lower `MAX_HANDOFFS` |

---

## Links

- [Murf LiveKit starter](https://github.com/murf-ai/murf-livekit-starter)
- [Murf Falcon TTS](https://murf.ai/api/docs/text-to-speech-models/falcon-2)
- [Murf voice library](https://murf.ai/api/docs/voices-styles/voice-library)
- [LiveKit Agents docs](https://docs.livekit.io/agents)
