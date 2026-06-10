"""Slack alerts for severity-3 accounts, formatted as Block Kit.

With SLACK_WEBHOOK_URL set, alerts POST to the webhook. Without it, the
exact payload is logged instead.
"""

import json
import logging

import requests

from revops import config

log = logging.getLogger(__name__)


def build_alert_blocks(
    account_name: str,
    arr: float,
    owner_rep: str,
    signals: list[str],
    recommended_action: str,
    dashboard_url: str,
) -> dict:
    """Block Kit payload for a severity-3 account alert."""
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🔴 Severity 3: {account_name}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*ARR at risk:*\n${arr:,.0f}"},
                    {"type": "mrkdwn", "text": f"*Owner:*\n{owner_rep}"},
                    {"type": "mrkdwn", "text": "*Signals:*\n" + ", ".join(signals)},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Next action:*\n{recommended_action}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open dashboard"},
                        "url": dashboard_url,
                        "style": "danger",
                    }
                ],
            },
        ]
    }


def send_alert(payload: dict) -> bool:
    """POST to Slack if configured; otherwise log the payload.

    Returns True if the alert was actually delivered to Slack.
    """
    webhook = config.slack_webhook_url()
    if not webhook:
        log.info(
            "SLACK_WEBHOOK_URL not set; alert logged instead of sent:\n%s",
            json.dumps(payload, indent=2),
        )
        return False
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException:
        log.warning("Slack webhook delivery failed; alert logged instead", exc_info=True)
        log.info("Undelivered alert payload:\n%s", json.dumps(payload, indent=2))
        return False
