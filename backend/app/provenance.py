"""P3 — PCN-style provenance gate (RQ2, docs/RESEARCH_PLAN_V3.md §3.2).

A deterministic extract-and-match check on the LLM tier's free-text
answers: every numeric token in the answer must trace back to a number
that actually appears in the JSON payload of the tool call(s) made for
that same query (or to one of MAWOS's own fixed institutional-policy
constants, e.g. the 75% attendance threshold — those are real values the
system is entitled to state, not hallucinations). This is an engineering
gate, not a novelty claim — the mechanism is PCN-style (Proof-Carrying
Numbers, arXiv 2509.06902) — and it exists to catch the LLM inventing a
number the tools never returned.

H2 (§3.2): "a deterministic extract-and-match gate reduces ungrounded
numeric claims at acceptable false-block and latency cost." Falsified if
false-block rate degrades usable answers, or claims escape by paraphrase.
`evaluation/gate_p3.py` measures both on the dev split — see that file's
docstring for why this is a dev-only, not-yet-confirmed engineering
result, same caveat P4's tau carries.

Only the LLM tier is gated, and only turns where at least one tool was
called — the lexicon tier's answers are formatted directly from tool
output (`orchestrator._FORMATTERS`) and are grounded by construction, and
a turn with zero tool calls has nothing to check a claim against.
"""
import re

from . import config

_NUMBER_RE = re.compile(
    r"(?<![\w.])"                                   # not mid-token
    r"₹?\s*(\d[\d,]*(?:\.\d+)?)\s*%?"
)
#: Deliberately not comma-grouping-aware: strips every comma rather than
#: validating 3-digit Western groups, because MAWOS is an Indian
#: institution and its own currency strings use lakh-style 2-digit
#: secondary groups ("1,03,340.55") that a Western-grouping regex
#: mis-splits. A spaced list ("5, 10, 15") still parses as three numbers
#: -- the whitespace breaks the match, only a directly-attached comma
#: gets swallowed.

#: Absolute tolerance covering display rounding (e.g. 82.04 restated as "82%").
TOLERANCE = 0.05

#: Fixed institutional-policy numbers MAWOS is entitled to state in any
#: answer — they are not per-query tool output, so they would otherwise
#: always read as "ungrounded" even though they are real, not invented.
STATIC_GROUNDED = {
    config.ATTENDANCE_THRESHOLD, config.ABSENCE_STREAK_ALERT,
    config.FEE_LATE_FINE_PER_DAY, config.FEE_GRACE_DAYS,
    # Claim matching uses approximate display numbers, never monetary accounting.
    config.LIBRARY_LOAN_DAYS, float(config.LIBRARY_FINE_PER_DAY),
}


def extract_numbers(text: str) -> list[float]:
    """Every numeric token in free text, as floats, in order.

    Structural markers are excluded, not just decorated digits: a
    hyphen-attached code suffix ("CIE-1"), a parenthetical ordinal/year
    label ("(2)"), and a markdown ordered-list marker at the start of
    its line ("1. **IBM**") are not numeric claims about anything, and
    without this filter they dominate the false-block rate (measured on
    dev: 11 of 17 blocks on genuine answers were this class, see
    evaluation/results/v3_gates/p3_provenance.md). This is a documented
    scope limit, not a claim of full coverage — MAWOS's domain has no
    legitimate negative numeric claims, which is what makes excluding
    every hyphen-attached number safe here.
    """
    out = []
    for m in _NUMBER_RE.finditer(text):
        start, end = m.start(), m.end()
        raw = m.group(1)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before == "-" and "." not in raw and "," not in raw:
            continue
        # "(1)", "(2)": a bare short ordinal/year label, not a parenthetical
        # amount like "(₹1,03,340.55)" -- those keep their currency/comma/
        # decimal, this doesn't.
        if (before == "(" and after == ")" and len(raw) <= 2
                and "." not in raw and "," not in raw and "₹" not in m.group(0)):
            continue
        line_start = text.rfind("\n", 0, start) + 1
        if after == "." and text[line_start:start].strip() == "":
            continue
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _raw_numbers(text: str) -> list[float]:
    """Every digit run in `text`, with none of `extract_numbers`'s
    structural-marker filtering. Used only to build the *grounded* side:
    over-including there only makes the gate more lenient (a claim
    matching a coincidentally-grounded number is never a safety problem
    the way an under-grounded false block is), so e.g. the "25" inside
    an ISO date string ("2026-04-25") should count as grounded even
    though that same "25" would be filtered as hyphen-attached if it
    appeared in the *claim* text being checked.
    """
    out = []
    for m in _NUMBER_RE.finditer(text):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return out


def _flatten(obj, into: set) -> None:
    if isinstance(obj, bool):
        return                              # booleans are not numeric claims
    if isinstance(obj, (int, float)):
        into.add(round(float(obj), 4))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):          # e.g. a `by_year` dict keyed "1".."4"
                for n in _raw_numbers(k):
                    into.add(round(n, 4))
            _flatten(v, into)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten(v, into)
    elif isinstance(obj, str):
        for n in _raw_numbers(obj):         # numbers the tool itself emitted as text
            into.add(round(n, 4))
    else:
        # Dates and anything else non-primitive: this is exactly what the
        # LLM itself saw, since the tool message is built with
        # `json.dumps(result, default=str)` (orchestrator.py) -- ground
        # against the same string form, not the in-memory type.
        for n in _raw_numbers(str(obj)):
            into.add(round(n, 4))


def grounded_numbers(tool_results: list[dict]) -> set[float]:
    into = set(STATIC_GROUNDED)
    for r in tool_results:
        _flatten(r, into)
    return into


def _matches(claim: float, grounded: set[float]) -> bool:
    return claim in grounded or any(abs(claim - g) <= TOLERANCE for g in grounded)


def check(text: str, tool_results: list[dict]) -> dict:
    """Extract every numeric claim in `text` and check it against every
    number the tool call(s) for this query actually returned."""
    grounded = grounded_numbers(tool_results)
    claims = extract_numbers(text)
    ungrounded = [c for c in claims if not _matches(c, grounded)]
    return {
        "claims_checked": len(claims),
        "ungrounded": ungrounded,
        "grounded_rate": 1.0 if not claims else 1 - len(ungrounded) / len(claims),
        "blocked": len(ungrounded) > 0,
    }
