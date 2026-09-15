"""Turn an event page into an Ideal Customer Profile + search plan (LLM)."""
from __future__ import annotations

from .llm import complete_json

# BSE announcement sub-categories that behave as intent triggers. The LLM picks
# the relevant ones for the event; bse.py queries exactly these strings.
BSE_SUBCATEGORIES = [
    "Award of Order / Receipt of Order",                 # EPC / contracts / claims
    "Appointment of Company Secretary / Compliance Officer",  # LODR / RPT / governance (new in seat)
    "Resignation of Company Secretary / Compliance Officer",
    "Change in Management",
    "Change in Directorate",
    "Acquisition",
    "Raising of Funds",
    "Updates - Corporate Insolvency Resolution Process  (CIRP)",
    "Open Offer - Updates",
    "Credit Rating",
]

_SYSTEM = """You are a B2B sales strategist for Bloomasia, a Mumbai-based organiser of paid,
in-person compliance / regulatory / contracts training workshops for senior managers at Indian
corporates (typical delegate: CFO, Company Secretary, CHRO, General Counsel, Head Contracts,
Head Compliance, Plant HR). Delegates pay per seat; each workshop needs 20-50 delegates from a
very specific job role.

Given one event, produce a precise Ideal Customer Profile and a concrete search plan that a
pipeline will execute against: Google News (India), RBI/SEBI press releases and orders, BSE
corporate announcements, LinkedIn job postings, and LinkedIn posts.

Rules:
- buyer_titles: 8-14 exact job titles as they appear on Indian LinkedIn profiles, most senior first.
- industries: 5-10 industries most likely to send delegates.
- news_queries: 12-16 SHORT Google News searches (3-6 words each, the way a person types into the news
  search box — long sentences return nothing) that surface a COMPANY that just became a prospect:
  penalties, orders, disputes, strikes, layoffs, breaches, contract wins, IPO filings, plant expansions,
  senior appointments. Mostly national ("factory workers strike", "EPFO notice employer", "layoffs India",
  "new CHRO appointed"), 2-3 with the event city/state. Never include "training" or "workshop".
- job_queries: 4-6 short LinkedIn job-search strings for roles whose hiring signals the company is
  staffing this problem (e.g. "labour law compliance manager").
- linkedin_keywords: 4-6 keyword phrases people post/engage about on LinkedIn for this topic.
- regulators: subset of ["SEBI", "RBI", "MCA", "Labour Ministry", "MeitY", "IRDAI", "NHAI"] relevant.
- bse_subcategories: choose ONLY from the provided list, and ONLY when the filing is a DIRECT trigger for this
  exact topic (be strict — a wrong choice floods the pipeline with irrelevant listed companies):
    "Award of Order / Receipt of Order"            → only contracts / EPC / FIDIC / claims / tendering / procurement events
    "Appointment/Resignation of Company Secretary" → only SEBI LODR / insider trading / RPT / board governance events
    "Change in Management", "Change in Directorate", "Acquisition", "Raising of Funds", "Open Offer - Updates"
                                                   → only board governance / M&A / SEBI disclosure events
    "Updates - CIRP"                               → only insolvency / IBC / banking-finance events
    HR / labour / safety / data-privacy / pharma / energy / AI events → [] (use bse_keywords instead)
- bse_keywords: 3-8 lowercase keywords that would appear in an exchange filing ONLY when the company has this
  exact problem (labour: "strike", "lockout", "retrenchment", "layoff", "workmen", "wage settlement";
  governance: "penalty", "non-compliance", "show cause"; contracts: "arbitration", "claim", "dispute").
  Never include generic corporate words like "acquisition", "expansion" or "merger" unless the event is about them.
- bse_industry_keywords: 6-12 lowercase substrings that would appear in a stock exchange's industry/sector
  classification for companies that fit this event (e.g. for FIDIC: "construction", "infrastructure",
  "engineering", "power", "capital goods", "realty", "cement"; for SEBI LODR: leave broad — [] means every listed company fits).
- trigger_kinds: subset of ["regulatory_penalty","corporate_event","news_trigger","hiring","engagement","job_change"].
- persona_angles: map of buyer function -> one-line selling angle, following Bloomasia's rule:
  CFO/finance = cost reduction & ROI; department heads = time saving; HR = penalty avoidance & smooth transition;
  legal/CS/compliance = regulatory exposure & board comfort; contracts/projects = money at stake in claims.
- cities: 3-6 Indian cities to prioritise (event city first if known).
"""


def derive_icp(event: dict) -> dict:
    user = f"""EVENT TITLE: {event['title']}
DATE / VENUE: {event.get('date_venue') or 'not stated'}
EVENT PAGE TEXT (truncated):
{event.get('description', '')[:3000]}

AVAILABLE BSE SUB-CATEGORIES: {BSE_SUBCATEGORIES}

Return JSON with keys: topic, one_line_pitch, buyer_titles, buyer_functions, industries, company_types,
cities, news_queries, job_queries, linkedin_keywords, regulators, bse_subcategories, bse_keywords,
bse_industry_keywords, trigger_kinds, persona_angles, why_now (2-3 sentences on the regulatory context that makes this urgent in 2026)."""
    icp = complete_json(_SYSTEM, user, max_tokens=3000)
    # defensive defaults so downstream never KeyErrors
    for k, default in (
        ("buyer_titles", []), ("buyer_functions", []), ("industries", []), ("company_types", []),
        ("cities", ["Mumbai", "Delhi", "Bengaluru", "Pune"]), ("news_queries", []), ("job_queries", []),
        ("linkedin_keywords", []), ("regulators", []), ("bse_subcategories", []), ("bse_keywords", []),
        ("bse_industry_keywords", []),
        ("trigger_kinds", ["regulatory_penalty", "corporate_event", "news_trigger", "hiring"]),
        ("persona_angles", {}), ("topic", event["title"]), ("one_line_pitch", ""), ("why_now", ""),
    ):
        icp.setdefault(k, default)
    icp["bse_subcategories"] = [s for s in icp["bse_subcategories"] if s in BSE_SUBCATEGORIES]
    return icp
