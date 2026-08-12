"""
escalations/notify.py — pluggable, best-effort delivery of an alert about an
escalation that has ALREADY been saved.

IMPORTANT: escalations/store.py is the source of truth. By the time send()
is called, the escalation row already exists in the store — send() only
tries to tell a human about it faster than they'd notice it by checking the
store themselves. If delivery fails, the request is not lost; it's just
not (yet) announced anywhere. Never let a notification failure block or
undo a store write, and never crash the caller's turn because a webhook
timed out.

Three sinks, chosen via the ESCALATION_SINK environment variable:

  "console" (default) — pretty-prints the escalation to stdout. Zero setup,
      nothing to configure, good for local demos and development.

  "webhook" — POSTs the escalation as JSON to ESCALATION_WEBHOOK_URL. Deliberately
      generic: this works as-is against any endpoint that accepts a raw
      JSON POST body (a help-desk's inbound webhook, your own backend, a
      logging endpoint, ...). Chat platforms with a specific expected body
      shape (Discord, Slack) usually need a small adapter — see the worked
      example below.

  "none" — no-op. Use this when you don't want any outbound notification at
      all (e.g. in tests, or if a human will only ever check the store
      directly).

How to choose: start with "console" while developing. Move to "webhook"
once you have somewhere for the alert to land (a Discord/Slack channel or
your own on-call system). Use "none" to disable notifications without
touching any code.

---------------------------------------------------------------------------
Worked example — shaping the body for a Discord webhook specifically
---------------------------------------------------------------------------
Discord's incoming webhooks don't accept an arbitrary JSON object — they
expect a body shaped like {"content": "<message text>"} (or an "embeds"
array for richer formatting). The generic "webhook" sink here POSTs the
escalation row as-is, which Discord will reject. To notify a Discord
channel, either point ESCALATION_WEBHOOK_URL at a small relay you control
that reshapes the body, or adapt _send_webhook() to build a Discord-shaped
payload directly, e.g.:

    def _discord_payload(row: dict) -> dict:
        return {
            "content": (
                f"🚨 **Escalation {row['id']}** ({row['urgency']})\n"
                f"Reason: {row['reason_label']}\n"
                f"Who: {row.get('caller') or 'unknown'}\n"
                f"What: {row.get('what_happened') or '—'}\n"
                f"Follow-up: {row.get('follow_up_method') or 'not specified'}"
            )
        }

  ...and then `payload = _discord_payload(row)` instead of `payload = row`
  before the POST. The rest of _send_webhook() (timeout, error handling,
  headers) stays the same regardless of which platform is on the other end.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger("escalations.notify")

# Keep this short — a slow or hanging notification sink should never make
# the caller wait. The escalation is already saved by the time send() runs.
DEFAULT_TIMEOUT_SECONDS = 5


def send(escalation_row: dict) -> bool:
    """Best-effort notification about an already-stored escalation.

    Dispatches on the ESCALATION_SINK env var ("console" | "webhook" |
    "none"; defaults to "console" if unset). Never raises — any sender
    failure (bad config, network error, timeout, non-2xx response) is
    caught, logged, and reported back as False so the caller can tell the
    agent/human that delivery didn't go through, without the escalation
    itself being affected.

    Returns:
        True if the notification was delivered (or the sink is "none",
        which is an intentional no-op, not a failure). False otherwise.
    """
    sink = os.getenv("ESCALATION_SINK", "console").strip().lower()

    try:
        if sink == "console":
            return _send_console(escalation_row)
        if sink == "webhook":
            return _send_webhook(escalation_row)
        if sink == "none":
            # Notifications are deliberately disabled — this is a
            # configuration choice, not a delivery failure, so it does not
            # count against delivery success.
            return True

        logger.warning("Unknown ESCALATION_SINK %r; no notification sent.", sink)
        return False
    except Exception:  # noqa: BLE001 - a sender must never crash the call
        logger.exception("Escalation notification failed (sink=%s)", sink)
        return False


def _send_console(row: dict) -> bool:
    """Pretty-print the escalation to stdout. Always succeeds unless
    stdout itself is broken, in which case the outer try/except in send()
    catches it."""
    lines = [
        "=" * 60,
        f"ESCALATION {row.get('id', '(no id)')} — urgency: {row.get('urgency', 'unknown')}",
        f"Reason:      {row.get('reason_label', row.get('reason_code', '(unknown)'))}",
        f"Who:         {row.get('caller') or '(not given)'}",
        f"What:        {row.get('what_happened') or '(not given)'}",
        f"Checked:     {', '.join(row.get('checked') or []) or '(nothing recorded)'}",
        f"Language:    {row.get('language') or '(not given)'}",
        f"Follow-up:   {row.get('follow_up_method') or '(not given)'}",
        "=" * 60,
    ]
    print("\n".join(lines))
    return True


def _send_webhook(row: dict) -> bool:
    """POST the escalation as JSON to ESCALATION_WEBHOOK_URL. Generic on
    purpose — works against any endpoint that accepts a raw JSON body. See
    the module docstring for how to adapt this for Discord/Slack-style
    endpoints that expect a specific payload shape."""
    url = os.getenv("ESCALATION_WEBHOOK_URL", "").strip()
    if not url:
        logger.warning(
            "ESCALATION_SINK=webhook but ESCALATION_WEBHOOK_URL is not set; "
            "no notification sent."
        )
        return False

    payload = json.dumps(row).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=DEFAULT_TIMEOUT_SECONDS
        ) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception("Webhook delivery to %s failed", url)
        return False
