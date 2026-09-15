# MAWOS v2 — Architecture & Design Rationale

**Framing:** MAWOS is an *event-driven multi-agent workflow orchestration
engine for educational institutions, with hybrid AI*. The novelty is
autonomous coordination of institutional workflows — not the agent count, not
any library, not the LLM. Use the phrase **"Event-Driven Multi-Agent Workflow
Orchestration"** consistently in the abstract, contributions and conclusion.

## 1. System shape

```
 Browser — five role portals (student · faculty · HOD · principal · admin)
    │
 FastAPI gateway (JWT; every route role-guarded)
    │
 ORCHESTRATOR AGENT
   ├── Deterministic path:  recognized private/MAWOS intent → one
   │      permission-checked read-only tool → server formatter
   ├── General path:        bounded sanitized text → Groq, optional Ollama
   │      fallback, or a safe provider-unavailable response
   └── Legacy escalation:   bounded Ollama selection for an ambiguous
                            supported record intent → one checked tool →
                            deterministic formatter
    │  (same tools, same permissions in both paths)
    ▼
 TOOL REGISTRY (12 typed tools; roles enforced in code, not in the prompt)
    │
 ┌──┴───────────────────────────────────────────────────────────┐
 │ Admission · Timetable · Academic · Attendance · Finance      │
 │ Exam · Scholarship · Placement · Notification                │
 └──┬───────────────────────────────────────────────────────────┘
    │  instrumented pub/sub bus (workflow IDs, hop counts, per-hop latency,
    │  per-subscriber fault isolation)  +  periodic autonomous scans
    ▼
 Shared Institutional Context Store (SQLAlchemy; SQLite or PostgreSQL)
```

### The propagation cascade (the core demonstration)

```
attendance_agent  publishes attendance.uploaded              (hop 0)
  └─ recomputes %, shortage, streaks
       └─ publishes attendance.updated                        (hop 1)
            ├─ exam_agent         → hall-ticket eligibility   (hop 2)
            ├─ scholarship_agent  → CART re-evaluation        (hop 2)
            │     └─ notification_agent → award alerts         (hop 3)
            ├─ placement_agent    → RF shortlist refresh      (hop 2)
            └─ notification_agent → shortage alerts            (hop 2)
```

A second cascade starts from `fees.updated` when a student pays (eligibility
can flip in real time), and a third from `admission.enrolled`. Every publish
writes a `workflow_events` row with the same `workflow_id`, so cascade depth,
agents involved and end-to-end latency are **measured**, not asserted — and
any workflow can be replayed as a timeline in the System tab.

## 2. Key decisions and their defence

### "Is this really a multi-agent system, or just modules?"
Each agent has (a) its own domain and state, (b) its own decision logic,
(c) **subscriptions** — it reacts to events nobody explicitly called it for,
(d) **autonomy** — Attendance and Finance run periodic scans on their own
schedule and raise alerts with no user in the loop, and (e) coordination
purely through the shared store and events, never direct peer calls between
domains. Process count is an operational choice; what makes a system
multi-agent is decomposition, autonomy and message-based coordination.
Running in one process is the correct engineering choice for the hardware
budget, and the bus has Redis pub/sub semantics, so distributing later is a
transport swap, not a redesign.

### "What is the LLM actually doing?"
For general-learning questions, a hosted Groq model or optional local Ollama
model writes a bounded answer from sanitized conversation text, with no tools
or database access. For recognized private and institutional queries, the
model does nothing: FastAPI selects a permission-checked read-only tool and
renders the answer deterministically. A bounded local-only escalation may help
classify ambiguous supported record phrasing, but it receives no private result
and cannot authorize access. **Permissions are enforced in code**
(`agents/tools.py`), never in a model prompt. A student asking for another
student's record is denied by the registry regardless of model output.

The deterministic tier exists so the institution keeps working offline — and
because it is a genuine research baseline: publishing both numbers quantifies
what the LLM buys. **Measured on 2026-08-03 with `qwen2.5:3b-instruct`, it
buys nothing on the routing task and costs a great deal of latency:**

| Tier | Overall | Direct | Indirect | Latency/query |
|---|---|---|---|---|
| Keyword lexicon | 89.8% | 100% | 69.4% | <1 ms |
| LLM tool selection | 70.4% | 83.3% | 44.4% | 4.5 s |

The design assumption was that the LLM would recover the indirect-phrasing
gap. It does the opposite. The result stands as measured; §1.2/§1.3 of
`evaluation/results/RESULTS.md` lists every miss. What the LLM still does
better is **compose** an answer from several tool results in prose, and chain
2-3 tools in one turn — which is what the portal uses it for. Routing is the
part it does worse, and on this evidence a deployment on this hardware should
route with the lexicon and reserve the model for composition.

Threat to validity, stated up front: the 108-query benchmark and the lexicon
were written by the same project, so the lexicon is tuned to these very
phrasings. The comparison is evidence about *this classifier on this
benchmark*, not a general claim about language understanding.

### "Why these ten agents?"
Chosen by institutional function. v1 had eleven because a report said eleven;
Library and Smart-Event were dropped as peripheral, and the "Student Agent"
and "Faculty Agent" were deleted outright — those are **roles with
permissions**, not autonomous units. Admission and Timetable were added
because they are the two genuinely agentic university workflows (a multi-stage
pipeline and a constraint-satisfaction problem) and their absence was the
loudest gap in v1.

### "Why not LangGraph?"
Because the orchestration layer *is* the contribution. If a library performs
the coordination, the answer to "what did you build?" becomes "we configured
a library." MAWOS implements its own loop — classify → select tools →
permission-check → execute → compose — in ~150 readable lines, and the tool
registry, the role gate and the trace are all ours.

### "Why no cloud API?"
A paper whose behaviour depends on a hosted model and a free-tier quota cannot
be reproduced in two years. Local open weights run offline, cost nothing, and
keep the contribution ours. The system is also honest about it: the portal
displays which mode is live rather than pretending.

### "Synthetic data ⇒ 100% accuracy?"
Not here, by construction: features are calibrated to the real UCI Student
Performance dataset (n=1,044) **including its correlation structure** (Gaussian
copula), labels carry 3% clerical noise plus committee-level variation, and
placement outcomes are stochastic draws. Results land at 90% / 82% with 5-fold
CV — below the clean-rule ceiling *deliberately*. See
`docs/DATASET_METHODOLOGY.md` for exact formulas and threats to validity.

### Where each technique is used — and deliberately not used
| Component | Technique | Why |
|---|---|---|
| Attendance %, fines, hall tickets, timetable feasibility | deterministic | must be exact, auditable, appealable |
| Timetable construction | constraint solver (randomized greedy + restarts) | search problem, not a prediction problem |
| Admissions merit & allotment | weighted scoring + quota rules | institutional policy must be transparent |
| Scholarship / placement ranking | CART / Random Forest | genuine prediction over noisy outcomes |
| Composing an answer from tool results | local LLM | free-text generation grounded in JSON — the one thing no rule writes |
| Routing a request to a tool | keyword lexicon (LLM available, measured worse) | the only genuinely ambiguous input — but on a *closed* 12-intent set a tuned lexicon measured 89.8% vs the 3B model's 70.4%, so the honest default here is rules |

## 3. Evaluation methodology

| Metric | How measured |
|---|---|
| Routing accuracy | 108 labelled queries, 12 intents, 72 direct + 36 indirect; per-tier accuracy, confusion matrix, every miss listed. Scored for **both routing tiers** — deterministic keyword classifier and LLM tool selection — on the same queries against the same target tools, so the head-to-head delta (and its latency cost) is measured, not asserted |
| Attendance correctness | agent output vs independent brute-force recomputation (reported as *verification*, never "AI accuracy") |
| Propagation latency | `workflow_events` audit: per-cascade max elapsed-ms; avg/p95/max over live cascades |
| Architecture value | **ablation** — bus disabled vs enabled; orchestration layer vs raw tool call |
| Fault tolerance | **failure injection** — crash an agent mid-cascade; verify siblings, audit trail, replay recovery |
| Scaling | **constant 300-record workload** across a 4× institution range (1,200→4,800 students) |
| Solver quality | slots placed / required, teacher conflicts, restarts, solve time |
| ML quality | held-out test + 5-fold CV; accuracy/precision/recall/F1/confusion |

| Permission sensitivity of tool calling | the same 108 queries re-asked under one staff persona instead of the role that would naturally ask them; measures how much tool selection depends on the caller being able to satisfy the tool's arguments |

Reporting rules the team must follow: never headline a 100% figure; keep the
hard tier in the routing benchmark; label attendance as deterministic
verification; label the manual-workflow comparison as a modeled estimate with
stated assumptions; **and report the LLM-vs-lexicon result in the direction the
data points, including that the LLM currently loses on routing.** A negative
result that is measured and explained is defensible; the same result quietly
dropped from the report is not.

## 4. Recommended Phase-2 paper structure

1. Problem — fragmented institutional workflows, no cross-domain propagation
2. Architecture — event-driven, role-based, one context store
3. Orchestration layer — tool selection, role enforcement, fallback tier
4. Event bus & instrumentation — workflow IDs; measurement as a design goal
5. Agent design — one section for the roster, not one per agent
6. Hybrid AI — rules / solver / ML / LLM division of labour
7. Dataset methodology — UCI calibration, copula, noise injection
8. Evaluation — routing, propagation, **ablation, failure injection, scalability**, ML
9. Limitations and threats to validity
10. Future work — federated learning PoC (appendix; never earlier)

## 5. Threats to validity / limitations (say these first)

* Single-institution prototype: JWT + PBKDF2 auth, role gates and duplicate
  prevention are real, but there is no rate limiting, HTTPS termination, or
  audit of privileged actions beyond the workflow log.
* The bus is in-process, at-most-once and synchronous; a crash mid-cascade is
  recoverable by replay from the audit log, but that replay is manual.
* Data is synthetic and calibrated, not real; no institution shares student
  records, which is precisely the constraint the federated-learning module in
  `fl/` addresses as future work.
* The timetable solver is a randomized greedy with restarts — sufficient for
  the 40-section instance (0 conflicts, 1 restart) but not proven optimal;
  room capacity and lab-block constraints are out of scope.
* National-level views (Chairman / AICTE / NIRF) are deliberately deferred;
  the role model supports them, but they are not implemented or claimed.
* **The routing benchmark is self-authored.** Its 108 queries and the keyword
  lexicon they score come from the same project, so the lexicon is tuned to
  these phrasings. That the lexicon beats a 3B LLM on it is evidence about
  this classifier on this benchmark, not a general finding. A held-out set
  written by someone outside the team — or real portal queries — is the
  correction, and until then the head-to-head should be read as indicative.
* **One model, one size, one run.** The LLM tier was measured with
  `qwen2.5:3b-instruct` at temperature 0.1 on a 6 GB-VRAM laptop, single pass,
  no seeds swept and no confidence intervals. A 7B/14B model may well reverse
  the result; nothing here licenses a claim about "LLMs" as a class.
* LLM latency (~4.5 s/query) was measured with the model resident on the same
  machine serving the app, and cascade latency inflates ~3-4x under that load.
  Numbers taken with and without Ollama running are not comparable.
