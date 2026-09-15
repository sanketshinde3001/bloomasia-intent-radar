"""Known buyers of this training category — client logos published by Bloomasia's
competitors (Princeton Academy, IOD, Inventicon, Synnex, UBS Forums, Bricsa …).

Used only as a score BOOST for accounts that already have a live signal; a seed alone
never becomes a lead.
"""
from __future__ import annotations

from .base import Signal, normalize_company

# theme keywords → companies (short names). Themes are matched against the ICP industries/topic text.
SEEDS: dict[str, list[str]] = {
    "general": [  # multi-topic corporate training buyers
        "Tata Motors", "Mahindra & Mahindra", "Yes Bank", "Indian Oil", "Wipro", "Godrej", "Adani Gas",
        "Ajanta Pharma", "Dr. Reddy's", "Cipla", "Dabur", "Pfizer", "Essar Steel", "Kotak Mahindra Bank",
        "Honeywell", "Capgemini", "Genpact", "PNB MetLife", "SBI Card", "BNP Paribas", "Birla Group",
        "UTI", "Tanla", "Vodafone", "Coca-Cola",
    ],
    "infra": ["Delhi Metro Rail Corporation", "Bombardier Transportation", "Saipem", "KEC International", "L&T", "Tata Projects"],
    "bfsi": ["Yes Bank", "Kotak Mahindra Bank", "SBI Card", "PNB MetLife", "BNP Paribas", "Bandhan Bank", "IDFC First Bank"],
    "pharma": ["Dr. Reddy's", "Cipla", "Ajanta Pharma", "Pfizer", "Dabur"],
}

_THEME_HINTS = {
    "infra": ("epc", "fidic", "construction", "contract", "claims", "infrastructure", "tender", "procurement"),
    "bfsi": ("rbi", "bank", "nbfc", "fraud", "derivative", "isda", "sebi", "lodr", "related party"),
    "pharma": ("pharma", "gmp", "usfda"),
}


def collect(icp: dict) -> list[Signal]:
    text = " ".join([icp.get("topic", ""), " ".join(icp.get("industries", [])), " ".join(icp.get("company_types", []))]).lower()
    themes = ["general"] + [t for t, hints in _THEME_HINTS.items() if any(h in text for h in hints)]
    out: list[Signal] = []
    seen: set[str] = set()
    for t in themes:
        for name in SEEDS.get(t, []):
            k = normalize_company(name)
            if k in seen:
                continue
            seen.add(k)
            out.append(
                Signal(
                    source="seed", kind="category_buyer", company=name,
                    title="Listed as a client by a competing training organiser",
                    url="", date="", relevance=0.6, reason="Known buyer of compliance/executive training",
                )
            )
    return out
