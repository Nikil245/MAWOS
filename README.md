# MAWOS — an event-driven multi-agent workflow orchestration engine for universities

B.E. Final-Year Research Prototype · Dept. of AI&ML, MITE · Group 12

MAWOS models a **real institution** — 5 departments × 4 years × 2 sections
(1,200 students, 75 faculty, 100 subjects, 180,000 attendance records, an
admissions intake of 400 applicants) — and runs it through a **multi-agent
system**: currently ten agents, each owning an institutional domain,
coordinating over an instrumented event bus, behind a **confidence-gated
hybrid router** that calls role-scoped tools rather than answering from a
script.

The research contribution is the **orchestration engine**, not the agent
count: `intent → tool selection → permission-checked execution → grounded
answer` on the query path, and `attendance upload → attendance → {exam,
scholarship, placement, notification}` on the propagation path — where
**every hop is measured** under a workflow ID.

This is a live research project, not a finished paper. The **v3-research**
branch reframes v2's "LLM-first assistant" into a routing experiment with
pre-registered thresholds, held-out evaluation and an external benchmark.
§ [Research status](#research-status-v3) below says exactly what is done,
what is frozen, and what is still blocked — read it before citing any
number from this repository.

---

## Quickstart

Install backend and React dependencies once:

```bash
pip install -r requirements.txt
python ml/calibrate.py       # estimate distributions from the real UCI dataset
python ml/train.py           # generate calibrated data + train CART & RF
cd frontend && npm install
```

Create a local environment file before starting the service. Demo data is
disabled by default; set `MAWOS_SEED_DEMO_DATA=true` only for an explicitly
requested local demo dataset.

```bash
cp .env.example .env
# Set MAWOS_JWT_SECRET to a long random value before any production deployment.
set -a; source .env; set +a
```

MAWOS reads environment variables directly; load `.env` into the shell (as
above) or configure the same values through your process manager.

When demo seeding is explicitly enabled, the first launch seeds the institution.
Timetable configuration, draft generation and publication are explicit role-authorized
actions; startup never generates a timetable. See [Academic timetables](docs/TIMETABLE.md).

Run MAWOS in two terminals:

```bash
# Terminal 1 — backend API
source .venv/bin/activate
.venv/bin/python run.py
# API: http://127.0.0.1:8000
# Docs: http://127.0.0.1:8000/docs
```

```bash
# Terminal 2 — React website
cd frontend
npm run dev
# Website: http://127.0.0.1:5173
```

Port 8000 is the backend API only. Port 5173 is the React website; Vite
proxies its relative `/api` requests to the backend.

### PostgreSQL: existing MAWOS database

MAWOS uses Psycopg 3 for PostgreSQL. The populated `mawos` database is an
existing schema, not an application bootstrap target: startup validates it but
never creates tables, seeds users, or generates a timetable when PostgreSQL is
active. Use a dedicated non-superuser application role such as `mawos_app`.

Set the ignored local `.env` to use the explicit SQLAlchemy URL format:

```env
MAWOS_DATABASE_URL=postgresql+psycopg://mawos_app:replace-with-password@127.0.0.1:5432/mawos
MAWOS_SEED_DEMO_DATA=false
```

Verify the target before starting the API. This script opens an explicit
read-only transaction, checks `mawos.public`, compares all model metadata, and
prints safe row counts without printing credentials:

```bash
set -a; source .env; set +a
.venv/bin/python scripts/verify_postgresql.py
```

Take a full backup before any write operation, including Alembic stamping. Do
not overwrite an existing backup:

```bash
test ! -e mawos_before_app_switch.backup && pg_dump \
  -h 127.0.0.1 -U postgres -d mawos --format=custom \
  --file=mawos_before_app_switch.backup
```

After a successful verification and backup, start the backend using the loaded
environment. It reports `MAWOS database backend: postgresql` without logging
the connection URL.

```bash
set -a; source .env; set +a
.venv/bin/python run.py
```

Never run `pytest` against populated `mawos`. Unit tests force an isolated
SQLite database. PostgreSQL integration tests require an explicitly configured
separate `MAWOS_POSTGRES_TEST_URL` targeting `mawos_test`.

Timetable configuration can be previewed from the existing PostgreSQL academic
rows without writes. Weekly demand is never inferred unless explicitly enabled:

```bash
set -a; source .env; set +a
.venv/bin/python scripts/bootstrap_timetable_data.py --dry-run --term-id 1
```

See `docs/TIMETABLE.md` for the guarded apply options, placeholder-room warning,
and the HOD/Admin preview endpoint.

Alembic is configured with an empty baseline revision for the existing public
schema. After the read-only verification and confirmed backup, review it and
record the baseline only with:

```bash
set -a; source .env; set +a
.venv/bin/alembic -c alembic.ini stamp head
```

Do not run `alembic upgrade` for this baseline; `stamp` only adds Alembic
version tracking.

### Turning the LLM tier on

The router works with **no LLM at all** — the keyword lexicon is the
*primary* tier and answers ~90% of queries by itself. Ollama only adds the
escalation tier for the low-confidence remainder (see
[Routing](#routing-v3-a-confidence-gated-hybrid) below).

```bash
%LOCALAPPDATA%\Ollama\ollama.exe serve          # FIRST — leave running
%LOCALAPPDATA%\Ollama\ollama.exe pull qwen2.5:3b
.venv/bin/python run.py
```

For the local deployment, copy the Ollama entries from `.env.example` into
your ignored `.env`: `MAWOS_OLLAMA_HOST=http://127.0.0.1:11434`,
`MAWOS_OLLAMA_MODEL=qwen2.5:3b`, `MAWOS_OLLAMA_CONTEXT=2048`, and the bounded
output/round/concurrency values shown there. `MAWOS_OLLAMA_TIMEOUT` is one
complete assistant-turn deadline in seconds, covering health and every model
round; MAWOS does not multiply it. Ollama is checked on demand, never during
application startup, and a failed check is retried after the configured short
cooldown.

The configured runtime model is reported separately in API runtime metadata.
The frozen routing configuration and published research artifacts continue to
record `qwen2.5:3b-instruct`; they are historical evaluation metadata and are
not rewritten by local deployment configuration.

If `winget install Ollama.Ollama` downloads and then hangs (it blocks on a
UAC prompt that never appears in a non-interactive shell — and the partial
download can fail `Get-AuthenticodeSignature` with `HashMismatch` even
though it looks complete), skip the installer and use the portable build
instead — no admin rights required: download `ollama-windows-amd64.zip`
from the GitHub release, `unzip` it to `%LOCALAPPDATA%\Ollama`, verify the
signature, then run the two commands above.

### Demo accounts (five different portals, not five skins)

These accounts exist only after starting a development or test environment
with `MAWOS_SEED_DEMO_DATA=true`. They are never created automatically in
production.

| Role | Login | What that role can actually do |
|---|---|---|
| Student | `4MT23AI049` / `student123` | attendance & CIE marks, fees + pay, hall-ticket status, scholarship, placements, personal timetable + CSV download, notices, assistant |
| Faculty | `aiml.f02` / `faculty123` | own teaching assignments, **mark class attendance** (only for assigned subject-sections — enforced server-side), enter internal marks, own teaching timetable |
| HOD | `hod.aiml` / `faculty123` | department analytics, section-wise timetables, **configure, generate, validate and publish versioned department timetables**, fee-defaulter list |
| Principal | `principal` / `principal123` | institution-wide analytics: department comparison, fee collection, placements, admissions funnel |
| Admin | `admin` / `admin123` | **full admissions pipeline** (verify → merit rank → allot seats vs intake → enrol), demo cascade trigger |

### Private placement documents and lifecycle maintenance

Placement job-description PDFs are stored outside public/static assets. Local
development defaults to `runtime/placement-documents`; Docker mounts the
non-public `placement_documents` named volume at
`/var/lib/mawos/placement-documents`. Configure the limit with
`MAWOS_PLACEMENT_DOCUMENT_MAX_BYTES` (default 10 MiB). Back up PostgreSQL and
this document volume as one recovery set: PostgreSQL contains only opaque keys
and metadata, not the PDF bytes. Restore both to the same recovery point.

Schema migration and legacy status reconciliation are separate, explicit
operator actions. After a verified backup, migrate first, preview the repair,
then apply it. The repair changes only `OPEN` drives that already have
shortlist rows to `SHORTLIST_GENERATED` and writes an audit event; it is
idempotent and never runs at startup:

```bash
MAWOS_ENV=production MAWOS_DATABASE_MODE=external alembic upgrade 20260912_placement_details
python scripts/reconcile_placement_statuses.py --database-url "$MAWOS_DATABASE_URL"
python scripts/reconcile_placement_statuses.py --database-url "$MAWOS_DATABASE_URL" --apply --confirm-database mawos
```

**Do not run these live commands without a verified database and document-volume backup.**

Any USN from `4MT23AI001`–`4MT26CV6xx` works as a student login; faculty are
`{dept}.f02`…`{dept}.f15`; HODs are `hod.{dept}` for aiml/cse/ece/me/cv.

---

## Routing (v3): a confidence-gated hybrid

**Recaptured GPU-resident on the post-P2, 99-task/12-tool instrument
(2026-08-21, τ; 2026-08-25, full P6 sweep).** The numbers below are
current and citable for the 3B model. *Which* model (3B) gets tuned here
was picked by the P6 sweep, and as of 2026-08-25 that sweep is itself
recaptured against the same current instrument for all three sizes
(1.5B/3B/7B) — `analyze_sweep.py` reconfirms qwen2.5:3b-instruct as the
pick (McNemar vs 1.5B, p = 0.003), so τ-selection for the 3B rests on
fresh evidence end to end, not a stale sweep.

`backend/app/router.py` escalates to the LLM only when the lexicon's own
confidence is low — margin (top-1 score minus top-2 score) ≤ τ — not
whenever Ollama happens to be reachable. τ = 0 is frozen in
`backend/app/router_config.json` (sha256-hashed; hand-editing it makes it a
different experiment, see `PROTOCOL.md` §9.3) and was selected by a
pre-registered rule on the 108-query dev set **before** any held-out data
existed.

The lexicon is the **primary** tier — it handles the other ~90% of queries
unassisted. It is never called a "fallback" in this codebase.

The canonical chat response separates routing policy from outcome:
`routing.attempted_llm` (also exposed as the compatibility field
`routing.escalated`) means a request was sent to Ollama;
`routing.accepted_llm` means the returned answer passed tool and grounding
validation; and `routing.deterministic_fallback` means the deterministic path
answered because an intended or attempted LLM route was unavailable or
rejected. A normal lexicon answer has all three fields false and a null
`fallback_code`. Explicit requests containing more than one supported chat
topic are clarified locally before any model or data-tool call.

```mermaid
flowchart TD
    Q["Query text"] --> LX["Lexicon scores every intent by keyword match"]
    LX --> MG["margin = top1 score − top2 score"]
    MG --> CMP{"margin ≤ τ ?\nτ = 0, frozen"}
    CMP -- "no — confident" --> LXA["Lexicon answers directly"]
    CMP -- "yes — low confidence" --> LLM["Escalate: LLM tool-calling loop\nover role-filtered schemas"]
    LLM --> LLMA["Answer composed from tool result"]
    LXA --> OUT1["~0.09 ms median"]
    LLMA --> OUT2["~3.4 s median"]
```

This is the actual decision in `router.py` — a structural diagram, not a
result, so it holds regardless of which experiment is currently running.

**The LLM tier loses the routing comparison.** On the current 99 dev
queries, 3 seeds, `qwen2.5:3b-instruct` alone scores **83.5% ± 0.5%**
against the lexicon's **88.9%** — a **5.4-point loss**
(`evaluation/results/v3_llm/qwen2-5_3b-instruct.json`, GPU-resident,
0 call failures). Escalating only the lexicon's own low-confidence cases
recovers more than that back: the hybrid scores **94.6%**, a
**+5.7-point** gain over the lexicon alone. Its 95% bootstrap CI is
**[+0.3, +11.8] points** — no longer touching zero — but McNemar's exact
test on the majority vote still gives **p = 0.070**, so it is not
significant at the conventional 0.05 threshold even though the interval
shifted positive. It was also measured on the same 99 queries the lexicon
was tuned on, so the dev set cannot be trusted to confirm it either way —
**the held-out set (P5) still decides**
(`evaluation/results/v3_gates/p4_router.json`).

| | Lexicon (primary) | LLM tier alone | Hybrid, τ = 0 |
|---|---|---|---|
| Accuracy (dev, 99 queries) | 88.9% | 83.5% ± 0.5% | 94.6% |
| Median latency | 0.09 ms | 3,740 ms | 416 ms (expected) |
| vs lexicon | — | **−5.4 pts** | +5.7 pts, CI excludes 0, p = 0.070 |

This supersedes v2's finding of a −19.4-point LLM loss (70.4% vs 89.8%,
single uncontrolled run) and the earlier v3 108-task finding of −12.9 pts
— but none of v2, the pre-P2 108-task v3 run, and this 99-task v3 run may
be differenced against each other (different instruments/conditions,
PROTOCOL §10.1). All three are reported; none corrects another.

**Model sweep (P6, complete as of 2026-08-25).** 1.5B / 3B / 7B × 3 seeds,
all recaptured against the current 99-task/12-tool instrument. Recapturing
1.5B and 7B stalled for a few days on the laptop's NVIDIA kernel driver
(`nvlddmkm`) dropping mid-session (confirmed via `sc query nvlddmkm` →
`STOPPED` and Ollama's own `/api/ps` reporting `size_vram: 0`) — the same
class of issue as an earlier GPU-access block that a host reboot had
already fixed once, not a Claude Code sandbox restriction. The driver
came back healthy on its own; `analyze_sweep.py` now runs against all
three captures and **reconfirms qwen2.5:3b-instruct** as the §9.2 pick
(McNemar vs 1.5B, p = 0.003) — the model tuned above was not selected on
stale evidence after all. `evaluation/results/v3_gates/p6_sweep.json` is
current; F2/F6/F8 draw from it.

**CPU diagnostic capture (2026-08-20, historical, not citable).** Before
GPU access was available at all, a 3B capture ran on CPU against the
99-task set as a sanity check only —
`gpu_residency.fully_resident: false`, mean selection accuracy 81.5% ±
1.0% — superseded by the GPU-resident capture above and kept only as a
record that `tune_router.py` correctly hard-exited on it rather than let
a CPU timing masquerade as a real τ selection. That run also ended with
an ABORT from the harness's own database-integrity check, because two
unrelated scripts (`ablation.py`, `failure_injection.py`) were run
concurrently with it and both write to `mawos.db`; the 2026-08-21
GPU-resident recapture ran with nothing else touching the database and
completed with no such warning.

---

## The agents

**Four**, as of P2 (`docs/RESEARCH_PLAN_V3.md` §7 — a component is an
agent iff it owns state/policy that outlives one request *and* can act
without direct invocation; `backend/app/agents.CORE_AGENTS` is the
queryable source of truth, not just prose):

| Agent | Kind | Responsibility |
|---|---|---|
| **Orchestrator** | router + tools | Confidence-gated tool-calling loop; deterministic lexicon as primary tier |
| **Attendance** | rules + proactive | percentages, <75% shortage, absence streaks, autonomous periodic scan |
| **Eligibility** | rules + CART | hall-ticket eligibility *and* scholarship scoring — merged from Exam+Scholarship at P2, since both owned the same two upstream triggers (attendance/fee updates) and the same shape of policy |
| **Scheduling** | constraint solver | objective-driven simulated annealing (P1) over a greedy seed; CSV export |

**Still real, still running, not counted as agents.** Academic, Admission,
Finance, Placement and Notification stay in the registry — tools.py and
the REST routes call into them exactly as before — but fail the criterion
above: Academic/Admission/Finance/Placement have no policy of their own
that outlives a request, and Notification reacts to events but owns
neither state nor policy, so it is a bus subscriber, not an agent.
Merging Exam+Scholarship did **not** merge their tools: `get_hall_ticket`
and `get_scholarship` stay two distinct tools (§7.1) — agent merging is
not tool merging. `get_admissions_funnel` *was* retired (13→12 tools):
Admission's only chat-facing capability, dropped since Admission no longer
qualifies as an agent; the admissions funnel itself is untouched, still
served directly by the admin/principal REST routes.

Dropped from v1: Library, Smart-Event, and the pseudo "Student/Faculty
agents" — students and faculty are *roles with permissions*, not agents.

---

## Academic timetable workflow

The application uses a Python MRV solver with bounded repair and hill climbing,
independent validation, term-scoped configuration, immutable published versions,
and atomic publication. HODs manage drafts and locks; students and faculty see
only their own published schedules, including current/next classes in Asia/Kolkata.
Admin configures terms, periods and rooms; principal has a read-only coverage view.

The additive migration `20260908_timetable` is required before starting the updated
PostgreSQL application. It was tested only on `mawos_test`; no live migration was
executed. See [configuration, API, safety and limitations](docs/TIMETABLE.md) and
[verification results](docs/TIMETABLE_VERIFICATION.md).

The following P1 results and original research solvers are preserved as frozen
research artifacts. Their unversioned `timetable_slots` are no longer the source
for application timetable views.

## Scheduler (P1): objective-driven, not just feasible

v2's timetable solver was a randomized greedy with restarts — feasible, but
with no defined objective to improve against. P1 adds an explicit
multi-term objective (idle gaps, late starts, load balance, block length,
repeats) and a simulated annealer (Metropolis acceptance, geometric
cooling, incremental delta-cost) seeded by the same greedy construction, so
the comparison isolates the search rather than the seed.

10 seeds each, same institution instance (`evaluation/results/v3_scheduler/e4.json`):

| | v2 greedy (frozen) | P1 (SA) | Instance floor |
|---|---|---|---|
| Objective (mean) | 2,441.3 | 204.2 | 195.96 |
| Objective (range across seeds) | 2,394 – 2,531 | 198 – 215 | — |
| Solve time (median) | 163 ms | 5.3 s | — |

The two ranges are **disjoint by roughly an order of magnitude**
(v2-best ÷ P1-worst ≈ 11×), so no significance test was needed to call it —
a per-seed rank test was skipped rather than fabricated, since v2's
individual seed values were never stored, only the band. P1 lands within
~4% of the instance's known lower bound. A weight-ablation run (zeroing
each objective term in turn) confirms every term is load-bearing: none of
them can be dropped without moving the objective.

**Historical solver simulation (not a research result).**
`backend/app/scheduler_live.py` retains the P1 solver event trace for research
replays. Its former direct-generation API has been retired in favor of explicit
draft generation and publication. The new HOD workspace reports actual solver
metrics and validation results without implying that a draft is published.

---

## External benchmark: ITC-2007 track 3 (P1b)

Plan §4.4 names the risk directly: *"a wrong mapping produces a
meaningless number, which is worse than no number."* So before running the
scheduler against the competition's curriculum-based course timetabling
instances, the cost model (`evaluation/itc2007/ctt.py`) was transcribed
function-by-function from the competition's own `validator.cc` and
**differentially tested** against the compiled binary:

- **1,900 random instance/solution pairs, 2 seeds** — agree with the
  official validator on **all eight cost components**, not just the total.
- The published toy example reproduces the officially stated
  `Violations = 5, Total Cost = 30`.
- The solver (`evaluation/itc2007/solver.py`) solves the toy instance to
  0 violations from 3 seeds, confirmed by the official binary.
- Along the way this found a genuine **out-of-bounds read in the official
  validator** at `periods_per_day == 1` (documented in `crosscheck.py`);
  no competition instance uses that value, so it is excluded from the
  generator rather than reproduced.

**Blocked on data, not on code.** The `comp01–comp21` instance files sit
behind a login at the competition's own site, and the maintained mirror
currently fails TLS verification with a certificate hostname mismatch — see
`evaluation/itc2007/INSTANCES.md` for exactly why, and the three ways to
supply the files. `run_e4b.py` will not invent a number to fill the gap; it
exits with that explanation instead.

---

## Figures

```bash
python evaluation/figures.py          # every figure that has data, one command
```

Six of nine are drawn from real captured data
(`evaluation/results/figures/`, manifest in `FIGURES.md`):

| Figure | What it shows |
|---|---|
| F1 — cascade DAG | live bus topology: 7/9 agents in cascades, 9 edges, depth 2 |
| F2 — routing accuracy | v3 lexicon 88.9% (v2's 89.8% drawn only as a reference line, same frozen instrument), best eligible hybrid 94.6% |
| F3 — confusion matrices | where the lexicon's 11 dev misses actually land (99-task set) |
| F6 — Pareto (accuracy vs latency) | τ=0: 94.6% at 416 ms vs LLM-only 83.5% at 3,740 ms |
| F7 — scheduler | P1 204.2 vs v2 2,441, floor 196.0 (seed-0 convergence trace) |
| F8 — latency CDF | 88.9% of queries answered in 0.09 ms; escalated tail median 3,865 ms |

**Three are intentionally not drawn** — there is no placeholder or
illustrative version anywhere in this repository, only a `Blocked`
exception naming what phase (or what missing recapture) unblocks them,
enforced by `tests/test_figures.py`. F2, F6 and F8 used to be in this
list too (P6 was partial), but as of 2026-08-25 all three model captures
match the current instrument and those three now draw:

- **F4** (RQ1 2×2 factorial) — needs P2 for the conditions (done) and P5
  for the held-out data.
- **F5** (provenance gate on/off) — P3 has only a dev-only engineering
  pass so far (see [Research status](#research-status-v3)), not RQ2's
  confirmed held-out result F5 needs.
- **F9** (dose-response over tool-space size) — needs P5.

Reading rules that travel with every figure: all accuracy numbers are
**dev-set** results; v2 and v3 are never differenced; a figure refuses to
draw rather than read a capture computed against a dev task set that no
longer exists. Full detail in `evaluation/results/figures/FIGURES.md`.

---

## Measured results

| Metric | Result | Run |
|---|---|---|
| Intent routing, lexicon (primary tier) | **88.9%** — 99 labelled queries, 11 intents | v3, GPU-resident recapture, 2026-08-21 |
| Intent routing, LLM tier alone | **83.5% ± 0.5%** (3 seeds) — **loses to the lexicon by 5.4 pts** | v3, GPU-resident recapture, 2026-08-21 |
| Intent routing, confidence-gated hybrid, τ = 0 | **94.6%** — +5.7 pts over lexicon, 95% CI [+0.3, +11.8] pts, McNemar p = 0.070 | v3, GPU-resident recapture, 2026-08-21 |
| Intent routing, LLM tier alone (historical, superseded — not corrected) | 70.4%, single uncontrolled run | v2 |
| Scheduler objective (lower is better) | v2 greedy 2,441.3 → P1 SA **204.2**, instance floor 195.96 | v3 |
| ITC-2007 external benchmark | harness validated against the official scorer; result **pending instance files** | v3 (P1b) |
| Attendance computation | 0 mismatches / 1,000 summaries — reported as *deterministic verification*, **not** an AI accuracy claim | v2, unaffected by v3 routing changes |
| Cross-agent propagation | avg 466 ms, p95 479 ms with the LLM resident; ~127 ms with Ollama stopped — **not comparable across those two conditions**, resource contention only | v2 |
| Ablation — event bus removed | 0 downstream tables auto-update, 4 manual office interventions per upload vs 0 with the bus | re-verified 2026-08-20, post-P2 |
| Failure injection | Eligibility Agent crashed mid-cascade (owns hall-ticket *and* scholarship since the P2 merge, so both withhold together) → Placement and Notification, independent subscribers to the same event, still complete; error audited, replay recovers — **PASS** | re-verified 2026-08-20, post-P2 |
| Scalability | institution 4× larger, identical workload: latency did not grow (0.72×, within run-to-run noise) | v2 |
| Scholarship CART / Placement RF | 90% / 82% test accuracy — deliberately **not** ~100% | v2, unaffected |

**Nothing here is reported as 100%.** Every number above is regenerable
from `evaluation/` — see [Reproduce everything](#reproduce-everything).

---

## Reproduce everything

```bash
python -m pytest tests -q                 # 44 tests
python evaluation/gate_p05.py             # P0.5 router viability gate
python evaluation/capture_llm.py          # frozen-protocol LLM capture (live Ollama)
python evaluation/analyze_sweep.py        # P6 model selection, PROTOCOL 9.2
python evaluation/tune_router.py          # P4 threshold selection, PROTOCOL 9.3
python evaluation/gate_p3.py              # P3 provenance gate, dev-only pass (needs live Ollama)
python evaluation/gate_p3_figure.py       # P3 diagnostic chart (reads p3_provenance.json, no Ollama needed)
python evaluation/capture_llm_resume.py --models 1.5b,7b   # checkpointed capture, resumable if killed mid-run
python evaluation/scheduler_eval.py       # E4: P1 solver vs frozen v2 greedy
python evaluation/freeze_manifest.py      # verify the frozen instrument (PROTOCOL 1.5)
python evaluation/evaluate.py             # v2 harness: both routing tiers
python evaluation/evaluate.py --no-llm    # skip the 108 live LLM calls
python evaluation/ablation.py             # is the architecture load-bearing?
python evaluation/failure_injection.py    # fault isolation + replay
python evaluation/scalability.py          # constant workload vs institution size
python evaluation/figures.py              # every figure, one command
python evaluation/itc2007/build.py        # fetch+build the official ITC validator
python evaluation/itc2007/crosscheck.py   # our CB-CTT cost model vs that validator
python evaluation/itc2007/run_e4b.py      # E4b (needs instances -- see INSTANCES.md)
python fl/federated_poc.py                # appendix / future work only
```

`evaluate.py::_verdict` derives its conclusion from the *sign* of the
measured deltas — the report cannot claim the LLM helps when it doesn't.

---

## Project structure

```
backend/app/
  agents/            4 core agents (CORE_AGENTS) + 5 tool-backed components
    orchestrator.py    confidence-gated router + tool-calling loop [agent]
    eligibility.py     hall-ticket + scholarship, merged at P2 [agent]
    timetable.py       objective + greedy seed + simulated annealing (P1) [agent]
  timetable/         versioned academic configuration, pure solver, validator and role APIs
  scheduler_live.py  preserved legacy event-trace wrapper
    attendance.py      intake, recompute, proactive scan [agent]
    tools.py           typed tool registry (12 tools) with ROLE ENFORCEMENT
    admission.py       admissions pipeline [tool-backed, not an agent]
  router.py          v3 confidence gate: margin <= tau -> escalate
  router_config.json frozen tau=0, sha256-hashed (PROTOCOL 9.3)
  bus.py             instrumented pub/sub, workflow IDs, fault isolation
  llm.py             Ollama chat; startup availability cache
  models.py          shared institutional context store
  seed.py            the synthetic institution
  api/routes.py      role-guarded FastAPI gateway
frontend/            React/Vite application (served separately on port 5173)
  src/               role dashboards, components, services, and tests
  vite.config.js      development API proxy to port 8000
ml/                  UCI calibration -> copula generation -> CART/RF
evaluation/          v2 + v3 harnesses; see `evaluation/results/` and PROTOCOL.md
  itc2007/           ITC-2007 CB-CTT harness (P1b): parser, cost model, SA, validator crosscheck
  results/figures/   P7 figure harness output + FIGURES.md manifest
fl/                  federated-learning PoC (future work)
docs/                RESEARCH_PLAN_V3.md (the plan, phase-gated) · ARCHITECTURE ·
                     DATASET_METHODOLOGY · CODE_WALKTHROUGH · PLAN_V2 (historical)
tests/               44 pytest tests
```

---

## Research status (v3)

Full detail, gates and rationale: `docs/RESEARCH_PLAN_V3.md`. Short form:

| Phase | What | State |
|---|---|---|
| P0 | Freeze v2 baseline, RQ1 instrumentation, protocol | done |
| P0.5 | Router viability gate | done |
| P1 | Objective-driven scheduler (greedy seed + SA) | done |
| P1b | ITC-2007 external benchmark harness | harness validated; **blocked on instance files** |
| P2 | Agent reduction 10 → 4, tool surface held 13→12 | **done** — code, frozen instrument, and downstream figures/tests all on the 99-task set |
| P3 | PCN-style provenance gate | **dev-only engineering pass done**, 2026-08-25 — see below |
| P4 | Confidence-gated hybrid router, τ frozen on dev | **done, current** — GPU-resident recapture on the 99-task instrument, 2026-08-21 |
| P5 | Held-out set → dual annotation → single test run | **blocked — the largest schedule risk** |
| P6 | Model sweep, 1.5B/3B/7B × 3 seeds | **done**, 2026-08-25 — all three recaptured against the 99-task set; 3B reconfirmed as the §9.2 pick |
| P7 | Figures F1–F9 | harness done; F1/F2/F3/F6/F7/F8 draw fresh data; F4/F5/F9 blocked on P2-adjacent work/P3/P5 |
| P8 | Rewrite ARCHITECTURE.md / RESULTS.md to match the evidence | pending — README above is current except where flagged stale above, those two still carry v2-era numbers by design until P8 |

```mermaid
flowchart LR
    P0["P0 baseline"] --> P05["P0.5 gate"] --> P1["P1 scheduler"] --> P2["P2 4 agents"]
    P2 --> P3["P3 provenance gate\n(dev-only pass)"]
    P2 --> P4["P4 hybrid router"]
    P2 --> P6["P6 model sweep"]
    P4 --> P7["P7 figures"]
    P6 --> P7
    P3 -.needs P5.-> P5["P5 held-out set\nBLOCKED: external authors"]
    P1 -.instances.-> P1b["P1b ITC-2007\nBLOCKED: instance files"]
    P5 --> P8["P8 doc rewrite"]

    classDef done fill:#dce9e4,stroke:#1b7f79,color:#1b3d38;
    classDef partial fill:#f7ecd6,stroke:#a06a1e,color:#5a3e0f;
    classDef blocked fill:#f4dede,stroke:#8a3b2f,color:#5a2318;
    classDef pending fill:#eef1f6,stroke:#4a5899,color:#2a3357;
    class P0,P05,P1,P2,P4,P6 done;
    class P3,P7 partial;
    class P5,P1b blocked;
    class P8 pending;
```

**P6, completed 2026-08-25.** P2 dropped 9 admission-intent dev tasks
when `get_admissions_funnel` was retired (108→99 tasks, 13→12 tools),
invalidating every number computed against the old instrument. The 3B was
recaptured cleanly against the new one at P4 (297 records, 99 unique task
IDs, 0 call failures, `fully_resident: true`). Recapturing 1.5B and 7B
stalled for a few days on the laptop's NVIDIA kernel driver (`nvlddmkm`)
dropping mid-session (`STOPPED` in `sc query nvlddmkm`, no reboot); the
driver came back healthy on its own and both were recaptured the same
way (297 records each, 0 call failures; 1.5B 100% GPU-resident, 7B 81.7%
— same historical pattern, reported out-of-competition). `analyze_sweep.py`
now runs against all three and reconfirms **qwen2.5:3b-instruct** as the
§9.2 pick (McNemar vs 1.5B, p = 0.003) — the model behind P4's τ was not
selected on stale evidence after all. F2/F6/F8 now draw from the fresh
`p6_sweep.json`.

**P3 — provenance gate (dev-only, 2026-08-25).** `backend/app/provenance.py`
extracts every numeric claim from the LLM tier's free-text answer and checks
it against the tool payload(s) that actually ran, blocking (falling back to
a deterministic tool-result rendering) if any claim doesn't trace back to
real data. Only the LLM tier is gated — the lexicon's answers are formatted
directly from tool output and grounded by construction. `evaluation/gate_p3.py`
validates the mechanism on the 54 numeric-answer dev tasks: one real claim
per genuine answer replaced with a fabricated value (synthetic ground truth,
since a real annotated-hallucination corpus doesn't exist yet — that's P5,
blocked on external authors), one seed. **Catch rate on synthetic corruption:
100% (39/39). Block rate on genuine answers: 23.5% (12/51)** — down from an
initial 34% after fixing three real extraction bugs the first pass surfaced
(Indian lakh-style comma grouping, e.g. "₹1,03,340.55"; markdown list/ordinal
labels being mistaken for claims; dates and dict keys never entering the
grounded set). The residual 12 blocks are a mix the manual-review table in
`evaluation/results/v3_gates/p3_provenance.md` leaves auditable rather than
auto-classified — several are the LLM doing its own arithmetic (e.g. a
computed "shortage of 9.33%") which the gate correctly can't verify, not
proof of a hallucination. **Not RQ2's confirmed result** — one seed,
synthetic ground truth, not the 3-seed convention.
`python evaluation/gate_p3_figure.py` plots these two rates plus the
gate's cost against the LLM call it checks (`evaluation/results/v3_gates/p3_diagnostic.png`,
regex/set arithmetic at 468 µs/check vs. ~3.7 s for the LLM call itself —
about 8,000× cheaper). It is deliberately outside the P7 figure registry
and is not F5 — same caveats as this paragraph travel with it.

### Known limitations (say these before an examiner does)

- The 99-query routing benchmark and the lexicon it scores were written
  by the same project — evidence about this classifier on this benchmark,
  not a general claim about language understanding. A held-out set written
  outside the team (P5) is the fix, and it hasn't run yet.
- τ = 0 was selected on that same contaminated dev set. The hybrid's
  +5.7-point gain has a CI that now excludes zero, but McNemar's exact
  test still gives p = 0.070 — not significant at 0.05. P5 decides, not
  this README.
- The model sweep is one family (Qwen 2.5), one temperature, three seeds
  — all three sizes are now reconfirmed against the current 99-task
  instrument (2026-08-25), but it is still one family at one temperature.
- The 7B could not stay GPU-resident on this 6 GB laptop, in either the
  pre- or post-P2 sweep (81.7% both times), and is reported out of
  competition — valid accuracy, non-comparable latency.
- The provenance gate's false-block rate (23.5%) is measured against
  synthetic corruption, not real annotated hallucinations — a genuine
  hallucination might not look like "swap one number for a fabricated
  one," so the catch rate could be optimistic until P5's real annotated
  data exists.
- Data is synthetic (UCI-calibrated, copula, 3% label noise); the bus is
  in-process and at-most-once; replay recovery is manual.

### Physical Library Agent

Student catalogue/reservations/slips/returns, librarian counter workflows, in-person
library fines, Admin librarian management, and linked-child Parent summaries are
implemented in the current React/FastAPI application. See [Library operations and
acceptance guide](docs/LIBRARY.md) for policy settings, the manual additive migration,
expiry maintenance, endpoint access and the exact browser checklist.
