"""Analyze: turn the mapping and the normalize/dedup records into candidate findings.

Five finding categories, each a candidate until `verify` checks it against the whole repo:

1. spec_ambiguity      Spec text too ambiguous to write a good test from (an untestable or
                       under-specified requirement). From normalize's ambiguity records,
                       adjudicated by a stronger model: "would two competent DV engineers
                       write different checks because of this?"
2. spec_contradiction  The spec contradicts itself. From normalize's CONFLICT codes (e.g. a
                       register description vs. its property) and dedup's cross-document
                       conflicts.
3. test_inconsistency  A test contradicts its own prose: comments, log messages, its name or
                       the plan item it realizes say one thing and the code does another
                       (including checks that claim to check but can never fail). One LLM
                       pass per test over its extracted intent, steps and checks.
4. spec_test_mismatch  The spec says one thing and a test or plan item expects another. From
                       the mapping's `conflicts` entries, plus partial entries whose
                       missing part is really a contradiction (found by the triage pass).
5. untested            Spec behavior no test verifies: untested requirements (grouped per
                       spec section), partially tested ones (what no test verifies, merged
                       across all tests by the triage pass), planned-only or weak-evidence
                       ones, plan items with no test, and register property categories
                       without a generic suite.

Writes `analysis.yaml`. Every finding carries the spec quotes and test citations it is
based on, all of which were located in the source by earlier stages.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from chipbag.dedup import logged_responses
from chipbag.llm import LLM, BudgetExceeded, LLMError

DEFAULT_MODEL = "claude-sonnet-5-5"
REVIEW_MODEL = "claude-opus-5-5"
DEFAULT_MAX_COST_USD = 5.0
STRONG = {"failing", "unknown"}  # evidence that can fail a regression
GROUP_SIZE = 5  # requirements per untested / partial group finding
TRIAGE_BATCH = 12
QUOTE_CHARS = 300

CATEGORIES = {
    "spec_ambiguity": 1,
    "spec_contradiction": 2,
    "test_inconsistency": 3,
    "spec_test_mismatch": 4,
    "untested": 5,
}


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def _q(q: dict, path: str | None = None) -> dict:
    out = {"path": path or q.get("path"), **{k: q[k] for k in ("lines", "rows", "sheet") if q.get(k)}, "text": q["text"]}
    return out


def _loc(q: dict) -> str:
    if q.get("lines"):
        a, b = q["lines"]
        return f"{q['path']}:{a}" if a == b else f"{q['path']}:{a}-{b}"
    if q.get("rows"):
        return f"{q['path']} ({q.get('sheet')} rows {q['rows'][0]}-{q['rows'][1]})"
    return q["path"]


def _finding(category: str, type_: str, title: str, subject: dict, **rest) -> dict:
    keys = sorted(x for v in subject.values() for x in (v if isinstance(v, list) else [v]))
    return {"id": "f_" + _hash(category, type_, *keys), "category": category, "category_no": CATEGORIES[category],
            "type": type_, "title": title, "subject": subject, **rest, "status": "candidate"}


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


class Ctx:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.reqs_raw = yaml.safe_load((work_dir / "requirements.yaml").read_text())
        self.cases_raw = yaml.safe_load((work_dir / "cases.yaml").read_text())
        self.canon = yaml.safe_load((work_dir / "canonical.yaml").read_text())
        self.mapping = yaml.safe_load((work_dir / "mapping.yaml").read_text())
        self.req = {r["id"]: r for r in self.canon["requirements"]}
        self.raw_req = {r["id"]: r for r in self.reqs_raw["requirements"]}
        self.member_of = {m: r["id"] for r in self.canon["requirements"] for m in r["members"]}
        self.case = {c["id"]: c for c in self.cases_raw["cases"]}
        self.test = {t["id"]: t for t in self.canon["tests"]}
        self.plan = {p["id"]: p for p in self.canon["plans"]}
        self.sections = {s["id"]: s for s in self.reqs_raw["sections"]}
        self.entries_by_req: dict[str, list[dict]] = defaultdict(list)
        for e in self.mapping["entries"]:
            self.entries_by_req[e["req_id"]].append(e)

    def section_of(self, cr_id: str) -> str:
        r = self.req[cr_id]
        secs = [self.raw_req[m]["section_id"] for m in r["members"] if m in self.raw_req]
        # dict.fromkeys keeps first-seen order, so ties break the same way on every run
        return max(dict.fromkeys(secs), key=secs.count) if secs else ""

    def heading(self, section_id: str) -> str:
        s = self.sections.get(section_id)
        return f"{s['path']} > {s['heading_path']}" if s else section_id

    def req_quotes(self, cr_id: str) -> list[dict]:
        return [_q(q) for q in self.req[cr_id]["quotes"]]


# --------------------------------------------------------------------------
# 1. Spec ambiguity (adjudicated)
# --------------------------------------------------------------------------


class AmbVerdict(BaseModel):
    label: str = Field(description="Item label, e.g. a3.")
    keep: bool = Field(description="True if two competent DV engineers would write different checks because of this.")
    impact: str = Field(description="Concretely, which checks would differ, or why it doesn't matter for testing.")
    question: str = Field(description="The precise question the architect must answer (refined from the item's).")
    severity: Literal["high", "medium", "low"]


class AmbOut(BaseModel):
    verdicts: list[AmbVerdict]


AMB_SYSTEM = """\
You review ambiguity flags raised on a hardware specification. Each item is a passage or a
requirement that an earlier pass flagged as vague, under-specified, weakly worded or with
an unclear referent, with that pass's note and question. Where available you also see what
existing tests do for that requirement.

For each item decide `keep` with one test: would two competent DV engineers, reading only
this specification, write different checks (different stimulus, expected values, timing
or pass criteria) because of this ambiguity? Keep it if yes. Drop it if the meaning is
clear in context (read the section text), if it is marketing or overview prose that no
check depends on, or if the missing detail does not change what a test checks.

`impact` names the concrete difference in checks (or why there is none). `question` is the
exact question an architect must answer to make it testable. Severity: high if a central
behavior cannot be checked without guessing; medium if a corner case or one value is
unclear; low otherwise.
"""


def ambiguity_items(ctx: Ctx) -> list[dict]:
    """Ambiguous passages and under-specified requirements, minus CONFLICT codes (category 2)
    and minus ambiguities dedup resolved from another member."""
    items = []
    for o in ctx.reqs_raw["other_text"]:
        a = o.get("ambiguity")
        if o["label"] != "ambiguous" or not a or a["code"] == "CONFLICT":
            continue
        items.append({"kind": "passage", "section_id": o["section_id"], "code": a["code"], "note": a["note"],
                      "question": a["question"], "text": o["quote"]["text"], "spec": [_q(o["quote"], o["path"])],
                      "requirements": []})
    for cr in ctx.canon["requirements"]:
        if cr.get("ambiguity_resolved"):
            continue
        for a in cr.get("ambiguities") or []:
            if a["code"] == "CONFLICT":
                continue
            items.append({"kind": "requirement", "section_id": ctx.section_of(cr["id"]), "code": a["code"],
                          "note": a["note"], "question": a["question"], "text": cr["statement"],
                          "spec": ctx.req_quotes(cr["id"]), "requirements": [cr["id"]]})
    return items


def _amb_prompt(ctx: Ctx, section_id: str, items: list[dict], repo_root: Path | None) -> str:
    parts = [f"Specification section: {ctx.heading(section_id)}"]
    s = ctx.sections.get(section_id)
    if s and repo_root:
        try:
            lines = (repo_root / s["path"]).read_text(encoding="utf-8").splitlines()
            parts.append("\n".join(lines[s["lines"][0] - 1 : s["lines"][1]])[:5000])
        except (OSError, UnicodeDecodeError):
            pass
    parts.append("Flagged items:")
    for k, it in enumerate(items, 1):
        tests = ""
        for rid in it["requirements"]:
            es = [e for e in ctx.entries_by_req.get(rid, []) if e["unit_kind"] == "test"][:3]
            if es:
                tests = "\n  existing tests: " + " | ".join(f"{e['unit_name']}: {e['rationale']}" for e in es)
        parts.append(f"[a{k}] {it['kind']} ({it['code']}): {it['text']}\n  note: {it['note']}\n"
                     f"  question: {it['question']}{tests}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# 3. Test inconsistency (prose vs code, per test)
# --------------------------------------------------------------------------


class TestIssue(BaseModel):
    kind: Literal["comment_vs_code", "message_vs_condition", "plan_vs_test", "name_vs_behavior", "check_cannot_fail"]
    description: str = Field(description="What the prose says, what the code does instead, and why it matters.")
    prose: list[str] = Field(description="Labels of the prose involved: i1 (intent), p1 / p1.k2 (plan item), or 'name'.")
    code: list[str] = Field(description="Labels of the code involved: s1 (step), k2 (check).")
    severity: Literal["high", "medium", "low"]


class TestReview(BaseModel):
    issues: list[TestIssue]


TEST_SYSTEM = """\
You review one hardware verification test for internal consistency between what its prose
says and what its code does. You get the test's name, its intent (exact comments,
descriptions and log messages, labelled i1, ...), the plan items it is said to realize (p1,
with intended checks p1.k1, ...), and its extracted stimulus steps (s1, ...) and checks (k1,
...) with exact code excerpts and whether a mismatch fails the test.

Report only real inconsistencies:
- comment_vs_code: a comment or description says the code does X; the code does Y.
- message_vs_condition: an error or log message describes a condition different from the
  one the code tests (e.g. the message says 'should be 1' but the code checks for 0).
- plan_vs_test: the plan item says the test checks or drives X, and the test does not, or
  does something incompatible.
- name_vs_behavior: the test name promises a scenario the test does not exercise.
- check_cannot_fail: something presented as a check (comment, message, plan) can never fail
  the test: it only prints, its result is discarded, or its condition is always true.

Do not report missing features, style, or checks you merely would have added. Returning no
issues is the expected answer for most tests. Cite only labels shown.
"""


def _test_units(ctx: Ctx) -> list[dict]:
    """Implemented tests and checkers with something to compare: intent, plan links or checks."""
    plans_of: dict[str, list[str]] = defaultdict(list)
    for ln in ctx.mapping.get("plan_links") or []:
        if ln["relation"] in ("realizes", "partially_realizes"):
            plans_of[ln["test_id"]].append(ln["plan_id"])
    out = []
    for t in ctx.canon["tests"]:
        if t["status"] != "implemented" or t.get("role") == "coverage":
            continue
        members = [ctx.case[i] for i in t["implemented"] if i in ctx.case]
        intent = [dict(q, path=m["path"]) for m in members for q in m["intent"]]
        steps = [dict(s, path=m["path"], case_id=m["id"]) for m in members for s in m["stimulus"]]
        checks = [dict(k, path=m["path"], case_id=m["id"]) for m in members for k in m["checks"]]
        plans = [dict(ctx.plan[p], checks=[dict(c, path=ctx.case[c["case_id"]]["path"]) if c["case_id"] in ctx.case
                                           else dict(c, path=ctx.plan[p]["sources"][0]) for c in ctx.plan[p]["checks"]])
                 for p in sorted(set(plans_of[t["id"]])) if p in ctx.plan]
        if checks and (intent or plans):
            out.append({"test": t, "intent": intent, "steps": steps, "checks": checks, "plans": plans})
    return out


def _test_prompt(u: dict) -> str:
    t = u["test"]
    parts = [f"Test: {t['name']} ({t.get('role', 'test')})\nFiles: {', '.join(t['files'])}\nSummary: {t['summary']}"]
    if u["intent"]:
        parts.append("Intent:\n" + "\n".join(f"  i{k} {_loc(q)}: {q['text'][:QUOTE_CHARS]}" for k, q in enumerate(u["intent"], 1)))
    for k, p in enumerate(u["plans"], 1):
        checks = "\n".join(f"    p{k}.k{j} {c['text']}" for j, c in enumerate(p["checks"], 1))
        parts.append(f"Plan item p{k}: {p['name']}\n  {p['summary']}\n{checks}")
    parts.append("Stimulus:\n" + ("\n".join(
        f"  s{k} {s['text']}  `{(s['quotes'][0]['text'] if s['quotes'] else '')[:200]}` ({_loc(dict(s['quotes'][0], path=s['path'])) if s['quotes'] else s['path']})"
        for k, s in enumerate(u["steps"], 1)) or "  none"))
    tag = {"yes": "fails test", "no": "only reports", "unknown": "failure handling unknown"}
    parts.append("Checks:\n" + "\n".join(
        f"  k{k} [{tag[c['fails_test']]}] {c['text']}  `{(c['quotes'][0]['text'] if c['quotes'] else '')[:200]}` "
        f"({_loc(dict(c['quotes'][0], path=c['path'])) if c['quotes'] else c['path']})"
        for k, c in enumerate(u["checks"], 1)))
    return "\n\n".join(parts)


LABEL_RE = re.compile(r"\b(?:[iskp]\d+(?:\.k\d+)?)\b")


def _label_locations(u: dict, text: str) -> dict[str, list[dict]]:
    """Where each citation label named in a description points, so a reader can follow it.
    Kept out of the finding hash (display only)."""
    out: dict[str, list[dict]] = {}
    for lab in dict.fromkeys(LABEL_RE.findall(text or "")):
        cites, bad = _resolve_test_labels(u, [lab])
        locs = [{k: c[k] for k in ("path", "lines", "rows", "sheet") if c.get(k)} for c in cites if c.get("path")]
        if locs and not bad:
            out[lab] = locs
    return out


def _resolve_test_labels(u: dict, labels: list[str]) -> tuple[list[dict], list[str]]:
    cites, bad = [], []
    for lab in labels:
        lab = lab.strip()
        try:
            if lab == "name":
                cites.append({"kind": "name", "text": u["test"]["name"]})
            elif lab.startswith("i"):
                q = u["intent"][int(lab[1:]) - 1]
                cites.append({"kind": "intent", **_q(q)})
            elif lab.startswith("s"):
                s = u["steps"][int(lab[1:]) - 1]
                cites += [{"kind": "stimulus", "statement": s["text"], **_q(q, s["path"])} for q in s["quotes"]]
            elif lab.startswith("k"):
                c = u["checks"][int(lab[1:]) - 1]
                cites += [{"kind": "check", "statement": c["text"], "fails_test": c["fails_test"], **_q(q, c["path"])}
                          for q in c["quotes"]]
            elif lab.startswith("p"):
                head, _, sub = lab.partition(".")
                p = u["plans"][int(head[1:]) - 1]
                if sub:
                    c = p["checks"][int(sub[1:]) - 1]
                    cites += [{"kind": "plan", "plan_id": p["id"], "statement": c["text"], **_q(q, c["path"])}
                              for q in c["quotes"]]
                else:
                    cites.append({"kind": "plan", "plan_id": p["id"], "text": p["summary"], "path": p["sources"][0]})
            else:
                bad.append(lab)
        except (ValueError, IndexError):
            bad.append(lab)
    return cites, bad


# --------------------------------------------------------------------------
# 4/5. Partial triage: contradiction, real gap, or negligible
# --------------------------------------------------------------------------


class TriageVerdict(BaseModel):
    label: str = Field(description="Requirement label, e.g. r2.")
    classification: Literal["contradiction", "gap", "negligible"]
    gap: str | None = Field(description="For gap: what part of the requirement NO listed test verifies, as a "
                            "concrete test suggestion. For contradiction: what the test expects instead.")
    entries: list[str] = Field(description="Labels of the entries this is based on (e1, ...).")
    severity: Literal["high", "medium", "low"]


class TriageOut(BaseModel):
    verdicts: list[TriageVerdict]


TRIAGE_SYSTEM = """\
You triage partially tested hardware requirements. For each requirement (r1, ...) you get
every test that verifies part of it (e1, ...), with what the test does and what it leaves
unverified ('missing').

Classify each requirement:
- contradiction: some test expects or asserts behavior different from the requirement (a
  different value, range, owner, order or condition), not merely less of it. `gap` states
  what the test expects instead; cite that entry.
- gap: a meaningful part of the requirement is verified by NO listed test. A part missing
  from one test but verified by another is not a gap. `gap` states the remaining
  unverified part as a concrete test suggestion (condition, stimulus, expected result).
- negligible: what remains unverified would not change a DV engineer's test plan (e.g.
  'only one random value', 'the meaning of a status beyond its encoding', exhaustive
  iteration where one representative case is the norm).

Severity: high if a central behavior or an error/security path is unverified or
contradicted; medium for a secondary condition or instance; low otherwise.
"""


def _strong(es: list[dict]) -> list[dict]:
    return [e for e in es if e["unit_kind"] == "test" and e["strength"] in STRONG]


def coverage_state(ctx: Ctx, cr_id: str) -> str:
    es = [e for e in ctx.entries_by_req.get(cr_id, []) if e["relation"] in ("covers", "partial")]
    strong = _strong(es)
    if any(e["relation"] == "covers" for e in strong):
        return "tested"
    if strong:
        return "partial"
    if any(e["unit_kind"] == "plan" for e in es):
        return "planned_only"
    if es:
        return "weak_only"
    return "untested"


def _triage_prompt(ctx: Ctx, reqs: list[str]) -> tuple[str, dict[str, tuple[str, dict]]]:
    labels, parts, n = {}, [], 0
    for k, rid in enumerate(reqs, 1):
        r = ctx.req[rid]
        labels[f"r{k}"] = (rid, {})
        lines = [f"[r{k}] {r['statement']}"]
        for e in sorted(_strong(ctx.entries_by_req[rid]), key=lambda e: e["unit_id"]):
            n += 1
            labels[f"r{k}"][1][f"e{n}"] = e
            lines.append(f"  e{n} {e['unit_name']} ({e['relation']}): {e['rationale']}"
                         + (f"\n     missing: {e['missing']}" if e.get("missing") else ""))
        parts.append("\n".join(lines))
    return "Partially tested requirements:\n\n" + "\n\n".join(parts), labels


# --------------------------------------------------------------------------
# Building findings
# --------------------------------------------------------------------------


def _doc_order(ctx: Ctx, rid: str) -> tuple:
    s = ctx.sections.get(ctx.section_of(rid)) or {}
    return (s.get("path", ""), (s.get("lines") or [0])[0], rid)


def _doc_groups(ctx: Ctx, rids: list[str]) -> list[list[str]]:
    """Chunks of at most GROUP_SIZE requirements from the same source file, in document order."""
    by_file: dict[str, list[str]] = defaultdict(list)
    for rid in sorted(rids, key=lambda r: _doc_order(ctx, r)):
        by_file[_doc_order(ctx, rid)[0]].append(rid)
    return [rs[k : k + GROUP_SIZE] for _, rs in sorted(by_file.items()) for k in range(0, len(rs), GROUP_SIZE)]


def _group_title(ctx: Ctx, rids: list[str]) -> str:
    heads = [ctx.heading(ctx.section_of(r)) for r in rids]
    return heads[0] if len(set(heads)) == 1 else f"{heads[0]} … ({len(set(heads))} sections)"


def _entry_cites(e: dict) -> list[dict]:
    return [{k: v for k, v in c.items() if k != "case_id"} for c in e["unit_cite"]]


def deterministic_findings(ctx: Ctx) -> list[dict]:
    out = []
    # 2. Spec contradictions: normalize CONFLICT flags and dedup conflicts.
    for o in ctx.reqs_raw["other_text"]:
        a = o.get("ambiguity")
        if o["label"] == "ambiguous" and a and a["code"] == "CONFLICT":
            out.append(_finding("spec_contradiction", "within_section", a["note"][:120],
                                {"section": o["section_id"], "quote": _hash(o["quote"]["text"])},
                                spec=[_q(o["quote"], o["path"])], detail={"note": a["note"], "question": a["question"]},
                                origin="normalize", severity="high"))
    for cr in ctx.canon["requirements"]:
        for a in cr.get("ambiguities") or []:
            if a["code"] == "CONFLICT":
                out.append(_finding("spec_contradiction", "description_vs_definition", a["note"][:120],
                                    {"requirements": [cr["id"]]}, spec=ctx.req_quotes(cr["id"]),
                                    detail={"statement": cr["statement"], "note": a["note"], "question": a["question"]},
                                    origin="normalize", severity="high"))
    for cx in ctx.canon.get("conflicts") or []:
        out.append(_finding("spec_contradiction", "cross_document", cx["subject"], {"requirements": cx["requirements"]},
                            spec=[q for rid in cx["requirements"] for q in ctx.req_quotes(rid)],
                            detail={"statements": [ctx.req[r]["statement"] for r in cx["requirements"]],
                                    "difference": cx["difference"], "question": cx["question"]},
                            origin="dedup", severity="high"))

    # 4. Mapping conflicts.
    for e in ctx.mapping["entries"]:
        if e["relation"] == "conflicts":
            out.append(_finding("spec_test_mismatch", "plan_conflict" if e["unit_kind"] == "plan" else "test_conflict",
                                f"{e['unit_name']} contradicts: {ctx.req[e['req_id']]['statement'][:90]}",
                                {"requirements": [e["req_id"]], "units": [e["unit_id"]]},
                                spec=e["req_cite"], tests=_entry_cites(e),
                                detail={"statement": ctx.req[e["req_id"]]["statement"], "unit": e["unit_name"],
                                        "rationale": e["rationale"]},
                                origin="correlate", severity="high"))

    # 5. Untested, planned-only and weak-only requirements, grouped in document order (same
    # source file, consecutive sections), at most GROUP_SIZE per group.
    by_state: dict[str, list[str]] = defaultdict(list)
    for rid, r in ctx.req.items():
        if r["tier"] != "needs_test":
            continue
        state = coverage_state(ctx, rid)
        if state in ("untested", "planned_only", "weak_only"):
            by_state[state].append(rid)
    for state, rids in sorted(by_state.items()):
        for chunk in _doc_groups(ctx, rids):
            items = []
            for rid in chunk:
                weak = [{"unit": e["unit_name"], "kind": e["unit_kind"], "strength": e["strength"],
                         "relation": e["relation"], "rationale": e["rationale"]}
                        for e in ctx.entries_by_req.get(rid, []) if e["relation"] in ("covers", "partial")]
                items.append({"req_id": rid, "statement": ctx.req[rid]["statement"],
                              "section": ctx.heading(ctx.section_of(rid)), "spec": ctx.req_quotes(rid),
                              "weak_evidence": weak})
            title = {"untested": "Untested", "planned_only": "Planned but not implemented",
                     "weak_only": "Only weak evidence (non-failing checks, coverage, list entries)"}[state]
            out.append(_finding("untested", state, f"{title}: {len(chunk)} requirement(s) in {_group_title(ctx, chunk)}",
                                {"requirements": chunk}, section=_group_title(ctx, chunk), items=items,
                                origin="mapping", severity="medium"))
    # Plan items with no implementing test.
    linked = {ln["plan_id"] for ln in ctx.mapping.get("plan_links") or []}
    orphans = sorted(p for p in ctx.plan if p not in linked)
    for k in range(0, len(orphans), GROUP_SIZE):
        chunk = orphans[k : k + GROUP_SIZE]
        out.append(_finding("untested", "plan_item_not_implemented",
                            f"{len(chunk)} plan item(s) with no implementing test", {"plans": chunk},
                            items=[{"plan_id": p, "name": ctx.plan[p]["name"], "summary": ctx.plan[p]["summary"],
                                    "sources": ctx.plan[p]["sources"]} for p in chunk],
                            origin="correlate", severity="medium"))
    # Register property categories without an all-register suite.
    rs = ctx.mapping.get("register_suites") or {}
    for cat, v in (rs.get("categories") or {}).items():
        if v["status"] in ("no_suite", "some_registers_only", "planned_only"):
            out.append(_finding("untested", "register_suite", f"Register {cat}: {v['status']} ({v['requirements']} properties)",
                                {"register_category": cat}, detail=v, origin="correlate", severity="low"))
    return out


async def run(work_dir: Path, *, model: str = DEFAULT_MODEL, review_model: str = REVIEW_MODEL,
              max_cost_usd: float = DEFAULT_MAX_COST_USD, from_log: bool = False, repo_root: Path | None = None,
              on_event: Callable[[str], None] = print) -> dict:
    ctx = Ctx(work_dir)
    log = work_dir / "llm_calls.jsonl"
    llm = LLM(model, log, max_cost_usd)
    review = LLM(review_model, log, max_cost_usd)
    cached = {p: logged_responses(log, p, m) if from_log else {}
              for p, m in (("analyze_ambiguity", AmbOut), ("analyze_test", TestReview), ("analyze_triage", TriageOut))}
    errors: list[str] = []

    async def call(client: LLM, purpose: str, system: str, prompt: str, output):
        hit = cached[purpose].get(prompt)
        if hit is not None:
            return hit
        try:
            return await client.parse(purpose, system, prompt, output)
        except (BudgetExceeded, LLMError) as e:
            errors.append(f"{purpose}: {e}")
            on_event(f"  ! {purpose}: {e}")
            return None

    findings = deterministic_findings(ctx)

    # 1. Ambiguity adjudication, per section.
    amb = ambiguity_items(ctx)
    by_sec: dict[str, list[dict]] = defaultdict(list)
    for it in amb:
        by_sec[it["section_id"]].append(it)
    amb_jobs = [(sid, its, _amb_prompt(ctx, sid, its, repo_root)) for sid, its in sorted(by_sec.items())]

    # 3. Test reviews.
    tunits = _test_units(ctx)
    test_jobs = [(u, _test_prompt(u)) for u in tunits]

    # 5/4. Partial triage.
    partial = sorted(rid for rid, r in ctx.req.items() if r["tier"] == "needs_test" and coverage_state(ctx, rid) == "partial")
    tri_jobs = []
    for k in range(0, len(partial), TRIAGE_BATCH):
        prompt, labels = _triage_prompt(ctx, partial[k : k + TRIAGE_BATCH])
        tri_jobs.append((labels, prompt))
    on_event(f"  analyze: {len(amb)} ambiguity flags in {len(amb_jobs)} sections; {len(test_jobs)} tests to review; "
             f"{len(partial)} partially tested requirements in {len(tri_jobs)} batches")

    amb_outs, test_outs, tri_outs = await asyncio.gather(
        asyncio.gather(*(call(review, "analyze_ambiguity", AMB_SYSTEM, p, AmbOut) for _, _, p in amb_jobs)),
        asyncio.gather(*(call(llm, "analyze_test", TEST_SYSTEM, p, TestReview) for _, p in test_jobs)),
        asyncio.gather(*(call(llm, "analyze_triage", TRIAGE_SYSTEM, p, TriageOut) for _, p in tri_jobs)),
    )

    dropped = defaultdict(int)
    for (sid, its, _), out in zip(amb_jobs, amb_outs):
        if out is None:
            continue
        for v in out.verdicts:
            if not v.label.startswith("a") or not v.label[1:].isdigit() or not 1 <= int(v.label[1:]) <= len(its):
                dropped["ambiguity_bad_label"] += 1
                continue
            it = its[int(v.label[1:]) - 1]
            if not v.keep:
                dropped["ambiguity_not_test_relevant"] += 1
                continue
            subject = {"requirements": it["requirements"]} if it["requirements"] else {
                "section": sid, "quote": _hash(it["text"])}
            findings.append(_finding("spec_ambiguity", it["code"].lower(), it["text"][:120], subject,
                                     section=ctx.heading(sid), spec=it["spec"],
                                     detail={"text": it["text"], "note": it["note"], "impact": v.impact,
                                             "question": v.question},
                                     origin="normalize+review", severity=v.severity))

    for (u, _), out in zip(test_jobs, test_outs):
        if out is None:
            continue
        for issue in out.issues:
            prose, bad1 = _resolve_test_labels(u, issue.prose)
            code, bad2 = _resolve_test_labels(u, issue.code)
            if bad1 or bad2 or not code:
                dropped["test_issue_bad_citation"] += 1
                continue
            t = u["test"]
            findings.append(_finding("test_inconsistency", issue.kind, f"{t['name']}: {issue.description[:100]}",
                                     {"units": [t["id"]], "kind": issue.kind, "d": _hash(issue.description)},
                                     tests=code, prose=prose, detail={"test": t["name"], "files": t["files"],
                                                                      "description": issue.description},
                                     origin="analyze", severity=issue.severity))
            findings[-1]["labels"] = _label_locations(u, issue.description)

    partial_groups: dict[str, list[dict]] = defaultdict(list)
    for (labels, _), out in zip(tri_jobs, tri_outs):
        if out is None:
            continue
        for v in out.verdicts:
            if v.label not in labels:
                dropped["triage_bad_label"] += 1
                continue
            rid, elabels = labels[v.label]
            es = [elabels[x] for x in v.entries if x in elabels]
            if v.classification == "negligible":
                dropped["partial_negligible"] += 1
            elif v.classification == "contradiction" and es:
                e = es[0]
                findings.append(_finding("spec_test_mismatch", "partial_contradiction",
                                         f"{e['unit_name']} expects otherwise: {ctx.req[rid]['statement'][:90]}",
                                         {"requirements": [rid], "units": [e["unit_id"]]},
                                         spec=e["req_cite"], tests=_entry_cites(e),
                                         detail={"statement": ctx.req[rid]["statement"], "unit": e["unit_name"],
                                                 "rationale": e["rationale"], "missing": e.get("missing"),
                                                 "contradiction": v.gap},
                                         origin="correlate+triage", severity=v.severity))
            else:
                partial_groups[ctx.section_of(rid)].append({
                    "req_id": rid, "statement": ctx.req[rid]["statement"], "section": ctx.heading(ctx.section_of(rid)),
                    "spec": ctx.req_quotes(rid),
                    "gap": v.gap, "severity": v.severity,
                    "tested_by": [{"unit": e["unit_name"], "rationale": e["rationale"], "missing": e.get("missing"),
                                   "cites": _entry_cites(e)[:3]} for e in _strong(ctx.entries_by_req[rid])]})
    partial_items = {i["req_id"]: i for items in partial_groups.values() for i in items}
    for chunk_ids in _doc_groups(ctx, list(partial_items)):
        chunk = [partial_items[r] for r in chunk_ids]
        sev = "high" if any(i["severity"] == "high" for i in chunk) else "medium"
        findings.append(_finding("untested", "partially_tested",
                                 f"Partially tested: {len(chunk)} requirement(s) in {_group_title(ctx, chunk_ids)}",
                                 {"requirements": chunk_ids}, section=_group_title(ctx, chunk_ids),
                                 items=chunk, origin="correlate+triage", severity=sev))

    # Deduplicate by id (e.g. the same quote flagged twice).
    uniq = {}
    for f in findings:
        uniq.setdefault(f["id"], f)
    findings = sorted(uniq.values(), key=lambda f: (f["category_no"], {"high": 0, "medium": 1, "low": 2}[f["severity"]], f["id"]))
    counts = defaultdict(lambda: defaultdict(int))
    for f in findings:
        counts[f["category"]][f["type"]] += 1
    result = {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": {"bulk": model, "review": review_model},
        "stats": {"findings": len(findings), "by_category": {k: dict(v) for k, v in counts.items()},
                  "dropped": dict(dropped), "errors": len(errors),
                  "cost_usd": round(llm.spent_usd + review.spent_usd, 4)},
        "errors": errors,
        "findings": findings,
    }
    (work_dir / "analysis.yaml").write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=100),
                                            encoding="utf-8")
    return result
