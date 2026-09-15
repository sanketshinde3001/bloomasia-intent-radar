"""Load pre-enriched contacts (e.g. exported from Clay) into the app's contact cache and
attach them to existing jobs.

Usage:  .venv\\Scripts\\python.exe scripts\\import_contacts.py data\\contacts\\_clay_import.json [job_id ...]

Input JSON: {"<Company name>": [{"name", "title", "linkedin_url", "email", "email_status", "phone", "city"}, ...]}
Company names are normalised the same way the pipeline does, so "REC" matches "REC Ltd".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.enrich import cached  # noqa: E402
from app.store import list_jobs, load_job, save_job  # noqa: E402


def main(path: str, job_ids: list[str]) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = {}
    for company, contacts in data.items():
        rows = [
            {
                "name": c.get("name", ""), "title": c.get("title", ""), "company": company,
                "linkedin_url": c.get("linkedin_url", ""), "email": c.get("email", ""),
                "email_status": c.get("email_status", "") or ("found" if c.get("email") else ""),
                "phone": c.get("phone", ""), "city": c.get("city", ""), "source": "clay",
            }
            for c in contacts
        ]
        keys[cached.save_for_company(company, rows, source="clay")] = rows
    print(f"cached contacts for {len(keys)} companies")

    targets = job_ids or [j["id"] for j in list_jobs()]
    for jid in targets:
        job = load_job(jid)
        if not job:
            continue
        n = 0
        for a in job["accounts"]:
            if a["company_key"] in keys:
                a["contacts"] = keys[a["company_key"]]
                a["contact_status"] = "clay"
                n += 1
        save_job(job)
        print(f"job {jid} ({job.get('event_title', '')[:40]}): attached contacts to {n} accounts")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2:])
