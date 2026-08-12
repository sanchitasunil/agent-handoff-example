# Voice agent with in-session handoff

A LiveKit voice agent that answers general questions itself and transfers the
live conversation to a specialist when the request calls for one. The caller
doesn't repeat themselves across the transfer: intent, established facts, and
language ride along on the session.

Built and tested against **livekit-agents 1.6.9**.

```
                    caller
                      |
                 [ triage ]  <-- answers general questions here
                      |
        request matches a specialist's when_to_use?
                      |
            yes ------+------ no
             |                 |
     transfer_to_specialist   stay and help
             |
      [ accounts ]  [ technical ]      <-- defined in handoff_config.py
             |
      return_to_triage
             |
        [ triage ]
```

## Quick start

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash; .venv\Scripts\activate.bat for cmd
pip install -r requirements.txt
cp .env.example .env                # fill in your keys

python simulate.py                  # see routing work over text, LLM key only
python agent.py console             # talk to it in the terminal
python agent.py dev                 # connect to a LiveKit room
```

## Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | everything | the LLM; the only key `simulate.py` reads |
| `DEEPGRAM_API_KEY` | voice | speech-to-text |
| `MURF_API_KEY` | voice | text-to-speech (Murf) |
| `LIVEKIT_URL` | `agent.py dev` | your LiveKit server or Cloud URL |
| `LIVEKIT_API_KEY` | `agent.py dev` | |
| `LIVEKIT_API_SECRET` | `agent.py dev` | |
| `ESCALATION_SINK` | escalations | `console` (default), `webhook`, or `none` |
| `ESCALATION_WEBHOOK_URL` | escalations | only when the sink is `webhook` |

`simulate.py` needs `OPENAI_API_KEY` and nothing else — no voice keys, no
LiveKit credentials.

## Files

| File | What's in it |
|---|---|
| `handoff_config.py` | the specialist roster and the transfer cap — the file you edit |
| `handoff_context.py` | the typed session context, the briefing builder, redaction |
| `specialists.py` | the specialist agent, generated from config; `return_to_triage` |
| `agent.py` | triage agent, `transfer_to_specialist`, session wiring, entrypoint |
| `observability.py` | handoff logging and the end-of-session route recap |
| `simulate.py` | scripted text harness for the routing |
| `escalations/` | the earlier human-escalation feature, unchanged |
| `tests/` | escalation summary and redaction tests |

## Adapt it: edit `handoff_config.py`

Replace the two placeholder specialists with your own. Each entry needs a
`name`, a `display_name`, a `when_to_use` line, a `persona`, and a `greeting`.
`when_to_use` goes into the triage prompt verbatim, so it does the routing —
keep the entries concrete and non-overlapping. Adding an entry is enough; the
agent class and the triage prompt are both built from this list.

`MAX_HANDOFFS` caps agent switches per session, counting transfers and
hand-backs alike. Past the cap the transfer tool refuses and triage keeps the
caller rather than raising.

## Safety note

For health, crisis, or disaster domains, a transfer is a queue position, not
help. Set `HIGH_RISK_MODE = True` in `escalations/config.py` and the triage
prompt gains a block telling the agent to point the caller at local emergency
services or a trusted person nearby *before* routing them anywhere. It's off
by default, because that framing is alarming and out of place on a billing
line.

## Running `simulate.py`

Drives the real agents and the real function tools over typed turns — no audio,
no room — and prints which tool the model chose and which agent is active after
each turn.

```bash
python simulate.py
```

This is the fastest way to check a config change before picking up a phone:
edit `SPECIALISTS` in `handoff_config.py`, add a matching turn to `SCRIPT` at
the top of `simulate.py`, and run it. The printed tool call and active agent
tell you whether the transfer fired and whether the context crossed with it.

## Demo script

1. Ask a general question ("what are your hours?"). Triage answers; no transfer.
2. Ask something account-related ("I was charged twice and want a refund").
3. Triage names the team out loud before transferring — the caller is never
   handed off in silence.
4. The specialist opens by referring to the double charge. It was never told
   directly; it read the briefing off session context.
5. Ask something unrelated. The specialist hands back to triage.
6. End the session and read the logged chain: `triage -> accounts -> triage`.

## Tests

```bash
python -m pytest tests/ -q
```

Covers the escalation summary builder and the redaction rules that
`handoff_context.py` also uses. No keys and no audio involved.
