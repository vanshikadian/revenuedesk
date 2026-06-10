"""Stage two of the agent: turning a fired signal into words.

Two interchangeable narrators:

* ClaudeNarrator: one Claude call per fired signal. The model only sees the
  rows relevant to that signal, never the whole warehouse, and writes a
  2-sentence recommended action plus a 1-sentence reason.
* TemplateNarrator: deterministic and offline. Used when no API key is
  configured, and as the fallback if an LLM call fails, so a missing key
  never crashes a run.
"""

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from revops import config
from revops.signals import CHAMPION_SILENT, RENEWAL_UPCOMING, USAGE_DROP, Signal

log = logging.getLogger(__name__)


@dataclass
class Narration:
    recommended_action: str
    reason: str


class Narrator(Protocol):
    name: str

    def narrate(self, signal: Signal) -> Narration: ...

    def summarize(self, flagged_accounts: int, arr_at_risk: float, most_urgent: str) -> str: ...


def _fmt_money(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount / 1_000:.0f}K"


class TemplateNarrator:
    """Deterministic offline narrator; needs no network or keys."""

    name = "template (offline)"

    def narrate(self, signal: Signal) -> Narration:
        ctx = signal.context
        owner = ctx.get("owner_rep", "the account owner")
        if signal.signal == RENEWAL_UPCOMING:
            action = (
                f"{owner} should schedule a renewal-planning call within the next "
                f"five business days and walk in with a value recap of the last two "
                f"quarters. Confirm the buying committee and surface any budget "
                f"changes before the {ctx['renewal_date']} renewal."
            )
            reason = (
                f"The renewal is {ctx['days_until_renewal']} days out, inside the "
                f"60-day window where unmanaged renewals start slipping."
            )
        elif signal.signal == USAGE_DROP:
            action = (
                f"{owner} should book a usage review with the account's power users "
                f"this week to find out what broke or who left. Offer a focused "
                f"re-onboarding session for the team to rebuild the habit."
            )
            reason = (
                f"Weighted usage fell {ctx['drop_pct']}% over the trailing 14 days "
                f"({ctx['prior_14d_usage']} → {ctx['recent_14d_usage']})."
            )
        elif signal.signal == CHAMPION_SILENT:
            action = (
                f"{owner} should re-engage champion {ctx['champion_name']} "
                f"({ctx['champion_title']}) with a personal note tied to a concrete "
                f"product win. In parallel, identify and warm up a backup champion "
                f"inside the account."
            )
            reason = (
                f"{ctx['champion_name']} has been inactive for {ctx['silent_days']} "
                f"days, well past the 30-day silence threshold."
            )
        else:  # pragma: no cover, future signal types
            action = f"{owner} should review the account and decide the next step."
            reason = f"Signal '{signal.signal}' fired."

        if signal.severity == 3:
            action = "Treat this as an active save play and loop in leadership today. " + action
        return Narration(recommended_action=action, reason=reason)

    def summarize(self, flagged_accounts: int, arr_at_risk: float, most_urgent: str) -> str:
        if flagged_accounts == 0:
            return "All accounts look healthy. No signals firing right now."
        plural = "account needs" if flagged_accounts == 1 else "accounts need"
        return (
            f"{flagged_accounts} {plural} attention, {_fmt_money(arr_at_risk)} ARR "
            f"at risk. Most urgent: {most_urgent}."
        )


_SYSTEM_PROMPT = (
    "You are a revenue-operations copilot. You receive one risk signal for one "
    "customer account, with only the data behind that signal. Respond with JSON "
    'only, no markdown: {"action": "<exactly two sentences telling the sales rep '
    'what to do next>", "reason": "<exactly one sentence explaining why, citing '
    'the data>"}'
)


class ClaudeNarrator:
    """One Claude call per fired signal. Falls back to the template on any error."""

    name = "claude"

    def __init__(self, api_key: str, model: str | None = None):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model or config.anthropic_model()
        self._fallback = TemplateNarrator()

    def narrate(self, signal: Signal) -> Narration:
        payload = {
            "account": signal.account_name,
            "signal": signal.signal,
            "severity": signal.severity,
            "data": signal.context,
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=300,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = next(b.text for b in response.content if b.type == "text").strip()
            # strip markdown code fences if the model wrapped the JSON
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            if not text:
                raise ValueError("empty response from model")
            parsed = json.loads(text)
            return Narration(recommended_action=str(parsed["action"]), reason=str(parsed["reason"]))
        except Exception:
            log.warning(
                "LLM call failed for %s/%s; using template fallback",
                signal.account_name,
                signal.signal,
                exc_info=True,
            )
            return self._fallback.narrate(signal)

    def summarize(self, flagged_accounts: int, arr_at_risk: float, most_urgent: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=120,
                system=(
                    "You write a single punchy headline for a revenue dashboard. "
                    "One sentence, plain text, lead with the numbers."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "accounts_needing_attention": flagged_accounts,
                                "arr_at_risk": arr_at_risk,
                                "most_urgent_account": most_urgent,
                            }
                        ),
                    }
                ],
            )
            return next(b.text for b in response.content if b.type == "text").strip()
        except Exception:
            log.warning("LLM summary failed; using template fallback", exc_info=True)
            return self._fallback.summarize(flagged_accounts, arr_at_risk, most_urgent)


def get_narrator() -> Narrator:
    """Pick the best available narrator. Never raises on a missing key."""
    api_key = config.anthropic_api_key()
    if api_key:
        try:
            return ClaudeNarrator(api_key)
        except Exception:
            log.warning("anthropic SDK unavailable; using template narrator", exc_info=True)
    return TemplateNarrator()
