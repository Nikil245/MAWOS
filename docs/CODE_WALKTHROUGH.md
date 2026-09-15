# MAWOS v2 Code Walkthrough — team study guide

Every team member must be able to explain any file here without opening it.
This is the reading order, the story per file, and the viva answers.

## The 30-second pitch (memorise)

> MAWOS is an event-driven multi-agent workflow orchestration engine for
> universities. Ten agents own institutional domains and coordinate over an
> instrumented event bus; an LLM orchestrator selects role-scoped tools rather
> than following a script. When faculty mark attendance, hall-ticket,
> scholarship and placement eligibility all update autonomously, and every hop
> is measured under a workflow ID. Rules where correctness matters, a solver
> where it's a search problem, ML where it's prediction, an LLM only where the
> input is language.

## Reading order

### 1. `backend/app/bus.py` (~90 lines — read first)
Publish/subscribe with Redis semantics, plus three research-critical
properties: every publish writes a `workflow_events` row (workflow_id, hop,
agent, elapsed-ms); the first publish of a cascade mints the workflow_id every
downstream event inherits; and each subscriber is wrapped in fault isolation
so one crashing agent becomes an auditable `agent.error` event instead of a
dead cascade.
**Q: why not real Redis?** Same semantics, zero infrastructure on the target
hardware; swapping the transport is not a redesign.

### 2. `backend/app/models.py`
The shared context store. v2 adds Department, Faculty, TeachingAssignment,
TimetableSlot, Application, MarksRecord to v1's attendance/fees/eligibility
tables, plus the two instrumentation tables (`workflow_events`,
`intent_logs`). Agents never call each other directly — they meet here.

### 3. `backend/app/agents/tools.py` — **the security boundary**
Twelve typed tools, each with a JSON schema (what the LLM sees), an allowed
role list, and an executor. `_resolve_usn` pins students to their own record
regardless of what they (or the model) ask for; `schemas_for_role` means a
student is never even *offered* an admin tool; `execute` re-checks the role
before running. Enforcement is in code, never in the prompt.
**Q: how do you stop the LLM leaking another student's marks?** It physically
cannot: the tool rewrites the USN to the caller's own before touching the DB.

### 4. `backend/app/agents/orchestrator.py` + `backend/app/llm.py` + `backend/app/ai_provider.py`
The brain. Recognized MAWOS/private intents execute a permission-checked,
read-only tool and use a deterministic server formatter. Safe general-learning
turns go through the provider policy: verified Groq first in `auto`, optional
local Ollama fallback, or a safe unavailable response. The hosted provider gets
no tools or database data. A bounded legacy Ollama selection path remains for
ambiguous supported record phrasing, but the authorized result is still rendered
by the server. Routing is logged to `intent_logs`, and `llm.py` retains the
measured lexicon/gate implementation.

### 5. `backend/app/agents/timetable.py`
Constraint solver: randomized greedy, subjects placed hardest-first, with
restarts. Hard constraints — a faculty member is never double-booked
*globally across all 40 sections*, one class per section-slot, ≤2 periods of a
subject per day, exactly `credits` periods per subject per week. Reports
placement rate, restarts and solve time (720/720, 1 restart, ~70 ms). Also
does CSV export.
**Q: is it optimal?** No — it's a practical randomized heuristic, and we
report the achieved placement rate rather than claiming optimality.

### 6. `backend/app/agents/admission.py`
The pipeline: verify (threshold checks with reason codes) → merit
(50% entrance + 30% twelfth + 20% tenth, ranked per department) → allot
(against department intake with a 30% reserved-category floor) → enrol
(creates the Student, the login, the first-term fee, and publishes
`admission.enrolled`, which the Notification Agent turns into a welcome
message). This is the clearest example of an agent owning a *workflow*, not
just a query.

### 7. The domain agents
`attendance` (percentages, shortage, streaks, **proactive scan**),
`finance` (fines, payments, defaulters, **proactive scan**),
`exam` (hall-ticket rules), `scholarship` (rule pre-filter + CART),
`placement` (drive criteria + RF ranking, final-years only),
`academic` (marks, rosters, analytics), `notification` (event → message).
Know the cascade by heart — it is the demo.

### 8. `ml/` — the dataset story
`calibrate.py` estimates CGPA/attendance/backlog distributions **and their
correlation matrix** from the real UCI Student Performance data (n=1,044).
`generate_datasets.py` samples through a **Gaussian copula** so those
correlations survive, then makes labels non-trivial (banded committee scoring
+ noise + 3% flips; stochastic placement outcomes). `train.py` fits CART and
RF with a held-out split and 5-fold CV.
**Q: why not ~95% like the cited papers?** Our labels contain irreducible
noise by construction; a clean rule re-encoding would give 100% and prove
nothing.
**Q: why a copula and not a multivariate normal?** MVN forces every marginal
Gaussian, but backlogs are a zero-inflated count (82% zeros). The copula keeps
the dependence structure while each variable keeps its real marginal.

### 9. `evaluation/`
`evaluate.py` (routing with a hard tier + confusion matrix, deterministic
attendance verification, live cascades, modeled manual baseline),
`ablation.py` (is the bus load-bearing? is the orchestration layer cheap?),
`failure_injection.py` (crash an agent, prove isolation + replay recovery),
`scalability.py` (constant 300-record workload across 1,200→4,800 students).
Never present a number you cannot regenerate with these.

Inside `evaluate.py`, three functions carry the routing story and you should
be able to point at each:

* `eval_intent_routing` — the deterministic tier. One query in, one tool out.
* `eval_intent_routing_llm(db, role_mode)` — the same 108 queries through the
  LLM, scored on the **first tool it selects** against the same target, so the
  two tiers are directly comparable. `role_mode="matched"` asks each query as
  the role that would really ask it; `"single"` re-asks everything as admin.
  Flags: `--no-llm`, `--no-role-sensitivity`.
* `_verdict` — writes §1.3's interpretation **from the sign of the measured
  deltas**. It is deliberately written so the report cannot claim the LLM
  "earns its place" when the data says otherwise. If someone asks whether the
  conclusion was decided in advance, show them this function.

The model warm-up before the timed loop is not cosmetic: the first call loads
~2 GB and would otherwise blow the 32 s HTTP timeout and score as an error.

### 10. `frontend/src/` + `backend/app/api/routes.py`
The React/Vite frontend runs separately on port 5173 and proxies relative
`/api` requests to the FastAPI backend on port 8000. React routes are handled
by Vite, while backend routes are thin JWT+role-guarded wrappers (business
logic lives only in agents). Note `_owns_assignment` — faculty can mark
attendance *only* for subject-sections they are actually assigned to; the API
returns 403 otherwise, and that is tested.

## Viva question bank

**Why an event bus instead of direct calls?** Decoupling (the Attendance Agent
doesn't know its consumers), auditability (the workflow log is our measuring
instrument), and a scaling story (pub/sub semantics → distributable).

**Orchestration vs choreography — which is this?** Both, deliberately. The
*query path* is orchestrated (a central agent decides the tool sequence); the
*propagation path* is choreographed (agents react to events with no
controller). Requests need explainable plans; propagation needs loose coupling.

**Why is this multi-agent if it's one process?** See ARCHITECTURE §2 —
autonomy, subscriptions, proactive scans and message-based coordination, not
process count.

**Your LLM scores 70.4% on routing and the keyword classifier scores 89.8%.
Why keep the LLM at all?** Because routing is not the only job. The lexicon
maps a query to exactly one tool and then prints a fixed formatter; the LLM
chains 2-3 tools in a single turn and writes the answer in prose grounded in
their combined JSON. Ask both "check my attendance and whether my hall ticket
is blocked" — the lexicon answers half the question. What the measurement
actually says is narrower than "the LLM is worse": on a **closed 12-intent
set** with a lexicon tuned to these phrasings, rules win the routing step. So
route with rules, compose with the model. We report it that way.

**Isn't this just a rule-based system again, then?** No — and this is the
honest version of the answer. The routing *step* is better served by rules
here, and we say so. The system is not rule-based because the architecture
is: agents own domains and coordinate over an instrumented event bus, one
upload cascades to four downstream agents with no controller, permissions are
enforced in code at the tool boundary, and the timetable is a constraint
solver. Remove the bus and 4 manual office interventions reappear per upload
(§ablation) — that is the contribution, and it is independent of which tier
routes a chat message.

**You changed the benchmark after seeing a bad number. Why is that not
cherry-picking?** Because the first configuration had a defect we can name,
and both numbers are still in the report. The first run asked every query as
*admin*, including first-person student questions like "Am I short of
attendance?" — unanswerable for an admin, because `get_attendance` demands an
explicit USN from staff and the persona never supplied one. The model
correctly refused to call a tool on 35 queries; that scored as 35 misses and
measured our harness, not the model. The fix asks each query as the role that
would really ask it (70.4%). The admin run is retained as §1.2b, where the
70.4% → 55.6% drop became a finding in its own right: tool calling is
permission-sensitive, so any single-persona benchmark understates a
role-scoped system. Cherry-picking would be deleting the 55.6%.

**Why CART, not XGBoost?** The reference literature for scholarship prediction
uses CART (like-for-like comparison); a single tree is interpretable, which
matters for a decision about a student's money; and at 500 rows boosting's
edge is marginal while the explainability cost is total.

**Why Random Forest for placement?** Bagging reduces variance on a stochastic
target, vote proportions give the ranking probability we need, and it matches
the cited reference.

**Why one LLM and not one per agent?** There is exactly one
language-ambiguous task — understanding the request. Everything after is
deterministic routing and domain logic. Eleven models would cost eleven times
as much for no additional capability.

**What happens if the LLM is unavailable?** The portal says so, and the
deterministic tier answers with the same tools and the same permissions —
89.8% routing accuracy, and the gap on indirect phrasings is exactly what we
publish as the LLM's measured value.

**How do you know the architecture earns its keep?** The ablation: with the
bus disabled, zero downstream tables update and four manual office
interventions are required per attendance upload.

**Is it fault tolerant?** Yes, and it's demonstrated: crash the Scholarship
Agent mid-cascade — siblings still complete, the failure is audited as
`agent.error` under the same workflow_id, and replaying that event after
recovery restores consistency.

**Does it scale?** A 4× larger institution (1,200 → 4,800 students) changed
cascade latency for identical work by 1.04× with flat memory — cost tracks the
affected cohort, not institution size.

## Suggested viva ownership (4 members)
* **1** — orchestrator, tools/permissions, LLM & fallback tiers (files 3, 4)
* **2** — bus, cascade, domain agents, context store (files 1, 2, 7)
* **3** — timetable solver, admissions pipeline (files 5, 6)
* **4** — ML methodology + evaluation experiments + demo driving (files 8, 9, 10)

Everyone: the pitch, the cascade diagram, ARCHITECTURE §2 and §5.
