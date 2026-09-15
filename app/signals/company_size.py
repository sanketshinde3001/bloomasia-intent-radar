"""Company quality proxy for listed companies via BSE's header API.

BSE group (A/B/T/X/XT/M/MT) + index membership tells us whether an account is a
BSE-500 corporate (will pay ₹25k a seat) or a ₹30-crore SME (will not). Industry text
lets us check the fit for the event (an "order win" only matters for FIDIC if the
company builds things).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx

from .. import config
from ..store import cache_get, cache_put

API = "https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w"
_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}
_BIG_INDEXES = ("SENSEX", "BSE 100", "BSE 200", "BSE 500", "BSE 250", "BSE 150", "BSE MidCap", "BSE LargeCap")


def size_multiplier(profile: dict) -> float:
    group = (profile.get("group") or "").strip().upper()
    index = profile.get("index") or ""
    if any(ix.lower() in index.lower() for ix in _BIG_INDEXES):
        return 1.1
    if group == "A":
        return 1.0
    if group == "B":
        return 0.7
    if group in ("M", "MT", "XT", "X", "T", "Z", "ZP", "ZY", "P", "IP"):
        return 0.35
    return 0.6


def size_label(profile: dict) -> str:
    m = size_multiplier(profile)
    if m >= 1.1:
        return "Large (index constituent)"
    if m >= 1.0:
        return "Large (Group A)"
    if m >= 0.7:
        return "Mid/Small (Group B)"
    return "Micro / SME"


def bse_profile(scrip_code: str | int) -> dict:
    code = str(scrip_code)
    hit = cache_get("bse_profile", code)
    if hit is not None:
        return hit
    prof: dict = {"scrip_code": code}
    try:
        r = httpx.get(API, params={"quotetype": "EQ", "scripcode": code, "seriesid": ""}, headers=_HEADERS, timeout=20)
        if r.status_code == 200 and r.text.startswith("{"):
            j = r.json()
            prof.update(
                {
                    "group": j.get("Group", ""),
                    "index": j.get("Index", ""),
                    "industry": j.get("Industry", "") or j.get("ISubGroup", ""),
                    "industry_group": j.get("IGroup", "") or j.get("IndustryNew", ""),
                    "sector": j.get("Sector", ""),
                    "security_id": j.get("SecurityId", ""),
                }
            )
    except Exception:
        pass
    prof["size_label"] = size_label(prof) if prof.get("group") else ""
    prof["size_multiplier"] = size_multiplier(prof) if prof.get("group") else 1.0
    cache_put("bse_profile", code, prof)
    return prof


def profiles_for(scrip_codes: list[str]) -> dict[str, dict]:
    codes = [c for c in dict.fromkeys(scrip_codes) if c]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(bse_profile, codes))
    return {p["scrip_code"]: p for p in results}


def industry_fit(profile: dict, industry_keywords: list[str]) -> float | None:
    """1.0 if BSE industry text matches the event's industry keywords, 0.45 if clearly not, None if unknown."""
    text = " ".join([profile.get("industry", ""), profile.get("industry_group", ""), profile.get("sector", "")]).lower()
    if not text.strip() or not industry_keywords:
        return None
    return 1.0 if any(k.lower() in text for k in industry_keywords) else 0.45
