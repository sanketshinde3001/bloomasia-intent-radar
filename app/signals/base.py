"""Common signal model + company-name normalisation."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# kind → base weight used by scoring (0..1). Person-level and "must act" signals
# are the strongest; generic "in the news" is weakest.
KIND_WEIGHTS = {
    "regulatory_penalty": 0.60,   # SEBI/RBI/exchange fine or order naming the company
    "corporate_event": 0.40,      # IPO/DRHP, merger, order win, big contract, restructuring
    "news_trigger": 0.30,         # labour dispute, fraud case, breach, dispute/arbitration
    "hiring": 0.40,               # job post for the topic's role
    "engagement": 0.50,           # person engaged with topic content on LinkedIn
    "job_change": 0.35,           # new-in-seat decision maker
    "category_buyer": 0.15,       # known buyer of compliance training (competitor clients)
    "regulatory_change": 0.10,    # industry-wide rule change (context, weak per-company)
}

KIND_LABELS = {
    "regulatory_penalty": "Regulator action",
    "corporate_event": "Corporate event",
    "news_trigger": "News trigger",
    "hiring": "Hiring for the role",
    "engagement": "Engaged on LinkedIn",
    "job_change": "New in seat",
    "category_buyer": "Buys this category",
    "regulatory_change": "Rule change",
}

_SUFFIXES = (
    r"private limited|pvt\.? ltd\.?|pvt|ltd\.?|limited|llp|inc\.?|corp\.?|corporation|co\.?|company|"
    r"plc|group|holdings|india|\(india\)|\(i\)|technologies|technology|solutions|services|"
    r"industries|enterprises|international|global"
)


def normalize_company(name: str) -> str:
    """'Tata Motors Ltd.' → 'tata motors' — used to merge signals for one account."""
    s = name.lower().strip()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    # strip trailing legal suffixes repeatedly
    prev = None
    while prev != s:
        prev = s
        s = re.sub(rf"\b({_SUFFIXES})\s*$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s or name.lower().strip()


@dataclass
class Signal:
    source: str                    # news | rbi | sebi | bse | jobs_linkedin | jobs_naukri | linkedin_post | seed
    kind: str                      # one of KIND_WEIGHTS
    company: str                   # display name
    title: str                     # headline / job title / post text
    url: str
    date: str                      # ISO date (YYYY-MM-DD) best effort
    snippet: str = ""
    relevance: float = 0.7         # 0..1 how relevant to THIS event
    reason: str = ""               # one-line explanation from the extractor
    location: str = ""
    person_name: str = ""
    person_title: str = ""
    person_url: str = ""
    extra: dict = field(default_factory=dict)
    company_key: str = ""
    id: str = ""

    def __post_init__(self):
        self.company = re.sub(r"\s+", " ", (self.company or "").strip())
        self.company_key = normalize_company(self.company) if self.company else ""
        if not self.id:
            h = hashlib.sha1(f"{self.source}|{self.company_key}|{self.url}|{self.person_url}|{self.title}".encode()).hexdigest()
            self.id = h[:12]
        if not self.date:
            self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @property
    def weight(self) -> float:
        return KIND_WEIGHTS.get(self.kind, 0.2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["weight"] = self.weight
        d["kind_label"] = KIND_LABELS.get(self.kind, self.kind)
        return d


def iso_date(dt) -> str:
    """Best-effort conversion of feedparser/struct_time/datetime/str → YYYY-MM-DD."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if hasattr(dt, "tm_year"):
        return f"{dt.tm_year:04d}-{dt.tm_mon:02d}-{dt.tm_mday:02d}"
    s = str(dt)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:31], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return ""
