"""Orchestrates one run: event → ICP → signals → accounts → contacts → job file."""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config
from .enrich import resolve_contacts
from .enrich.domains import resolve_domains
from .events import fetch_event_details, get_event
from .icp import derive_icp
from .scoring import apply_company_profiles, explain_accounts, score_accounts
from .signals import bse, jobs, linkedin_free, linkedin_posts, news, regulators, seeds
from .store import load_job, save_job


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Job:
    def __init__(self, event_id: str):
        self.data = {
            "id": uuid.uuid4().hex[:10],
            "event_id": event_id,
            "event_title": "",
            "status": "queued",
            "stage": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "log": [],
            "counts": {},
            "event": {},
            "icp": {},
            "accounts": [],
            "people": [],
            "lead_count": 0,
            "error": "",
        }
        save_job(self.data)

    @property
    def id(self) -> str:
        return self.data["id"]

    def log(self, msg: str, stage: str | None = None) -> None:
        self.data["log"].append({"t": _now(), "msg": msg})
        if stage:
            self.data["stage"] = stage
        self.data["updated_at"] = _now()
        save_job(self.data)

    def run(self) -> None:
        d = self.data
        try:
            d["status"] = "running"
            ev = get_event(d["event_id"])
            if not ev:
                raise ValueError(f"unknown event id {d['event_id']}")
            self.log(f"Reading event page: {ev['title']}", "event")
            event = fetch_event_details(ev)
            d["event"] = event
            d["event_title"] = event["title"]

            self.log("Deriving ICP + search plan with Claude", "icp")
            icp = derive_icp(event)
            d["icp"] = icp
            self.log(
                f"ICP: {len(icp['buyer_titles'])} buyer titles · {len(icp['news_queries'])} news queries · "
                f"{len(icp['job_queries'])} role queries · regulators {icp['regulators']}",
                "icp",
            )

            # --- collectors (network-bound → run in parallel) ---
            self.log("Collecting signals: news, regulators, BSE, LinkedIn jobs, LinkedIn posts", "collect")
            signals = []
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = {
                    "news": ex.submit(self._news, event, icp),
                    "regulators": ex.submit(regulators.collect, event["title"], icp, self.log),
                    "bse": ex.submit(bse.collect, icp, self.log),
                    "jobs": ex.submit(jobs.collect, event["title"], icp, self.log),
                    "linkedin": ex.submit(linkedin_posts.collect, event["title"], icp, self.log),
                    "linkedin_free": ex.submit(linkedin_free.collect, event["title"], icp, self.log),
                }
                for name, f in futs.items():
                    try:
                        got = f.result()
                    except Exception as exc:
                        self.log(f"{name} collector failed: {exc}")
                        got = []
                    d["counts"][name] = len(got)
                    signals.extend(got)
            signals.extend(seeds.collect(icp))
            d["counts"]["total_signals"] = len(signals)
            d["counts"]["linkedin"] = d["counts"].get("linkedin", 0) + d["counts"].pop("linkedin_free", 0)
            d["people"] = _people(signals)
            d["counts"]["people"] = len(d["people"])
            self.log(f"{len(signals)} raw signals collected", "score")

            # --- score + explain ---
            accounts = score_accounts(signals)
            self.log(f"{len(accounts)} accounts with live signals; sizing listed companies via BSE", "score")
            apply_company_profiles(accounts, icp, progress=self.log)
            accounts = accounts[: config.MAX_ACCOUNTS_STORED]
            for a in accounts:
                a["job_id"] = d["id"]
            d["accounts"] = accounts
            d["lead_count"] = len(accounts)
            self.log(f"{len(accounts)} accounts scored; writing call notes for top {config.TOP_ACCOUNTS_TO_ENRICH}", "explain")
            explain_accounts(accounts, event, icp, progress=self.log)
            save_job(d)

            # --- contacts ---
            self.log("Resolving decision-maker contacts", "contacts")
            top = accounts[: config.TOP_ACCOUNTS_TO_ENRICH]
            doms = resolve_domains([a["company"] for a in top])
            for a in top:
                a["domain"] = doms.get(a["company_key"], {}).get("domain", "")
                a["linkedin_company"] = doms.get(a["company_key"], {}).get("linkedin", "")
            self.log(f"domains resolved for {sum(1 for a in top if a['domain'])}/{len(top)} top accounts")
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(lambda a: resolve_contacts(a, icp, self.log), top))
            resolved = sum(1 for a in top if a["contacts"])
            self.log(f"contacts: {resolved}/{len(top)} top accounts have named contacts", "done")
            d["status"] = "done"
            self.log("Run complete", "done")
        except Exception as exc:
            d["status"] = "error"
            d["error"] = f"{exc}\n{traceback.format_exc()[-1500:]}"
            self.log(f"ERROR: {exc}", "error")

    def _news(self, event: dict, icp: dict):
        items = news.fetch_news(icp.get("news_queries", []))
        self.log(f"Google News (IN): {len(items)} unique items across {len(icp.get('news_queries', []))} queries")
        return news.extract_signals(items, event["title"], icp, progress=self.log)


def _people(signals) -> list[dict]:
    """Every named person across signals (engagers, new appointees) — the person-level view."""
    seen: set[str] = set()
    out: list[dict] = []
    for s in sorted(signals, key=lambda x: (-x.relevance, x.date)):
        if not s.person_name:
            continue
        k = (s.person_url or s.person_name.lower()) + "|" + s.company_key
        if k in seen:
            continue
        seen.add(k)
        out.append(
            {
                "name": s.person_name, "title": s.person_title, "company": s.company, "company_key": s.company_key,
                "url": s.person_url, "how": s.title, "reason": s.reason, "date": s.date, "source": s.source,
                "evidence_url": s.url, "relevance": s.relevance, "comment": s.extra.get("comment", ""),
            }
        )
    return out


def start_job(event_id: str) -> str:
    job = Job(event_id)
    t = threading.Thread(target=job.run, daemon=True, name=f"job-{job.id}")
    t.start()
    return job.id


def attach_clay_contacts(job_id: str, company_key: str, contacts: list[dict]) -> bool:
    from .enrich import cached

    cached.save(company_key, contacts, source="clay")
    job = load_job(job_id)
    if not job:
        return False
    for a in job["accounts"]:
        if a["company_key"] == company_key:
            a["contacts"] = contacts
            a["contact_status"] = "clay"
    save_job(job)
    return True
