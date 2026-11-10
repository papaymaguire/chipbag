"""Verify: one targeted agent per candidate finding, which tries to refute it.

Each candidate from `analysis.yaml` gets a bounded Claude Code agent (Claude Code's own
prompt and read/search tools) working on the whole repository, not just the manifest.
It is asked to find the evidence that would make the finding wrong, because an agent asked
to confirm tends to agree. It ends by calling `submit_verdict`:

- verified:     the refutation search failed; the finding stands (with its search record).
- withdrawn:    refuted; must cite the refuting file:line.
- inconclusive: the repo doesn't settle it.

Code checks every citation before accepting a verdict: the file exists, the lines exist,
and the quoted text is really there. A verdict with bad citations is sent back (3
submissions at most); without an accepted verdict the finding stays `unchecked`.

Grouped findings (several untested requirements of one document) get a verdict per item.
Results are cached per finding in `verify_runs/<id>/`, keyed by the finding's content, so
re-running only spends on new or changed findings. Writes `findings.yaml`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
import yaml
from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from chipbag.agent import Agent
from chipbag.index import resolve_in_root

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TURNS = 30
DEFAULT_AGENT_BUDGET_USD = 1.0
DEFAULT_MAX_COST_USD = 30.0
CONCURRENCY = 4
MAX_SUBMISSIONS = 3
LINE_SLACK = 3  # quoted text may sit this many lines outside the cited range
MCP_SERVER = "verify"
BUILTIN_TOOLS = ["Read", "Glob", "Grep", "Bash"]
SEVERITY = {"high": 0, "medium": 1, "low": 2}
CATEGORY_ORDER = {"spec_test_mismatch": 0, "spec_contradiction": 1, "test_inconsistency": 2, "spec_ambiguity": 3,
                  "untested": 4}

# --------------------------------------------------------------------------
# Instructions
# --------------------------------------------------------------------------

VERIFY_INSTRUCTIONS = """

# Task: check one candidate finding

You check one candidate finding produced by an automated analysis of a hardware design
repository: its specifications, its verification (test plans, tests, sequences,
scoreboards, assertions, coverage, regression lists) and its RTL. The analysis only saw
part of the repository, and its readings can be wrong. Your job is to try to REFUTE the
finding: look for the evidence that would make it wrong. Only if a careful search fails
does the finding stand.

Rules
- Search the whole repository (your working directory), not only the files the finding
  cites. Use Grep and Glob broadly (names of registers, fields, signals, tests, error
  codes), then Read the relevant code in context.
- Read every cited location in context before judging; the finding's own citations may
  be misread.
- Do not modify, create or delete files in the repository. Use Bash only to inspect
  (e.g. open a spreadsheet with Python).
- Specification means documents and register descriptions written to say what the design
  must do. RTL, generated code, tests and models are not specification. They are good
  context (what the design or a test actually does) and you should report it as such.
- You have up to {max_turns} turns. Use them: a thorough search is the point of this
  check. Batch independent searches in one turn, and submit before you run out.

Submitting
Call submit_verdict exactly once when done (resubmit only if it is rejected):
- outcome: verified (you searched properly and the finding stands), withdrawn (you found
  concrete evidence it is wrong), or inconclusive (the repository does not settle it).
- evidence: file locations with an exact quote copied from the file at those lines. Code
  checks every quote against the file; paraphrases are rejected. role: supports (backs
  the finding), refutes (contradicts it), context (relevant background, e.g. what the RTL
  does).
- A withdrawn outcome needs at least one refutes evidence. A verified outcome needs a
  search record, and for contradictions and inconsistencies the supports evidence for
  both sides.
- searches: what you looked for and where, and what you found, including searches that
  found nothing. near_misses: things that looked like they might refute the finding and
  why they don't.
- For findings with several items (requirements or plan items), give an outcome per item
  in `items`, referencing evidence by its index in `evidence`. Search for every item
  yourself (one or more searches each, recorded in `searches`); the finding's own
  analysis is what you are checking, so do not rely on it. An item you did not search
  for is inconclusive, never verified.
- new_sources: relevant spec or verification files you found that the finding did not
  cite (they will be added to the analysis scope).
- resolution: for spec ambiguities and contradictions, what the design actually does (from
  RTL or tests) and what spec change would resolve it; for test problems, the fix.
- reclassify: if you withdraw the finding as stated but your evidence shows a different
  real problem, report it here instead of dropping it. Example: a test disagrees with one
  spec statement but agrees with another spec statement and the RTL, so the real problem is
  the two spec statements contradicting each other (spec_contradiction). Cite both sides.
"""

TASKS = {
    "spec_ambiguity": """\
The finding says this specification text is too ambiguous or under-specified for a test
writer to know what to check. Refute it by finding OTHER specification text in the
repository (other spec documents, register descriptions, integration or programmer's
guides, documentation comments in register definitions) that answers the question, so a
test writer would know what to check. RTL and tests do not withdraw it: if they show what
the design does, cite them as context and put it in `resolution`. Verified: no
specification text answers the question.""",
    "spec_contradiction": """\
The finding says the specification contradicts itself. Refute it by showing the
statements are consistent: equivalent notations (different register-description
conventions meaning the same access or value), different instances, conditions or
configurations, one statement explicitly superseded, or a misreading. Verified: the
specification really states two incompatible things. Cite what the RTL or generated
code implements as context, and say in `resolution` which side it matches.""",
    "test_inconsistency": """\
The finding says a test's prose (comments, log messages, its name, or the plan item it
is linked to) disagrees with what its code does, or that a check presented as a check
can never fail the test. Read the full test source and the helpers, macros and base
classes it uses. Refute it by showing the code does what the prose says (e.g. a macro
that does fail the test, a helper that compares the value, an error status that is
propagated, a scoreboard that checks it). For plan_vs_test: the link between the plan
item and this test may have been inferred by the analysis, not stated in the repository;
if nothing in the repository ties the plan item to this test and another test realizes
the plan item, withdraw and cite that test. Verified: the prose and the code really
disagree, or the check really cannot fail.""",
    "spec_test_mismatch": """\
The finding says the specification states one behavior and a test (or plan item) expects
another. Refute it by finding what explains the difference: other specification text
stating an exception or condition, a test configuration or parameter that makes the
test's expectation consistent, or a misreading of the test or the spec. Verified: the
specification and the test expect incompatible behavior. Cite what the RTL implements
as context, and say in `resolution` which side is likely wrong.""",
    "untested": """\
The finding says the items below are not verified by any test (or only partly, or only
by a plan, or only by checks that cannot fail). For each item, search the whole
repository for a test that does verify it: stimulus that creates the condition AND a
check that fails the test if the behavior is wrong. A scoreboard, assertion or checker
that runs with a test counts as that test's check. Look beyond the cited files: other
test directories, regression and test lists, assertion and formal property files,
generic or generated register tests, firmware and software tests, shared test
libraries. Withdraw an item only with a cited check (and the stimulus that reaches it).
For a partially tested item, search only for the missing part named in `gap`. For a
plan item, search for a test that implements it. Record near misses: tests that touch
the item but don't check it.""",
}

# --------------------------------------------------------------------------
# Verdict schema and citation checks
# --------------------------------------------------------------------------

_LOC = {
    "path": {"type": "string", "description": "path relative to the repository root"},
    "line_start": {"type": "integer"},
    "line_end": {"type": "integer"},
    "sheet": {"type": "string", "description": "for spreadsheets: the sheet name (lines are row numbers)"},
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["verified", "withdrawn", "inconclusive"]},
        "summary": {"type": "string", "description": "Two to four sentences: what you found and why this outcome."},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {**_LOC, "quote": {"type": "string", "description": "exact text from the file"},
                               "role": {"type": "string", "enum": ["supports", "refutes", "context"]},
                               "note": {"type": "string"}},
                "required": ["path", "line_start", "line_end", "quote", "role", "note"],
            },
        },
        "searches": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"what": {"type": "string"}, "where": {"type": "string"},
                                     "found": {"type": "string"}},
                      "required": ["what", "where", "found"]},
        },
        "near_misses": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {**_LOC, "why_not": {"type": "string"}},
                      "required": ["path", "line_start", "line_end", "why_not"]},
        },
        "items": {
            "type": "array",
            "description": "Per item, for findings that list items.",
            "items": {"type": "object",
                      "properties": {"id": {"type": "string"},
                                     "outcome": {"type": "string", "enum": ["verified", "withdrawn", "inconclusive"]},
                                     "reason": {"type": "string"},
                                     "evidence": {"type": "array", "items": {"type": "integer"},
                                                  "description": "indices into `evidence` (0-based)"}},
                      "required": ["id", "outcome", "reason", "evidence"]},
        },
        "new_sources": {
            "type": "array",
            "items": {"type": "object", "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
                      "required": ["path", "reason"]},
        },
        "resolution": {"type": "string"},
        "reclassify": {
            "type": "object",
            "description": "Only with outcome withdrawn: the finding is wrong as stated, but your evidence shows a "
                           "different real problem (e.g. the test agrees with one spec text that contradicts another).",
            "properties": {"category": {"type": "string", "enum": ["spec_ambiguity", "spec_contradiction",
                                                                   "test_inconsistency", "spec_test_mismatch",
                                                                   "untested"]},
                           "title": {"type": "string"},
                           "explanation": {"type": "string"},
                           "evidence": {"type": "array", "items": {"type": "integer"},
                                        "description": "indices into `evidence` that show the new problem"}},
            "required": ["category", "title", "explanation", "evidence"],
        },
    },
    "required": ["outcome", "summary", "evidence", "searches", "near_misses", "items", "new_sources"],
}


def _norm(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split())


def _table_text(path: Path, sheet: str | None) -> list[str] | None:
    """Rows of a spreadsheet as text lines (1-based by row), or None if unreadable."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return None
    sheets = [wb[sheet]] if sheet and sheet in wb.sheetnames else wb.worksheets
    rows: list[str] = []
    for ws in sheets:
        for row in ws.iter_rows(values_only=True):
            rows.append(" | ".join("" if v is None else str(v) for v in row))
    return rows


def check_location(root: Path, loc: dict, quote: str | None) -> str | None:
    """None if the location (and quote, if given) checks out, else the problem."""
    p = resolve_in_root(root, loc.get("path", ""))
    if p is None or not p.is_file():
        return f"{loc.get('path')}: no such file in the repository"
    a, b = loc.get("line_start", 0), loc.get("line_end", 0)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        lines = _table_text(p, loc.get("sheet"))
        if lines is None:
            return f"{loc['path']}: cannot read spreadsheet"
        window = lines  # row numbering across sheets is ambiguous; search the sheet(s)
    else:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"{loc['path']}: {e}"
        if not (1 <= a <= b <= len(lines)):
            return f"{loc['path']}: lines {a}-{b} outside the file (1-{len(lines)})"
        window = lines[max(0, a - 1 - LINE_SLACK) : min(len(lines), b + LINE_SLACK)]
    if quote is not None:
        q = _norm(quote)
        if len(q) < 4:
            return f"{loc['path']}:{a}: quote too short"
        if q not in _norm("\n".join(window)):
            return f"{loc['path']}:{a}-{b}: quote not found there: {quote[:80]!r}"
    return None


def validate_verdict(root: Path, finding: dict, v: dict) -> list[str]:
    errors = []
    for k, e in enumerate(v.get("evidence") or []):
        if problem := check_location(root, e, e.get("quote", "")):
            errors.append(f"evidence[{k}] {problem}")
    for k, n in enumerate(v.get("near_misses") or []):
        if problem := check_location(root, n, None):
            errors.append(f"near_misses[{k}] {problem}")
    roles = [e.get("role") for e in v.get("evidence") or []]
    item_ids = [_item_id(i) for i in finding.get("items") or []]
    if item_ids:
        got = {i.get("id") for i in v.get("items") or []}
        if missing := [i for i in item_ids if i not in got]:
            errors.append(f"items: no outcome for {missing}")
        n_ev = len(v.get("evidence") or [])
        for it in v.get("items") or []:
            if it.get("id") not in item_ids:
                errors.append(f"items: unknown id {it.get('id')}")
            refs = it.get("evidence") or []
            if any(not isinstance(r, int) or not 0 <= r < n_ev for r in refs):
                errors.append(f"items[{it.get('id')}]: evidence index out of range")
            elif it.get("outcome") == "withdrawn" and not any(v["evidence"][r].get("role") == "refutes" for r in refs):
                errors.append(f"items[{it.get('id')}]: withdrawn without refutes evidence")
    elif v.get("outcome") == "withdrawn" and "refutes" not in roles:
        errors.append("withdrawn without any refutes evidence")
    if rc := v.get("reclassify"):
        if v.get("outcome") != "withdrawn":
            errors.append("reclassify is only allowed with outcome withdrawn")
        n_ev = len(v.get("evidence") or [])
        refs = rc.get("evidence") or []
        if not refs or any(not isinstance(r, int) or not 0 <= r < n_ev for r in refs):
            errors.append("reclassify: cite the evidence (indices into `evidence`) for the new problem")
        elif rc.get("category") == "spec_contradiction" and len(refs) < 2:
            errors.append("reclassify as spec_contradiction needs evidence for both statements")
    if v.get("outcome") == "verified" and not v.get("searches"):
        errors.append("verified without a search record")
    n_verified = sum(1 for it in v.get("items") or [] if it.get("outcome") == "verified")
    if item_ids and len(v.get("searches") or []) < (n_verified + 1) // 2:
        errors.append(f"{n_verified} items verified but only {len(v.get('searches') or [])} searches recorded: "
                      "search for each item and record it (mark items you did not search for inconclusive)")
    if (v.get("outcome") == "verified" and finding["category"] in ("spec_contradiction", "spec_test_mismatch",
                                                                       "test_inconsistency") and "supports" not in roles):
        errors.append("verified without supports evidence")
    return errors


def _item_id(item: dict) -> str:
    return item.get("req_id") or item.get("plan_id") or ""


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------


def _cite_line(c: dict) -> str:
    loc = c.get("path", "?")
    if c.get("lines"):
        a, b = c["lines"]
        loc += f":{a}" if a == b else f":{a}-{b}"
    elif c.get("rows"):
        loc += f" ({c.get('sheet')} rows {c['rows'][0]}-{c['rows'][1]})"
    label = f"[{c['kind']}] " if c.get("kind") else ""
    stmt = f"{c['statement']} — " if c.get("statement") else ""
    return f"  {label}{loc}: {stmt}`{c.get('text', '')[:300]}`"


def finding_prompt(f: dict) -> str:
    parts = [f"Finding {f['id']}: {f['category']} / {f['type']} (severity {f['severity']})", f"Title: {f['title']}"]
    if f.get("section"):
        parts.append(f"Specification section: {f['section']}")
    for k, v in (f.get("detail") or {}).items():
        if isinstance(v, (str, int, float)) and v != "":
            parts.append(f"{k}: {v}")
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            parts.append(f"{k}: " + " | ".join(v))
    if f.get("spec"):
        parts.append("Specification quotes:\n" + "\n".join(_cite_line(c) for c in f["spec"]))
    if f.get("prose"):
        parts.append("Test prose:\n" + "\n".join(_cite_line(c) for c in f["prose"]))
    if f.get("tests"):
        parts.append("Test code:\n" + "\n".join(_cite_line(c) for c in f["tests"][:12]))
    for it in f.get("items") or []:
        if it.get("req_id"):
            lines = [f"Item {it['req_id']}: {it['statement']}"]
            if it.get("section"):
                lines.append(f"  section: {it['section']}")
            lines += [_cite_line(c) for c in it["spec"][:4]]
            if it.get("gap"):
                lines.append(f"  gap (the part to search for): {it['gap']}")
            for tb in it.get("tested_by") or []:
                lines.append(f"  partly tested by {tb['unit']}: {tb['rationale']}"
                             + (f" (missing: {tb['missing']})" if tb.get("missing") else ""))
            for w in it.get("weak_evidence") or []:
                lines.append(f"  weak evidence: {w['unit']} ({w['kind']}, {w['strength']}): {w['rationale']}")
        else:
            lines = [f"Item {it['plan_id']}: plan item '{it['name']}' in {', '.join(it['sources'])}: {it['summary']}"]
        parts.append("\n".join(lines))
    parts.append("Your task:\n" + TASKS[f["category"]])
    if f.get("items"):
        parts.append(f"This finding has {len(f['items'])} items. Search for each of them and give each an outcome.")
    if f["type"] == "register_suite":
        parts.append("For this finding: search for a generic register test that runs over every register of the "
                     "block named in the spec quotes (often generated from the register description or provided by a "
                     "shared test library and enabled per block). Withdraw if one exists and runs on this block.")
    return "\n\n".join(parts)


def finding_hash(f: dict) -> str:
    core = {k: v for k, v in f.items() if k not in ("status", "verify", "labels")}  # labels: display only
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Agent run
# --------------------------------------------------------------------------


def _text(msg: str, error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": msg}]}
    if error:
        out["is_error"] = True
    return out


@dataclass
class _Session:
    root: Path
    finding: dict
    submissions: int = 0
    accepted: dict | None = None
    last_errors: list[str] = field(default_factory=list)


def _options(state: _Session, model: str, max_turns: int, budget: float) -> ClaudeAgentOptions:
    @tool("submit_verdict", "Submit your verdict on the finding. Citations are checked against the files; on "
          f"failure you get the problems and may resubmit ({MAX_SUBMISSIONS} submissions in total).", VERDICT_SCHEMA)
    async def submit(args: dict[str, Any]) -> dict[str, Any]:
        if state.accepted is not None:
            return _text("A verdict was already accepted. Stop now.")
        state.submissions += 1
        errors = validate_verdict(state.root, state.finding, args)
        if errors:
            state.last_errors = errors
            left = MAX_SUBMISSIONS - state.submissions
            msg = "Verdict rejected:\n- " + "\n- ".join(errors)
            msg += f"\n{left} submission(s) left." if left > 0 else "\nNo submissions left. Stop now."
            return _text(msg, error=True)
        state.accepted = args
        return _text("Verdict accepted. Stop now.")

    server = create_sdk_mcp_server(MCP_SERVER, tools=[submit])
    return ClaudeAgentOptions(
        cwd=state.root,
        model=model,
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": VERIFY_INSTRUCTIONS.format(max_turns=max_turns)},
        tools=BUILTIN_TOOLS,
        allowed_tools=BUILTIN_TOOLS + [f"mcp__{MCP_SERVER}__submit_verdict"],
        permission_mode="dontAsk",
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=max_turns,
        max_budget_usd=budget,
    )


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def verify_one(root: Path, f: dict, run_dir: Path, *, model: str, max_turns: int, budget: float) -> dict:
    state = _Session(root=root, finding=f)
    transcript: list[dict] = []
    started = _now()

    def record(event: str) -> None:
        transcript.append({"t": _now(), "event": event})

    result = None
    error = None
    try:
        async with Agent(root=root, on_event=record, options=_options(state, model, max_turns, budget)) as agent:
            result = await agent.send(finding_prompt(f) + "\n\nCheck this finding and submit your verdict.")
            if state.accepted is None and (result is None or not result.is_error):
                result = await agent.send("You have not submitted an accepted verdict. Submit it now with "
                                          "submit_verdict, based on what you found so far.")
    except Exception as e:  # noqa: BLE001 - one failed agent must not stop the run
        error = f"{type(e).__name__}: {e}"
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "transcript.jsonl").open("w", encoding="utf-8") as fh:
        for t in transcript:
            fh.write(json.dumps(t) + "\n")
    record_ = {
        "finding_hash": finding_hash(f),
        "started_at": started,
        "model": model,
        "outcome": (state.accepted or {}).get("outcome") or "unchecked",
        "verdict": state.accepted,
        "submissions": state.submissions,
        "rejections": state.last_errors if state.accepted is None else [],
        "turns": result.num_turns if result else None,
        "cost_usd": result.total_cost_usd if result else None,
        "error": error,
    }
    (run_dir / "verdict.yaml").write_text(yaml.safe_dump(record_, sort_keys=False, allow_unicode=True, width=100),
                                          encoding="utf-8")
    return record_


def _status(f: dict, rec: dict) -> str:
    v = rec.get("verdict")
    if not v:
        return "unchecked"
    if v.get("reclassify"):
        return "reclassified"
    if f.get("items"):
        outs = {i["outcome"] for i in v.get("items") or []}
        if "verified" in outs:
            return "verified"
        if outs == {"withdrawn"}:
            return "withdrawn"
        return "inconclusive"
    return v["outcome"]


def merge(f: dict, rec: dict) -> dict:
    f = dict(f)
    v = rec.get("verdict") or {}
    f["status"] = _status(f, rec)
    if f.get("items") and v:
        per = {i["id"]: i for i in v.get("items") or []}
        f["items"] = [dict(it, status=per.get(_item_id(it), {}).get("outcome", "unchecked"),
                           verify_reason=per.get(_item_id(it), {}).get("reason"),
                           verify_evidence=[v["evidence"][k] for k in per.get(_item_id(it), {}).get("evidence") or []
                                            if 0 <= k < len(v["evidence"])])
                      for it in f["items"]]
    f["verify"] = {k: rec.get(k) for k in ("outcome", "turns", "cost_usd", "submissions", "rejections", "error")}
    if v:
        f["verify"].update({k: v.get(k) for k in ("summary", "resolution", "evidence", "searches", "near_misses",
                                                   "new_sources", "reclassify")})
    return f


def _derived(f: dict) -> dict | None:
    """A reclassified finding becomes a new, verified finding of its new category, backed by
    the evidence the verify agent cited (already checked against the files)."""
    v = f.get("verify") or {}
    rc = v.get("reclassify") if f["status"] == "reclassified" else None
    if not rc:
        return None
    ev = [v["evidence"][k] for k in rc["evidence"] if 0 <= k < len(v.get("evidence") or [])]
    from chipbag.analyze import CATEGORIES
    return {"id": "f_" + hashlib.sha256(f"{f['id']}:{rc['category']}".encode()).hexdigest()[:12],
            "category": rc["category"], "category_no": CATEGORIES[rc["category"]], "type": "reclassified",
            "title": rc["title"], "subject": f["subject"], "detail": {"explanation": rc["explanation"],
                                                                    "resolution": v.get("resolution")},
            "evidence": ev, "origin": f"verify (reclassified from {f['id']})", "severity": f["severity"],
            "status": "verified", "verify": {"outcome": "verified", "summary": rc["explanation"],
                                             "evidence": ev, "searches": v.get("searches")}}


async def run(work_dir: Path, root: Path, *, model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS,
              agent_budget_usd: float = DEFAULT_AGENT_BUDGET_USD, max_cost_usd: float = DEFAULT_MAX_COST_USD,
              limit: int | None = None, only: list[str] | None = None, force: bool = False,
              on_event: Callable[[str], None] = print) -> dict:
    analysis = yaml.safe_load((work_dir / "analysis.yaml").read_text())
    findings = sorted(analysis["findings"], key=lambda f: (CATEGORY_ORDER[f["category"]], SEVERITY[f["severity"]], f["id"]))
    if only:
        findings = [f for f in findings if f["category"] in only or f["id"] in only]
    runs = work_dir / "verify_runs"
    cached, todo = {}, []
    for f in findings:
        path = runs / f["id"] / "verdict.yaml"
        if not force and path.is_file():
            rec = yaml.safe_load(path.read_text())
            if rec.get("finding_hash") == finding_hash(f) and rec.get("verdict"):
                cached[f["id"]] = rec
                continue
        todo.append(f)
    if limit is not None:
        todo = todo[:limit]
    on_event(f"  verify: {len(findings)} findings; {len(cached)} cached; {len(todo)} to check")

    spent = 0.0
    sem = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, dict] = dict(cached)

    async def worker(f: dict) -> None:
        nonlocal spent
        async with sem:
            if spent >= max_cost_usd:
                return
            rec = await verify_one(root, f, runs / f["id"], model=model, max_turns=max_turns, budget=agent_budget_usd)
            spent += rec["cost_usd"] or 0.0
            results[f["id"]] = rec
            on_event(f"  {f['id']} {f['category']}/{f['type']}: {_status(f, rec)} "
                     f"({rec['turns']} turns, ${rec['cost_usd'] or 0:.2f}; total ${spent:.2f})")

    await asyncio.gather(*(worker(f) for f in todo))

    merged = [merge(f, results[f["id"]]) if f["id"] in results else dict(f, status="unchecked") for f in findings]
    merged += [d for f in merged if (d := _derived(f))]
    additions = sorted({s["path"] for f in merged for s in (f.get("verify") or {}).get("new_sources") or []})
    status = Counter(f["status"] for f in merged)
    by_cat = {c: dict(Counter(f["status"] for f in merged if f["category"] == c)) for c in CATEGORY_ORDER}
    items = Counter(it.get("status", "unchecked") for f in merged for it in f.get("items") or [])
    out = {
        "generated_at": _now(),
        "model": model,
        "stats": {"findings": len(merged), "by_status": dict(status), "by_category": by_cat,
                  "group_items": dict(items), "cost_usd_this_run": round(spent, 4),
                  "cost_usd_total": round(sum((r.get("cost_usd") or 0) for r in results.values()), 4)},
        "manifest_additions": additions,
        "findings": merged,
    }
    (work_dir / "findings.yaml").write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100),
                                            encoding="utf-8")
    return out
