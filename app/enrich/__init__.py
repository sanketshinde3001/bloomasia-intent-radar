"""Contact resolution: account → named decision-makers with email/phone.

Adapters (tried in order):
  cached  – data/contacts/<company_key>.json (pre-enriched, e.g. via Clay)
  apollo  – Apollo.io people search + match (APOLLO_API_KEY)
  clay    – push to a Clay webhook table; Clay enriches and calls back /api/clay/callback (CLAY_WEBHOOK_URL)
"""
from __future__ import annotations

from . import apollo, cached, clay


def resolve_contacts(account: dict, icp: dict, progress=None) -> dict:
    """Mutates account['contacts'] / ['contact_status'] and returns the account."""
    hits = cached.lookup(account["company_key"])
    if hits:
        account["contacts"] = hits
        account["contact_status"] = "cached"
        return account

    if apollo.available():
        try:
            people = apollo.find_people(account["company"], icp.get("buyer_titles", []), domain=account.get("domain", ""))
            if people:
                account["contacts"] = people
                account["contact_status"] = "apollo"
                cached.save(account["company_key"], people, source="apollo")
                return account
        except Exception as exc:
            if progress:
                progress(f"apollo failed for {account['company']}: {exc}")

    if clay.available():
        try:
            clay.request(account, icp)
            account["contact_status"] = "clay_requested"
            return account
        except Exception as exc:
            if progress:
                progress(f"clay webhook failed for {account['company']}: {exc}")

    account["contact_status"] = "unresolved"
    return account
