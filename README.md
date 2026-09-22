# GroundControl

**An operations copilot for Riverside Grounds** — a landscaping and grounds-maintenance company
that turns inbound plain-English job-request emails into priced, human-approved quotes.

> Built as a Forward Deployed Engineer portfolio engagement. Full writeup: [`CASE_STUDY.md`](./CASE_STUDY.md).
> Build plan and phase status: [`PLAN.md`](./PLAN.md).

---

## The problem

Riverside Grounds runs ~8 crews on roughly $2M/year. Every job request arrives as a plain-English
email or a forwarded voicemail. The office manager reads each one by hand, checks whether the
customer already exists, hand-builds a quote in a spreadsheet, and emails it back. Turnaround is
1–2 days. They lose jobs to faster competitors and make pricing errors under time pressure.

GroundControl drafts the priced quote in minutes — but **never sends anything to a customer, and
never commits a number, without a human clicking approve**.

---

## Quick start

```bash
cp .env.example .env        # then add your ANTHROPIC_API_KEY
docker compose up           # postgres + api + web, migrations run on boot
```

- API docs → http://localhost:8000/docs
- Web UI → http://localhost:3000

Then:

```bash
make seed        # load fixtures into a coherent demo state
make test        # test suite — deterministic, no API calls, free
make eval        # eval harness on a 20-case subset
make eval-full   # eval harness on the full dataset
```

**On Windows without GNU Make**, use the shim — identical targets:

```powershell
.\make.ps1 seed
.\make.ps1 test
```

Run `make help` (or `.\make.ps1 help`) for the full target list.

---

## Status

Phase 0 complete — scaffold and one-command run. See [`PLAN.md`](./PLAN.md) for the phase checklist.

<!-- HEADLINE_RESULT: populated at Phase 6 from real eval-harness output. -->
