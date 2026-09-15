# Bloomasia Intent Radar

Demo for Azhar Khan's acceptance test: **"Go to bloomasia.in events, choose any one training,
show me intent leads for it."**

Pick a live event from bloomasia.in → the app derives the buyer profile, pulls India-specific
intent signals from the last 45 days, scores every account, names the person to call, and drafts
the persona-tailored email / WhatsApp / call opener — with the evidence link on every lead.

## Why this beats ZoomInfo / Apollo intent for Bloomasia

| | ZoomInfo / Apollo (Bombora) | Intent Radar |
|---|---|---|
| Signal source | US-heavy publisher co-op, IP→company | SEBI/RBI orders, BSE filings, Indian business news, LinkedIn jobs & posts |
| Granularity | Account only | Account **and person** (new CS appointed, who commented on the topic) |
| Topics | Generic taxonomy ("HR software") | Bloomasia's actual events (Labour Codes, LODR/RPT, FIDIC, DPDP …) |
| Explainability | Black-box surge score | Every lead shows *what happened, when, link* + call notes |
| Cost | $25k+/yr | API costs only (LLM pennies; Apify/Clay optional) |

## Signal lanes

| Lane | Source | Signal kind | Notes |
|---|---|---|---|
| News | Google News RSS (India edition), 8–12 LLM-written queries per event | regulator action, corporate event, news trigger, appointment | LLM extracts company, kind, relevance, reason |
| Regulators | RBI press-release RSS, SEBI RSS | regulator action | penalties / orders naming an entity |
| Exchange filings | BSE announcements API | order wins (FIDIC/EPC), new Company Secretary (LODR/RPT), CIRP, acquisitions | company size via BSE group/index; industry fit |
| Hiring | LinkedIn public job search | hiring for the role | LLM relevance pass removes noise |
| LinkedIn engagement | Apify (harvestapi no-cookie actors) **or** free lane (DuckDuckGo + public post pages) | engaged on LinkedIn (person-level) | free lane is best-effort (rate-limited) |
| Category buyers | competitor client logos | boost only | never a lead on its own |

Scoring = noisy-OR of `weight × relevance × recency` per signal, capped at 100, then adjusted
for company size (BSE group / index) and industry fit. Seeds and industry-wide rule changes
never create a lead alone.

## Run

```powershell
cd E:\FDE\bloomasia-intent-radar
copy .env.example .env      # then fill in OPENAI_API_KEY (or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic)
.\run.ps1                   # http://127.0.0.1:8000
```

`.venv` already exists; recreate with `python -m venv .venv; .venv\Scripts\pip install -r requirements.txt`.

## Demo runbook (second call with Azhar)

1. `.\run.ps1`, open http://127.0.0.1:8000. Caches for the two demo events are warm (re-run ≈ 20 s; cold ≈ 2 min).
2. Pick **Mastering Labour Codes** → Find intent leads. Talk through the pipeline panel while it runs.
3. Show the ICP card (buyer titles / search plan derived from *his* event page), then the leads:
   TeamLease (EPFO ₹1,845 cr show-cause), Jindal (new CHRO named), NMDC wage settlement, M&M strike, hiring at Eaton/Flipkart/AdaniConneX.
4. Open a lead → evidence links → contact (name, title, email) → **Draft outreach** → email / WhatsApp / call opener.
5. Switch to **SEBI LODR** → REC / SJVN / NTPC / NALCO exchange fines, 160+ newly appointed Company Secretaries (person-level panel).
6. Export CSV. Ask Azhar to rate 20 random leads relevant / not.

## Contacts (decision-maker names, email, phone)

Three adapters, tried in order:

1. **cached** — `data/contacts/<company_key>.json` (pre-enriched, e.g. from Clay; or POST `/api/contacts`).
   Bulk-load with `scripts\import_contacts.py data\contacts\_clay_import.json [job_id ...]` — the demo accounts
   were enriched through BFWAI's Clay workspace (search-contacts by domain + Email enrichment) and live there.
2. **Apollo** — set `APOLLO_API_KEY` (paid plan needed for people search).
3. **Clay webhook** — set `CLAY_WEBHOOK_URL`; the app POSTs `{company, company_key, titles, cities, event, job_id, callback_url}`
   to a Clay table. In Clay: Find People at Company → Enrich Work Email → HTTP API step that POSTs
   `{"secret": CLAY_CALLBACK_SECRET, "job_id", "company_key", "contacts": [{name,title,linkedin_url,email,email_status,phone}]}`
   to `http://<host>:8000/api/clay/callback`. Contacts appear on the next dashboard refresh.

## API

- `GET /api/events` — live catalogue from bloomasia.in (`?refresh=1` to re-scrape)
- `POST /api/run {event_id}` → `{job_id}`; `GET /api/jobs/{id}` (poll), `?full=1` for all signals
- `GET /api/jobs/{id}/accounts/{company_key}` — one lead with all evidence
- `POST /api/jobs/{id}/accounts/{company_key}/outreach?contact_index=0` — email / WhatsApp / call opener
- `GET /api/jobs/{id}/export.csv`
- `POST /api/contacts {company, contacts[], job_id?}` — load pre-enriched contacts
- `POST /api/clay/callback` — Clay table callback

## Notes

- All external calls are cached under `data/cache/` (LLM calls by prompt hash) so a re-run for the
  same event on the same day is instant and free — warm the cache before a live demo.
- LinkedIn: only public job-search and public post pages are read; no member login is used.
  Profiles are never fetched (LinkedIn returns 999 to guests).
- BSE limits each query to ~30 days; the collector walks the look-back in 30-day windows.
