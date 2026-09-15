"""Company name → primary website domain (LLM, cached). Needed by Clay/Apollo, which key on domains."""
from __future__ import annotations

from ..llm import complete_json
from ..store import cache_get, cache_put
from ..signals.base import normalize_company

_SYSTEM = """You map Indian company names to their primary corporate website domain (no scheme, no www),
e.g. "Larsen & Toubro Ltd" -> "larsentoubro.com", "REC" -> "recindia.nic.in", "NTPC" -> "ntpc.co.in".
Return "" when you are not confident (wrong domains are worse than blanks). For subsidiaries use the
subsidiary's own domain if it has one, else the parent's. Also give the LinkedIn company page slug if known
(e.g. "larsen-&-toubro-limited"), else ""."""


def resolve_domains(names: list[str]) -> dict[str, dict]:
    """{company_key: {domain, linkedin}} for each name; cached per company_key."""
    out: dict[str, dict] = {}
    todo: list[str] = []
    for n in names:
        k = normalize_company(n)
        hit = cache_get("domains", k)
        if hit is not None:
            out[k] = hit
        else:
            todo.append(n)
    for start in range(0, len(todo), 25):
        batch = todo[start : start + 25]
        lines = "\n".join(f"[{i}] {n}" for i, n in enumerate(batch))
        user = f"COMPANIES:\n{lines}\n\nReturn JSON array: [{{\"i\": <index>, \"domain\": \"...\", \"linkedin\": \"...\"}}]"
        try:
            rows = complete_json(_SYSTEM, user, model="default", max_tokens=2000)
        except Exception:
            rows = []
        got = {}
        for row in rows if isinstance(rows, list) else []:
            try:
                got[int(row["i"])] = {"domain": (row.get("domain") or "").strip().lower(), "linkedin": (row.get("linkedin") or "").strip()}
            except Exception:
                continue
        for i, n in enumerate(batch):
            k = normalize_company(n)
            val = got.get(i, {"domain": "", "linkedin": ""})
            cache_put("domains", k, val)
            out[k] = val
    return out
