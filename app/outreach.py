"""Persona-tailored outreach for one lead (email + WhatsApp + call opener)."""
from __future__ import annotations

from .llm import complete_json

_SYSTEM = """You write outreach for Bloomasia Incorporated (Mumbai), which runs paid, in-person compliance
workshops for senior managers at Indian corporates. The sender is Azhar Khan, Managing Partner.

House rules (from Azhar):
- CFO / finance persona → lead with cost reduction and return on investment.
- Department heads (HR head, Contracts head, Plant head) → lead with time saved / avoiding rework.
- Legal / Company Secretary / Compliance → lead with regulatory exposure and board comfort.
- HR for Labour Codes → penalty avoidance and smooth transition for the workforce.
- Always open with the specific trigger (what happened at THEIR company and when) — that is the whole point.
- Indian business English, warm but direct, no fluff, no exclamation marks, no emojis in email.
- Email: subject <= 8 words; body <= 120 words; one clear ask (a 10-minute call or a reply). Sign as Azhar Khan, Bloomasia.
- WhatsApp: <= 60 words, first name basis, one line about the trigger, one line about the workshop, ask permission to send brochure.
- Call opener: <= 45 words, what a caller says in the first 20 seconds, referencing the trigger, then a permission question."""


def generate(lead: dict, contact: dict | None, event: dict, icp: dict) -> dict:
    persona = (contact or {}).get("title") or lead.get("best_persona") or "Decision maker"
    name = (contact or {}).get("name") or ""
    sig_lines = "\n".join(
        f"- {s['kind_label']} ({s['date'] or 'n/d'}): {s['reason'] or s['title'][:100]}" for s in lead["signals"][:4]
    )
    user = f"""WORKSHOP: {event['title']}
WHEN / WHERE: {event.get('date_venue') or 'dates on request'}
PITCH: {icp.get('one_line_pitch', '')}
WHY NOW: {icp.get('why_now', '')}

LEAD: {lead['company']}
PERSON: {name or '(name unknown)'} — {persona}
ANGLE FOR THIS PERSONA: {lead.get('angle') or icp.get('persona_angles', {}).get(persona, '')}
TRIGGERS:
{sig_lines}

Return JSON: {{"persona_angle": "...", "email_subject": "...", "email_body": "...", "whatsapp": "...", "call_opener": "..."}}"""
    return complete_json(_SYSTEM, user, max_tokens=1200, temperature=0.2)
