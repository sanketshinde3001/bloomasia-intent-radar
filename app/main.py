"""FastAPI server: JSON API + the dashboard (web/index.html)."""
from __future__ import annotations

import csv
import io

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import config
from .enrich import cached
from .events import list_events
from .outreach import generate
from .pipeline import attach_clay_contacts, start_job
from .store import cache_get, cache_key, cache_put, list_jobs, load_job

app = FastAPI(title="Bloomasia Intent Radar", version="0.1")


class RunRequest(BaseModel):
    event_id: str


class ClayCallback(BaseModel):
    secret: str = ""
    job_id: str
    company_key: str
    contacts: list[dict]


class ManualContacts(BaseModel):
    company: str
    contacts: list[dict]
    job_id: str | None = None


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "llm": config.llm_available(),
        "provider": config.LLM_PROVIDER,
        "model": config.OPENAI_MODEL if config.LLM_PROVIDER == "openai" else config.LLM_MODEL,
        "linkedin_free": config.LINKEDIN_FREE_LANE and not config.APIFY_TOKEN,
        "apify": bool(config.APIFY_TOKEN),
        "apollo": bool(config.APOLLO_API_KEY),
        "clay_webhook": bool(config.CLAY_WEBHOOK_URL),
        "lookback_days": config.LOOKBACK_DAYS,
    }


@app.get("/api/events")
def events(refresh: bool = False):
    return list_events(force=refresh)


@app.post("/api/run")
def run(req: RunRequest):
    if not config.llm_available():
        key = "OPENAI_API_KEY" if config.LLM_PROVIDER == "openai" else "ANTHROPIC_API_KEY"
        raise HTTPException(400, f"{key} missing — add it to .env and restart")
    return {"job_id": start_job(req.event_id)}


@app.get("/api/jobs")
def jobs():
    return list_jobs()


@app.get("/api/jobs/{job_id}")
def job(job_id: str, full: bool = False):
    j = load_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if full:
        return j
    slim = {k: v for k, v in j.items() if k != "accounts"}
    slim["accounts"] = [
        {k: v for k, v in a.items() if k != "signals"} | {"signals": a["signals"][:3]} for a in j["accounts"]
    ]
    return slim


@app.get("/api/jobs/{job_id}/accounts/{company_key}")
def account(job_id: str, company_key: str):
    j = load_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    for a in j["accounts"]:
        if a["company_key"] == company_key:
            return a
    raise HTTPException(404, "account not found")


@app.post("/api/jobs/{job_id}/accounts/{company_key}/outreach")
def outreach(job_id: str, company_key: str, contact_index: int = -1):
    j = load_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    lead = next((a for a in j["accounts"] if a["company_key"] == company_key), None)
    if not lead:
        raise HTTPException(404, "account not found")
    contact = lead["contacts"][contact_index] if 0 <= contact_index < len(lead["contacts"]) else None
    key = cache_key("outreach", job_id, company_key, contact_index)
    hit = cache_get("outreach", key)
    if hit:
        return hit
    out = generate(lead, contact, j["event"], j["icp"])
    cache_put("outreach", key, out)
    return out


@app.get("/api/jobs/{job_id}/export.csv")
def export_csv(job_id: str):
    j = load_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "score", "company", "signals", "latest_signal", "why", "best_persona", "angle",
                "contact_name", "contact_title", "contact_email", "contact_phone", "contact_linkedin", "evidence_urls"])
    for i, a in enumerate(j["accounts"], 1):
        contacts = a["contacts"] or [{}]
        for c in contacts:
            w.writerow([
                i, a["score"], a["company"], "; ".join(a["signal_kinds"]), a["latest_signal"], a["why"], a["best_persona"], a["angle"],
                c.get("name", ""), c.get("title", ""), c.get("email", ""), c.get("phone", ""), c.get("linkedin_url", ""),
                " | ".join(s["url"] for s in a["signals"][:3] if s["url"]),
            ])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=intent-leads-{job_id}.csv"})


@app.post("/api/clay/callback")
def clay_callback(cb: ClayCallback):
    if config.CLAY_CALLBACK_SECRET and cb.secret != config.CLAY_CALLBACK_SECRET:
        raise HTTPException(403, "bad secret")
    ok = attach_clay_contacts(cb.job_id, cb.company_key, cb.contacts)
    return {"ok": ok}


@app.post("/api/contacts")
def manual_contacts(body: ManualContacts):
    """Store pre-enriched contacts for a company (e.g. exported from Clay) and attach to a job."""
    key = cached.save_for_company(body.company, body.contacts, source="manual")
    if body.job_id:
        attach_clay_contacts(body.job_id, key, body.contacts)
    return {"ok": True, "company_key": key}
