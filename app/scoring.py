"""Aggregate signals into accounts and score them 0-100 with an explanation."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from . import config
from .llm import complete_json
from .signals.base import Signal
from .signals.company_size import industry_fit, profiles_for

HALF_LIFE_DAYS = 21.0
_WEAK_ONLY = {"category_buyer", "regulatory_change"}


def _recency(d: str) -> float:
    try:
        age = (date.today() - datetime.strptime(d[:10], "%Y-%m-%d").date()).days
    except Exception:
        return 0.7
    if age < 0:
        age = 0
    return 0.5 ** (age / HALF_LIFE_DAYS)


def score_accounts(signals: list[Signal]) -> list[dict]:
    """Noisy-OR combination: independent signals compound, capped at 100."""
    by_key: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        if s.company_key:
            by_key[s.company_key].append(s)

    accounts: list[dict] = []
    for key, sigs in by_key.items():
        kinds = {s.kind for s in sigs}
        if kinds <= _WEAK_ONLY:
            continue  # seed / rule-change alone is not a lead
        # de-dupe near-identical signals (same source+title)
        uniq: dict[str, Signal] = {}
        for s in sigs:
            uniq.setdefault(f"{s.source}|{s.title[:60]}|{s.person_url}", s)
        sigs = list(uniq.values())

        # Diminishing returns within one (source, kind): the 5th "order win" adds little,
        # whereas a fine + a hiring signal + an engaged person compound fully.
        p_none = 1.0
        seen_kind: Counter = Counter()
        for s in sorted(sigs, key=lambda x: -x.weight * x.relevance):
            k = (s.source, s.kind)
            damp = 0.5 ** seen_kind[k]
            seen_kind[k] += 1
            p = min(0.97, s.weight * max(0.0, min(1.0, s.relevance)) * (0.5 + 0.5 * _recency(s.date)) * damp)
            p_none *= 1 - p
        score = round(100 * (1 - p_none))

        names = Counter(s.company for s in sigs)
        display = names.most_common(1)[0][0]
        people = [
            {"name": s.person_name, "title": s.person_title, "url": s.person_url, "how": s.title, "post": s.url}
            for s in sigs if s.person_name
        ]
        dates = sorted((s.date for s in sigs if s.date), reverse=True)
        accounts.append(
            {
                "company_key": key,
                "company": display,
                "score": score,
                "signal_kinds": sorted(kinds - _WEAK_ONLY) + sorted(kinds & _WEAK_ONLY),
                "signal_count": len(sigs),
                "latest_signal": dates[0] if dates else "",
                "location": next((s.location for s in sigs if s.location), ""),
                "signals": [s.to_dict() for s in sorted(sigs, key=lambda x: (-x.weight * x.relevance, x.date), reverse=False)],
                "engaged_people": people,
                "why": "",
                "best_persona": "",
                "angle": "",
                "contacts": [],
                "contact_status": "pending",
                "profile": {},
                "raw_score": score,
                "scrip_code": next((s.extra.get("scrip_code") for s in sigs if s.extra.get("scrip_code")), ""),
            }
        )
    accounts.sort(key=lambda a: (-a["score"], -a["signal_count"], a["company"]))
    return accounts


def apply_company_profiles(accounts: list[dict], icp: dict, limit: int = 250, progress=None) -> None:
    """Pull BSE group/index/industry for listed accounts and adjust scores for size + industry fit.

    A ₹30-crore SME that just appointed a Company Secretary is a weaker lead than a BSE-500
    company doing the same; an order win only matters for a contracts workshop if the winner
    builds things. Non-listed accounts (news, jobs, LinkedIn) keep their raw score.
    """
    listed = [a for a in accounts[:limit] if a.get("scrip_code")]
    if not listed:
        return
    profiles = profiles_for([a["scrip_code"] for a in listed])
    kws = icp.get("bse_industry_keywords", []) or []
    for a in listed:
        prof = profiles.get(a["scrip_code"], {})
        if not prof.get("group"):
            continue
        a["profile"] = {k: prof.get(k, "") for k in ("group", "index", "industry", "industry_group", "sector", "size_label")}
        mult = prof.get("size_multiplier", 1.0)
        fit = industry_fit(prof, kws)
        if fit is not None:
            mult *= fit
        a["score"] = max(1, min(100, round(a["raw_score"] * mult)))
        a["profile"]["adjustment"] = round(mult, 2)
    accounts.sort(key=lambda a: (-a["score"], -a["signal_count"], a["company"]))
    if progress:
        progress(f"company profiles: {len(profiles)} listed accounts sized via BSE (group/index/industry)")


_WHY_SYSTEM = """You are preparing call notes for a delegate-sales team that sells seats at a paid compliance
workshop. For each account, write:
- why: 1-2 plain sentences (<= 45 words) a caller can say, citing the concrete signals with dates. No hype.
- best_persona: the single job title at this company to call first (choose from the buyer titles).
- angle: <= 15 words — the selling angle for that persona (cost / time / penalty avoidance / ROI / claims exposure)."""


def _explain_batch(batch: list[dict], event: dict, icp: dict) -> list:
    blocks = []
    for i, a in enumerate(batch):
        lines = "; ".join(
            f"{s['kind_label']} ({s['date'] or 'n/d'}): {s['reason'] or s['title'][:90]}" for s in a["signals"][:5]
        )
        size = a.get("profile", {}).get("size_label", "")
        blocks.append(f"[{i}] {a['company']}{' (' + size + ')' if size else ''} — {lines}")
    user = f"""WORKSHOP: {event['title']} ({event.get('date_venue', '')})
BUYER TITLES: {', '.join(icp.get('buyer_titles', [])[:10])}
PERSONA ANGLES: {icp.get('persona_angles', {})}

ACCOUNTS:
""" + "\n".join(blocks) + """

Return JSON array: [{"i": <index>, "why": "...", "best_persona": "...", "angle": "..."}]"""
    rows = complete_json(_WHY_SYSTEM, user, model="default", max_tokens=3000)
    return rows if isinstance(rows, list) else []


def explain_accounts(accounts: list[dict], event: dict, icp: dict, limit: int | None = None, progress=None) -> None:
    limit = limit or config.TOP_ACCOUNTS_TO_ENRICH
    todo = [a for a in accounts[:limit]]
    batches = [todo[s : s + 10] for s in range(0, len(todo), 10)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_explain_batch, b, event, icp) for b in batches]
    done = 0
    for batch, fut in zip(batches, futures):
        try:
            rows = fut.result()
        except Exception as exc:
            if progress:
                progress(f"explanation batch failed: {exc}")
            continue
        for row in rows:
            try:
                a = batch[int(row["i"])]
            except Exception:
                continue
            a["why"] = row.get("why", "")
            a["best_persona"] = row.get("best_persona", "")
            a["angle"] = row.get("angle", "")
        done += len(batch)
    if progress:
        progress(f"call notes written for {done}/{len(todo)} accounts")
