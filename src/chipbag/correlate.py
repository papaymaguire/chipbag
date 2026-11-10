"""Correlate: map canonical requirements to the tests and plan items that verify them.

Three passes over canonical.yaml, all writing mapping.yaml:

1. Requirements <-> units. Candidates by BM25 in both directions (each `needs_test`
   requirement retrieves its top-k units, each unit its top-k requirements); the union is
   judged by the LLM in batches per spec section, with the section text as context. The
   model returns only related pairs (covers | partial | conflicts) and cites evidence by
   label (requirement quotes q1.., test checks k1.. and stimulus steps s1..). Those labels
   point at quotes normalize already located, so code turns them into file:line citations;
   the model cannot cite text that isn't there. Evidence strength (failing, non-failing,
   planned, listed-only, coverage) is computed by code from the cited checks.
2. Plan items -> tests: the link dedup leaves open when no name ties them. BM25 candidates
   per plan item, judged by the LLM (realizes | partially_realizes).
3. Register suites: `by_construction` requirements are not mapped one by one. One call
   identifies the generic register tests and plan items (reset values, read/write,
   bit-bash, ...); each property category is covered as a group by the matching suites.

Shared checkers (scoreboards, bound assertions) are linked to the tests they run with by
dedup (`checked_by`). A batch shows each linked checker once (c1, ...), and a test's pair
may cite the checker's checks (c1.k2): stimulus from the test plus checks from the checker
is one piece of evidence. A linked checker is not judged on its own; checkers with no
known tests are.

The top-1 BM25 unit per requirement is kept as the evaluation baseline, and the full
candidate set is kept so candidate recall can be measured against a gold set.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field

from chipbag.dedup import logged_responses, tokens
from chipbag.index import _glob_match
from chipbag.llm import LLM, PRICES, BudgetExceeded, LLMError

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_COST_USD = 10.0
CONFIRM_ABOVE_USD = 3.0
TOP_K = 8  # BM25 neighbors per requirement and per unit
MAX_UNITS_PER_REQ = 16  # candidate units per requirement after merging both directions
CHECKER_TESTS_PER_HIT = 3  # a scoped checker hit is replaced by this many of its tests
MAX_REQS_PER_BATCH = 10
MAX_UNITS_PER_BATCH = 40
PLAN_TOP_K = 6  # candidate tests per plan item
MAX_PLANS_PER_BATCH = 4
SECTION_CHARS = 5000  # spec section context shown per batch
QUOTE_CHARS = 300
CHECK_QUOTE_CHARS = 160
MAX_CHECKS = 40
MAX_STEPS = 15
# Cost estimate, calibrated on logged correlate calls (code-heavy prompts).
EST_CHARS_PER_TOKEN = 2.0
EST_OUTPUT_TOKENS = 1800

# By-construction register properties, by what a generic register suite must do to exercise
# them (general SystemRDL/Hjson semantics). A category is satisfied by any suite check in its set.
PROPERTY_CATEGORY = {
    "reset": "reset_values", "resval": "reset_values",
    "sw": "access", "swaccess": "access", "hw": "access", "hwaccess": "access", "onread": "access",
    "onwrite": "access", "rclr": "access", "rset": "access", "woclr": "access", "woset": "access",
    "swmod": "access", "swacc": "access", "bits": "access",
    "regwen": "write_enable_locks",
}
CATEGORY_SUITES = {
    "reset_values": {"reset_values"},
    "access": {"read_write", "bit_bash"},
    "write_enable_locks": {"write_enable_locks"},
    "other": {"reset_values", "read_write", "bit_bash", "aliasing", "write_enable_locks", "other"},
}


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) if docs else 0.0
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.tf):
            for w in t:
                self.postings[w].append(i)

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.tf))
        norm = self.k1 * (1 - self.b + self.b * self.len / (self.avg or 1.0))
        for w in set(query):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i in self.postings[w]:
                f = self.tf[i][w]
                out[i] += idf * f * (self.k1 + 1) / (f + norm[i])
        return out


def _top(scores: np.ndarray, k: int) -> list[tuple[int, float]]:
    idx = np.argsort(-scores, kind="stable")[:k]
    return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]


# --------------------------------------------------------------------------
# Units as the model sees them
# --------------------------------------------------------------------------


def _loc(q: dict) -> str:
    if q.get("lines"):
        a, b = q["lines"]
        return f"L{a}" if a == b else f"L{a}-{b}"
    if q.get("rows"):
        a, b = q["rows"]
        return f"{q.get('sheet') or 'sheet'} row {a}" if a == b else f"{q.get('sheet') or 'sheet'} rows {a}-{b}"
    return "?"


def _cite(q: dict, path: str, **extra) -> dict:
    out = {"path": path, **{k: q[k] for k in ("lines", "rows", "sheet") if q.get(k)}, "text": q["text"]}
    return {**out, **extra}


@dataclass
class Req:
    id: str
    statement: str
    aspect: str
    quotes: list[dict]  # each with path
    section_id: str
    heading_paths: list[str]
    ambiguities: list[dict]
    properties: list[str] = field(default_factory=list)  # register properties of the members

    def block(self) -> str:
        lines = [f"statement: {self.statement}  (aspect: {self.aspect})"]
        for a in self.ambiguities[:2]:
            lines.append(f"under-specified ({a.get('code')}): {a.get('note')}")
        for k, q in enumerate(self.quotes, 1):
            lines.append(f"q{k} {q['path']} {_loc(q)}: {q['text'][:QUOTE_CHARS]}")
        return "\n  ".join(lines)

    def search_text(self) -> str:
        return self.statement + " " + " ".join(q["text"] for q in self.quotes)


@dataclass
class Unit:
    id: str
    kind: Literal["test", "plan"]
    name: str
    summary: str
    status: str  # implemented | listed_only | planned
    files: list[str]
    listings: list[str]
    role: str = "test"  # test | checker | coverage (plans: test)
    checked_by: list[str] = field(default_factory=list)  # checker unit ids
    checks: list[dict] = field(default_factory=list)  # each: case_id, path, text, fails_test, quotes
    steps: list[dict] = field(default_factory=list)  # each: case_id, path, text, quotes

    def block(self, checker_labels: dict[str, str] | None = None) -> str:
        if self.kind == "plan":
            head = "test-plan item"
        elif self.role == "checker":
            head = "checker (passive: checks while tests run, drives no stimulus)"
        elif self.role == "coverage":
            head = "coverage collector (records that scenarios occurred; checks nothing)"
        elif self.status == "listed_only":
            head = "test (only listed in a regression; no code found)"
        else:
            head = "test"
        lines = [f"{head}: {self.name}"]
        if self.files:
            lines.append("files: " + ", ".join(self.files[:6]))
        if self.listings:
            lines.append("listed in: " + ", ".join(self.listings[:4]))
        lines.append(f"summary: {self.summary}")
        if checker_labels is not None:
            linked = [checker_labels[c] for c in self.checked_by if c in checker_labels]
            if linked:
                lines.append("also checked by: " + ", ".join(linked))
        if self.steps:
            lines.append("observes:" if self.role != "test" else "stimulus:")
            for k, s in enumerate(self.steps[:MAX_STEPS], 1):
                lines.append(f"  s{k} {s['text']}")
        if self.checks:
            lines.append("coverage points:" if self.role == "coverage" else "checks:")
            for k, c in enumerate(self.checks[:MAX_CHECKS], 1):
                tag = {"yes": "fails test", "no": "only reports", "unknown": "failure handling unknown"}[c["fails_test"]]
                quote = c["quotes"][0]["text"][:CHECK_QUOTE_CHARS] if c["quotes"] and self.kind == "test" else ""
                lines.append(f"  k{k} [{tag}] {c['text']}" + (f"  `{quote}`" if quote else ""))
        else:
            lines.append("checks: none shown")
        return "\n  ".join(lines)

    def search_text(self) -> str:
        return " ".join([self.name, self.summary, *(c["text"] for c in self.checks), *(s["text"] for s in self.steps),
                         *(q["text"] for c in self.checks for q in c["quotes"])])


def load_units(work_dir: Path) -> tuple[list[Req], list[Req], list[Unit], dict]:
    """(needs_test requirements, by_construction requirements, units, raw canonical)."""
    canon = yaml.safe_load((work_dir / "canonical.yaml").read_text())
    raw_reqs = {r["id"]: r for r in yaml.safe_load((work_dir / "requirements.yaml").read_text())["requirements"]}
    raw_cases = {c["id"]: c for c in yaml.safe_load((work_dir / "cases.yaml").read_text())["cases"]}

    needs, by_con = [], []
    for c in canon.get("requirements") or []:
        members = [raw_reqs[m] for m in c["members"] if m in raw_reqs]
        secs = Counter(m["section_id"] for m in members)
        req = Req(id=c["id"], statement=c["statement"], aspect=c.get("aspect") or "", quotes=c["quotes"],
                  section_id=secs.most_common(1)[0][0] if secs else "",
                  heading_paths=sorted({m["heading_path"] for m in members}),
                  ambiguities=c.get("ambiguities") or [],
                  properties=sorted({m["property"] for m in members if m.get("property")}))
        (needs if c["tier"] == "needs_test" else by_con).append(req)

    def steps_of(case_ids: list[str]) -> list[dict]:
        return [{"case_id": i, "path": raw_cases[i]["path"], "text": s["text"], "quotes": s["quotes"]}
                for i in case_ids if i in raw_cases for s in raw_cases[i]["stimulus"]]

    def checks_of(checks: list[dict]) -> list[dict]:
        return [{**k, "path": raw_cases[k["case_id"]]["path"]} for k in checks if k["case_id"] in raw_cases]

    units = []
    for t in canon.get("tests") or []:
        units.append(Unit(id=t["id"], kind="test", name=t["name"], summary=t["summary"], status=t["status"],
                          files=t["files"], listings=t["listings"], role=t.get("role", "test"),
                          checked_by=t.get("checked_by") or [], checks=checks_of(t["checks"]),
                          steps=steps_of(t["implemented"])))
    for p in canon.get("plans") or []:
        units.append(Unit(id=p["id"], kind="plan", name=p["name"], summary=p["summary"], status="planned",
                          files=p["sources"], listings=[], checks=checks_of(p["checks"]), steps=steps_of(p["members"])))
    return needs, by_con, units, canon


def _scoped_checkers(units: list[Unit]) -> dict[str, list[str]]:
    """Checker id -> ids of the tests it runs with (only checkers with at least one)."""
    out: dict[str, list[str]] = defaultdict(list)
    for u in units:
        for c in u.checked_by:
            out[c].append(u.id)
    return dict(out)


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


def candidates(reqs: list[Req], units: list[Unit], top_k: int = TOP_K) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Candidate units per requirement (forward hits first, then reverse hits), and the
    BM25 top-1 baseline per requirement. A hit on a checker that runs with known tests is
    replaced by the best-scoring of those tests: the checker is judged through them."""
    if not reqs or not units:
        return {}, {}
    req_tok = [tokens(r.search_text()) for r in reqs]
    unit_tok = [tokens(u.search_text()) for u in units]
    unit_index, req_index = BM25(unit_tok), BM25(req_tok)
    pos = {u.id: j for j, u in enumerate(units)}
    scoped = _scoped_checkers(units)
    fwd_scores = [unit_index.scores(req_tok[i]) for i in range(len(reqs))]
    forward = {i: _top(fwd_scores[i], top_k) for i in range(len(reqs))}
    reverse: dict[int, list[tuple[int, int]]] = defaultdict(list)  # req -> [(rank in unit's list, unit)]
    for j in range(len(units)):
        for rank, (i, _) in enumerate(_top(req_index.scores(unit_tok[j]), top_k)):
            reverse[i].append((rank, j))
    cands, baseline = {}, {}
    for i, r in enumerate(reqs):
        hits = [j for j, _ in forward[i]]
        hits += [j for _, j in sorted(reverse[i]) if j not in hits]
        chosen: list[str] = []
        for j in hits:
            uid = units[j].id
            if uid in scoped:
                tests = sorted(scoped[uid], key=lambda t: -fwd_scores[i][pos[t]])[:CHECKER_TESTS_PER_HIT]
                chosen += [t for t in tests if t not in chosen]
            elif uid not in chosen:
                chosen.append(uid)
        cands[r.id] = chosen[:MAX_UNITS_PER_REQ]
        if forward[i]:
            j, s = forward[i][0]
            baseline[r.id] = {"unit_id": units[j].id, "score": round(s, 3)}
    return cands, baseline


# --------------------------------------------------------------------------
# Pass 1: requirement <-> unit judgment
# --------------------------------------------------------------------------


class RelationOut(BaseModel):
    requirement: str = Field(description="Requirement label, e.g. r2.")
    unit: str = Field(description="Unit label, e.g. t3, p1, or c2 for a checker no test lists.")
    relation: Literal["covers", "partial", "conflicts"]
    rationale: str = Field(description="One or two sentences: what the unit does that bears on the requirement.")
    missing: str | None = Field(description="For partial: what part of the requirement is not verified. Otherwise null.")
    requirement_quotes: list[str] = Field(description="Labels of the requirement quotes involved (q1, q2, ...).")
    evidence: list[str] = Field(
        description="Labels of the unit's checks and stimulus steps (k1, s2, ...) and of checks of checkers "
        "listed under it (c1.k3)."
    )


class BatchOut(BaseModel):
    relations: list[RelationOut]


EVIDENCE_RULES = """\
Checkers
- A checker (c1, ...) is passive: a scoreboard, reference model or assertion that checks
  behavior while tests run. A test that lists "also checked by: c1" runs with c1, so the
  test's stimulus together with c1's checks is one piece of evidence: relate the test and
  cite c1's checks as c1.k2. Do not return pairs for a checker that some test lists;
  return pairs for the tests instead.
- A coverage collector only records that a scenario occurred. It never checks behavior:
  relate it at most as partial, with missing: a check of the outcome.
"""

SYSTEM = f"""\
You judge which verification units verify which hardware requirements. You get the text
of a specification section for context, a batch of requirements (labels r1, r2, ...) and
candidate verification units: tests (t1, ...), test-plan items (p1, ...) and checkers
(c1, ...). Each requirement shows its source quotes (q1, ...). A test shows the stimulus
steps (s1, ...) and checks (k1, ...) gathered from all of its files; a plan item shows
what it intends to check. Candidates were retrieved by keyword search, so most pairs are
unrelated.

Return only related pairs. Returning nothing for a pair is the expected, correct answer
when the unit does not verify the requirement.

Relations
- covers: the unit, as implemented (test) or as described (plan item), would verify the
  requirement's behavior in full: it creates the conditions the requirement talks about
  and checks the required outcome.
- partial: it verifies part of the behavior. `missing` must say concretely what is not
  verified: a condition, an instance, a value, the error path, or the check itself.
- conflicts: the unit expects or asserts behavior that contradicts the requirement (a
  different value, order, access rule or condition). Use it only when you can state both
  sides: what the requirement says and what the unit expects instead. If the unit simply
  does not check the requirement, or is consistent with it, it is not a conflict. Cite
  the requirement quote and the unit's check or step that contradicts it.

Evidence rules
- Judge what the unit does and checks, not its name. A matching name or summary is not
  evidence of coverage.
- Driving the stimulus without checking the outcome is at most partial (missing: the
  check of the outcome), unless a checker listed under the test checks it.
- Requirements about internal structure or an intermediate step (padding, an internal
  data path, an internal encoding) are covered by a check of an observable result that
  would be wrong if that step were wrong, such as comparing the output against a
  reference model or known-answer vectors. Say that it is verified end to end. Do not
  ask for direct observation of internals that a black-box test cannot see.
- Checks of testbench infrastructure (agent handshakes, timeouts, end-of-test status) do
  not verify design behavior.
- Instances and variants matter: a unit that exercises only one instance, mode or
  configuration covers a requirement about several of them only partially.
- A check marked "only reports" does not fail the test. Judge the relation it would
  verify anyway; this is recorded separately.
- A test that is only listed in a regression shows no steps or checks. Relate it only if
  its summary clearly targets the requirement's behavior, and cite no evidence for it.
- Cite only labels that are shown: q labels of the requirement in the pair, k and s
  labels of the unit in the pair, and cN.kM labels of checkers listed under that unit.

{EVIDENCE_RULES}"""


@dataclass
class Batch:
    section_id: str
    reqs: list[Req]
    units: list[Unit]
    prompt: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    linked: set[str] = field(default_factory=set)  # checker ids shown as evidence for listed tests


def _section_text(section: dict | None, repo_root: Path | None) -> str:
    if not section or repo_root is None:
        return ""
    path = repo_root / section["path"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    a, b = section["lines"]
    text = "\n".join(lines[a - 1 : b])
    if len(text) > SECTION_CHARS:
        text = text[:SECTION_CHARS] + "\n[... section truncated ...]"
    return text


def _with_checkers(units: list[Unit], unit_by_id: dict[str, Unit]) -> tuple[list[Unit], set[str]]:
    """Add the checkers that the batch's tests run with; return (units, linked checker ids)."""
    present = {u.id for u in units}
    linked = {c for u in units for c in u.checked_by if c in unit_by_id}
    extra = [unit_by_id[c] for c in sorted(linked - present)]
    return units + extra, linked


def _label_units(units: list[Unit]) -> tuple[list[tuple[str, Unit]], dict[str, str]]:
    """Stable labels: tests t, plan items p, checkers c (tests first, then plans, then checkers)."""
    order = sorted(units, key=lambda u: ({"test": 0, "plan": 1}[u.kind] + 2 * (u.role == "checker"), u.id))
    counts: Counter = Counter()
    labelled = []
    for u in order:
        prefix = "c" if u.role == "checker" else "p" if u.kind == "plan" else "t"
        counts[prefix] += 1
        labelled.append((f"{prefix}{counts[prefix]}", u))
    return labelled, {u.id: lab for lab, u in labelled}


def _batch_prompt(batch: Batch, section: dict | None, section_text: str) -> None:
    labels: dict[str, str] = {}
    parts = []
    if section:
        parts.append(f"Specification section: {section['path']} > {section['heading_path']} "
                     f"(lines {section['lines'][0]}-{section['lines'][1]})")
        if section_text:
            parts.append(section_text)
    parts.append("Requirements:")
    for k, r in enumerate(batch.reqs, 1):
        labels[f"r{k}"] = r.id
        parts.append(f"[r{k}] {r.block()}")
    parts.append("Candidate verification units:")
    labelled, by_unit = _label_units(batch.units)
    checker_labels = {cid: lab for cid, lab in by_unit.items() if cid in batch.linked}
    for lab, u in labelled:
        labels[lab] = u.id
        parts.append(f"[{lab}] {u.block(checker_labels)}")
    batch.prompt = "\n\n".join(parts)
    batch.labels = labels


def make_batches(reqs: list[Req], units: list[Unit], cands: dict[str, list[str]]) -> list[Batch]:
    """Greedy batches per spec section, bounded by requirement and unit counts; each batch
    then gets the checkers its tests run with."""
    unit_by_id = {u.id: u for u in units}
    by_section: dict[str, list[Req]] = defaultdict(list)
    for r in reqs:
        if cands.get(r.id):
            by_section[r.section_id].append(r)
    batches = []
    for sid in sorted(by_section):
        cur: list[Req] = []
        cur_units: list[str] = []
        for r in sorted(by_section[sid], key=lambda r: r.id):
            new = [u for u in cands[r.id] if u not in cur_units]
            if cur and (len(cur) >= MAX_REQS_PER_BATCH or len(cur_units) + len(new) > MAX_UNITS_PER_BATCH):
                batches.append(Batch(sid, cur, [unit_by_id[u] for u in cur_units]))
                cur, cur_units, new = [], [], list(cands[r.id])
            cur.append(r)
            cur_units += new
        if cur:
            batches.append(Batch(sid, cur, [unit_by_id[u] for u in cur_units]))
    for b in batches:
        b.units, b.linked = _with_checkers(b.units, unit_by_id)
    return batches


def _strength(unit: Unit, cited_checks: list[dict]) -> str:
    if unit.kind == "plan":
        return "planned"
    if unit.role == "coverage":
        return "coverage"
    if unit.status == "listed_only":
        return "listed_only"
    if not cited_checks:
        return "stimulus_only"
    fails = {c["fails_test"] for c in cited_checks}
    if "yes" in fails:
        return "failing"
    if fails == {"no"}:
        return "non_failing"
    return "unknown"


def _unit_evidence(unit: Unit, labels: list[str], batch: Batch, units: dict[str, Unit]
                   ) -> tuple[list[dict], list[dict], list[str], dict[str, str]]:
    """(cited checks, citation records, invalid labels, {checker id: unit hash} of cited checkers)."""
    checks, cites, bad, checker_hashes = [], [], [], {}
    for lab in dict.fromkeys(x.strip() for x in labels):
        owner, item_lab = unit, lab
        if "." in lab:  # cN.kM: a check of a checker this unit runs with
            head, item_lab = lab.split(".", 1)
            cid = batch.labels.get(head)
            if cid is None or cid not in unit.checked_by or cid not in units:
                bad.append(lab)
                continue
            owner = units[cid]
        kind, num = item_lab[:1], item_lab[1:]
        pool = owner.checks[:MAX_CHECKS] if kind == "k" else owner.steps[:MAX_STEPS] if kind == "s" else None
        if pool is None or not num.isdigit() or not 1 <= int(num) <= len(pool):
            bad.append(lab)
            continue
        item = pool[int(num) - 1]
        if kind == "k":
            checks.append(item)
        cite_kind = ("checker_check" if kind == "k" else "checker_step") if owner is not unit else (
            "check" if kind == "k" else "stimulus")
        extra = {"fails_test": item["fails_test"]} if kind == "k" else {}
        if owner is not unit:
            extra["checker_id"] = owner.id
            checker_hashes[owner.id] = _hash(owner.block())
        for q in item["quotes"] or [{"text": item["text"]}]:
            cites.append(_cite(q, item["path"], kind=cite_kind, case_id=item["case_id"], statement=item["text"], **extra))
    return checks, cites, bad, checker_hashes


def assemble(batch: Batch, out: BatchOut, run_id: str) -> tuple[list[dict], Counter]:
    reqs = {r.id: r for r in batch.reqs}
    units = {u.id: u for u in batch.units}
    entries, drops = [], Counter()
    seen: set[tuple[str, str]] = set()
    for rel in out.relations:
        rid, uid = batch.labels.get(rel.requirement), batch.labels.get(rel.unit)
        if rid not in reqs or uid not in units:
            drops["unknown_label"] += 1
            continue
        if uid in batch.linked:
            drops["linked_checker_paired_directly"] += 1
            continue
        if (rid, uid) in seen:
            drops["duplicate"] += 1
            continue
        req, unit = reqs[rid], units[uid]
        qs, bad_q = [], []
        for lab in dict.fromkeys(rel.requirement_quotes):
            if lab.startswith("q") and lab[1:].isdigit() and 1 <= int(lab[1:]) <= len(req.quotes):
                qs.append(req.quotes[int(lab[1:]) - 1])
            else:
                bad_q.append(lab)
        checks, ucites, bad_u, checker_hashes = _unit_evidence(unit, rel.evidence, batch, units)
        drops["citation_failures"] += len(bad_q) + len(bad_u)
        has_evidence = bool(unit.checks or unit.steps)
        if rel.relation == "partial" and not (rel.missing or "").strip():
            drops["partial_without_missing"] += 1
            continue
        if rel.relation == "conflicts" and not (qs and ucites):
            drops["conflict_without_both_citations"] += 1
            continue
        if has_evidence and not ucites:
            drops["no_unit_evidence"] += 1
            continue
        seen.add((rid, uid))
        entries.append({
            "req_id": rid,
            "unit_id": uid,
            "unit_kind": unit.kind,
            "unit_role": unit.role,
            "unit_name": unit.name,
            "relation": rel.relation,
            "strength": _strength(unit, checks),
            "rationale": rel.rationale,
            "missing": rel.missing if rel.relation == "partial" else None,
            "checkers": sorted(checker_hashes),
            "req_cite": [_cite(q, q["path"]) for q in (qs or req.quotes)],
            "unit_cite": ucites,
            "req_hash": _hash(req.block()),
            "unit_hash": _hash(unit.block()),
            "checker_hashes": checker_hashes,
            "run_id": run_id,
        })
    return entries, drops


# --------------------------------------------------------------------------
# Pass 2: plan item -> test links
# --------------------------------------------------------------------------


class PlanLinkOut(BaseModel):
    plan: str = Field(description="Plan item label, e.g. p1.")
    test: str = Field(description="Test label, e.g. t4.")
    relation: Literal["realizes", "partially_realizes"]
    rationale: str
    missing: str | None = Field(description="For partially_realizes: what the plan item asks for that the test does not do.")
    evidence: list[str] = Field(
        description="Labels of the test's checks and steps (k1, s2, ...) and of checks of checkers listed under it (c1.k3)."
    )


class PlanBatchOut(BaseModel):
    links: list[PlanLinkOut]


PLAN_SYSTEM = f"""\
You decide which tests implement which test-plan items. You get plan items (p1, ...),
each describing an intended scenario and checks, and candidate tests (t1, ...) with the
stimulus steps (s1, ...) and checks (k1, ...) gathered from all of their files.
Candidates were retrieved by keyword search, so most pairs are unrelated.

Return only real links:
- realizes: the test performs the plan item's scenario and makes its checks.
- partially_realizes: it does part of it; `missing` says what is not done.

Judge what the test does and checks, not its name. A plan item may be realized by several
tests or by none. A test only listed in a regression (no steps or checks shown) can be
linked only when its summary clearly matches the plan item; cite no evidence for it.
Cite only labels shown for the test in the pair, and cN.kM labels of checkers listed
under it.

{EVIDENCE_RULES}"""


def plan_batches(plans: list[Unit], tests: list[Unit], all_units: dict[str, Unit],
                 top_k: int = PLAN_TOP_K) -> list[Batch]:
    tests = [t for t in tests if t.role == "test"]
    if not plans or not tests:
        return []
    index = BM25([tokens(t.search_text()) for t in tests])
    cands = {p.id: [tests[j] for j, _ in _top(index.scores(tokens(p.search_text())), top_k)] for p in plans}
    groups, cur, cur_tests = [], [], []
    for p in sorted(plans, key=lambda p: p.id):
        if not cands[p.id]:
            continue
        new = [t for t in cands[p.id] if t not in cur_tests]
        if cur and (len(cur) >= MAX_PLANS_PER_BATCH or len(cur_tests) + len(new) > MAX_UNITS_PER_BATCH):
            groups.append((cur, cur_tests))
            cur, cur_tests, new = [], [], cands[p.id]
        cur.append(p)
        cur_tests += new
    if cur:
        groups.append((cur, cur_tests))
    out = []
    for ps, ts in groups:
        units, linked = _with_checkers(sorted(ts, key=lambda t: t.id), all_units)
        b = Batch("", [], ps + units, linked=linked)
        labels, parts = {}, ["Plan items:"]
        for k, p in enumerate(ps, 1):
            labels[f"p{k}"] = p.id
            parts.append(f"[p{k}] {p.block()}")
        parts.append("Candidate tests:")
        labelled, by_unit = _label_units(units)
        checker_labels = {cid: lab for cid, lab in by_unit.items() if cid in linked}
        for lab, u in labelled:
            labels[lab] = u.id
            parts.append(f"[{lab}] {u.block(checker_labels)}")
        b.prompt, b.labels = "\n\n".join(parts), labels
        out.append(b)
    return out


def assemble_plan_links(batch: Batch, out: PlanBatchOut, run_id: str) -> tuple[list[dict], Counter]:
    units = {u.id: u for u in batch.units}
    links, drops, seen = [], Counter(), set()
    for ln in out.links:
        pid, tid = batch.labels.get(ln.plan), batch.labels.get(ln.test)
        if pid not in units or tid not in units or units[pid].kind != "plan" or units[tid].kind != "test":
            drops["unknown_label"] += 1
            continue
        if tid in batch.linked:
            drops["linked_checker_paired_directly"] += 1
            continue
        if (pid, tid) in seen:
            continue
        test = units[tid]
        checks, cites, bad, checker_hashes = _unit_evidence(test, ln.evidence, batch, units)
        drops["citation_failures"] += len(bad)
        if ln.relation == "partially_realizes" and not (ln.missing or "").strip():
            drops["partial_without_missing"] += 1
            continue
        if (test.checks or test.steps) and not cites:
            drops["no_unit_evidence"] += 1
            continue
        seen.add((pid, tid))
        links.append({"plan_id": pid, "plan_name": units[pid].name, "test_id": tid, "test_name": test.name,
                      "relation": ln.relation, "source": "correlate", "strength": _strength(test, checks),
                      "rationale": ln.rationale, "missing": ln.missing if ln.relation == "partially_realizes" else None,
                      "checkers": sorted(checker_hashes), "test_cite": cites, "plan_hash": _hash(units[pid].block()),
                      "test_hash": _hash(test.block()), "checker_hashes": checker_hashes, "run_id": run_id})
    return links, drops


# --------------------------------------------------------------------------
# Pass 3: generic register suites (by_construction requirements, as a group)
# --------------------------------------------------------------------------


class SuiteOut(BaseModel):
    label: str = Field(description="Unit label, e.g. t3 or p2.")
    checks: list[Literal["reset_values", "read_write", "bit_bash", "aliasing", "write_enable_locks", "other"]]
    extent: Literal["all_registers", "some_registers"] = Field(
        description="all_registers: iterates over every register of the block (or every register of a kind), "
        "typically from the register model. some_registers: a fixed handful of named registers."
    )
    rationale: str


class SuitesOut(BaseModel):
    suites: list[SuiteOut]


SUITE_SYSTEM = """\
You identify generic register tests. You get the tests (t1, ...) and test-plan items
(p1, ...) of one design block, with their summaries. A generic register test exercises
every register (or every register of a kind) of the block from the register description
itself: reset values, write and read back, bit-bash, address aliasing, register
write-enable locks, often generated or shared across blocks. Return only those units,
with the kinds of checks each makes and their extent: all_registers if it iterates over
every register (or every register of a kind), some_registers if it checks a fixed
handful of named registers. A test of one specific register's function, or one that
touches registers only to drive a feature, is not generic and is not returned at all.
Returning an empty list is correct when there are none.
"""


def suite_prompt(units: list[Unit]) -> tuple[str, dict[str, str]]:
    labels, parts, nt, np_ = {}, [], 0, 0
    for u in sorted(units, key=lambda u: (u.kind != "test", u.id)):
        if u.role != "test":
            continue
        if u.kind == "test":
            nt += 1
            lab = f"t{nt}"
        else:
            np_ += 1
            lab = f"p{np_}"
        labels[lab] = u.id
        where = ", ".join((u.files or u.listings)[:2])
        parts.append(f"[{lab}] {'plan item' if u.kind == 'plan' else u.status + ' test'}: {u.name} ({where})\n"
                     f"  {u.summary[:300]}")
    return "Units:\n\n" + "\n\n".join(parts), labels


def register_status(by_con: list[Req], suites: list[dict], judged: bool) -> dict:
    """Per property category: how many by_construction requirements, and which suites cover them."""
    cats: Counter = Counter()
    for r in by_con:
        found = {PROPERTY_CATEGORY.get(p, "other") for p in r.properties} or {"other"}
        for c in found:
            cats[c] += 1
    out = {}
    for cat, n in sorted(cats.items()):
        matching = [s for s in suites if set(s["checks"]) & CATEGORY_SUITES[cat]]
        full = [s for s in matching if s["extent"] == "all_registers"]
        if not judged:
            status = "not_judged"
        elif any(s["kind"] == "test" and s["status"] == "implemented" for s in full):
            status = "covered"
        elif full:
            status = "planned_only"
        elif matching:
            status = "some_registers_only"
        else:
            status = "no_suite"
        out[cat] = {"requirements": n, "status": status, "suites": [s["name"] for s in matching]}
    return out


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def repo_root(work_dir: Path, manifest: Path | None, on_event: Callable[[str], None]) -> Path | None:
    """Repo root for reading section text: the workspace manifest, else the latest index run's."""
    path = manifest or work_dir / "manifest.yaml"
    if not path.is_file():
        runs = sorted((work_dir / "index_runs").glob("*/manifest.yaml"))
        if not runs:
            on_event("  ! no manifest found; batches get no section text")
            return None
        path = runs[-1]
        on_event(f"  no workspace manifest; using {path} for the repo root")
    return Path(yaml.safe_load(path.read_text())["repo_root"]).resolve()


@dataclass
class Prepared:
    work_dir: Path
    reqs: list[Req]  # selected needs_test requirements
    all_needs: list[Req]
    by_construction: list[Req]
    units: list[Unit]
    cands: dict[str, list[str]]
    baseline: dict[str, dict]
    batches: list[Batch]
    plan_batches: list[Batch]
    suite: tuple[str, dict[str, str]] | None
    cached: dict[str, dict]
    partial_scope: bool
    estimate_usd: float
    model: str
    top_k: int


def prepare(work_dir: Path, *, manifest: Path | None = None, section_glob: list[str] | None = None,
            limit: int | None = None, top_k: int = TOP_K, model: str = DEFAULT_MODEL, from_log: bool = False,
            on_event: Callable[[str], None] = print) -> Prepared:
    needs, by_con, units, _ = load_units(work_dir)
    unit_by_id = {u.id: u for u in units}
    sections = {s["id"]: s for s in yaml.safe_load((work_dir / "requirements.yaml").read_text())["sections"]}
    reqs = needs
    if section_glob:
        reqs = [r for r in needs if any(_glob_match(h, g) for h in r.heading_paths for g in section_glob)]
    # Retrieval over all requirements, so a --section run sees the same candidates as a full run.
    cands, baseline = candidates(needs, units, top_k)
    selected = {r.id for r in reqs}
    batches = make_batches(reqs, units, {k: v for k, v in cands.items() if k in selected})
    if limit is not None:
        batches = batches[:limit]
    root = repo_root(work_dir, manifest, on_event)
    texts: dict[str, str] = {}
    for b in batches:
        if b.section_id not in texts:
            texts[b.section_id] = _section_text(sections.get(b.section_id), root)
        _batch_prompt(b, sections.get(b.section_id), texts[b.section_id])

    tests = [u for u in units if u.kind == "test"]
    plans = [u for u in units if u.kind == "plan"]
    pbatches = plan_batches(plans, tests, unit_by_id)
    suite = suite_prompt(units) if by_con and units else None

    log = work_dir / "llm_calls.jsonl"
    cached = {}
    if from_log:
        cached = {"correlate": logged_responses(log, "correlate", BatchOut),
                  "correlate_plans": logged_responses(log, "correlate_plans", PlanBatchOut),
                  "correlate_register_suites": logged_responses(log, "correlate_register_suites", SuitesOut)}
    p_in, p_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    est = 0.0
    for purpose, system, prompts in (("correlate", SYSTEM, [b.prompt for b in batches]),
                                     ("correlate_plans", PLAN_SYSTEM, [b.prompt for b in pbatches]),
                                     ("correlate_register_suites", SUITE_SYSTEM, [suite[0]] if suite else [])):
        for p in prompts:
            if p not in cached.get(purpose, {}):
                est += ((len(system) + len(p)) / EST_CHARS_PER_TOKEN * p_in + EST_OUTPUT_TOKENS * p_out) / 1_000_000
    return Prepared(work_dir, reqs, needs, by_con, units, cands, baseline, batches, pbatches, suite, cached,
                    partial_scope=bool(section_glob) or limit is not None, estimate_usd=est, model=model, top_k=top_k)


async def execute(p: Prepared, *, max_cost_usd: float = DEFAULT_MAX_COST_USD,
                  on_event: Callable[[str], None] = print) -> dict:
    work_dir = p.work_dir
    llm = LLM(p.model, work_dir / "llm_calls.jsonl", max_cost_usd)
    run_id = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H%M%SZ")
    errors: list[str] = []
    calls = Counter()

    async def call(purpose: str, system: str, prompt: str, output: type[BaseModel]):
        hit = p.cached.get(purpose, {}).get(prompt)
        if hit is not None:
            calls["cached"] += 1
            return hit
        try:
            calls["llm"] += 1
            return await llm.parse(purpose, system, prompt, output)
        except (BudgetExceeded, LLMError) as e:
            errors.append(f"{purpose}: {e}")
            on_event(f"  ! {purpose}: {e}")
            return None

    async def none():
        return None

    on_event(f"  correlate: {len(p.reqs)} requirements in {len(p.batches)} batches; "
             f"{len(p.plan_batches)} plan-link batches; register suites: {'yes' if p.suite else 'no'}")
    outs, pouts, sout = await asyncio.gather(
        asyncio.gather(*(call("correlate", SYSTEM, b.prompt, BatchOut) for b in p.batches)),
        asyncio.gather(*(call("correlate_plans", PLAN_SYSTEM, b.prompt, PlanBatchOut) for b in p.plan_batches)),
        call("correlate_register_suites", SUITE_SYSTEM, p.suite[0], SuitesOut) if p.suite else none(),
    )

    entries, drops, batch_records, judged_reqs = [], Counter(), [], set()
    for b, out in zip(p.batches, outs):
        if out is None:
            continue
        e, d = assemble(b, out, run_id)
        entries += e
        drops += d
        judged_reqs.update(r.id for r in b.reqs)
        batch_records.append({"section_id": b.section_id, "prompt_hash": _hash(b.prompt),
                              "requirements": [r.id for r in b.reqs], "units": [u.id for u in b.units],
                              "linked_checkers": sorted(b.linked)})

    plan_links, plan_drops = [], Counter()
    for b, out in zip(p.plan_batches, pouts):
        if out is not None:
            ls, d = assemble_plan_links(b, out, run_id)
            plan_links += ls
            plan_drops += d
    # Links dedup already made by name are kept alongside, unjudged.
    canon = yaml.safe_load((work_dir / "canonical.yaml").read_text())
    names = {u.id: u.name for u in p.units}
    judged_pairs = {(ln["plan_id"], ln["test_id"]) for ln in plan_links}
    for t in canon.get("tests") or []:
        for pid in t["realizes"]:
            if (pid, t["id"]) not in judged_pairs:
                plan_links.append({"plan_id": pid, "plan_name": names.get(pid), "test_id": t["id"],
                                   "test_name": t["name"], "relation": "realizes", "source": "dedup_name",
                                   "strength": None, "rationale": "tied by name or reference in dedup",
                                   "missing": None, "test_cite": [], "run_id": run_id})
    plan_links.sort(key=lambda ln: (ln["plan_id"], ln["test_id"]))

    register_suites = None
    if p.suite:
        labels = p.suite[1]
        unit_by_id = {u.id: u for u in p.units}
        suites = []
        if sout is not None:
            for s in sout.suites:
                if s.label in labels:
                    u = unit_by_id[labels[s.label]]
                    suites.append({"unit_id": u.id, "name": u.name, "kind": u.kind, "status": u.status,
                                   "checks": s.checks, "extent": s.extent, "rationale": s.rationale})
        register_suites = {"requirements": len(p.by_construction),
                           "categories": register_status(p.by_construction, suites, judged=sout is not None),
                           "suites": suites}

    # Merge with an earlier mapping when this run covered only part of the scope.
    out_path = work_dir / "mapping.yaml"
    old = yaml.safe_load(out_path.read_text()) if out_path.is_file() and p.partial_scope else None
    if old:
        current = {r.id for r in p.all_needs}
        keep = lambda rid: rid not in judged_reqs and rid in current  # noqa: E731
        entries = [e for e in old.get("entries") or [] if keep(e["req_id"])] + entries
        batch_records = [b for b in old.get("batches") or [] if not set(b["requirements"]) & judged_reqs
                         and set(b["requirements"]) <= current] + batch_records
        if register_suites is None:
            register_suites = old.get("register_suites")
    entries.sort(key=lambda e: (e["req_id"], e["unit_id"]))

    rel = Counter(e["relation"] for e in entries)
    mapped = {e["req_id"] for e in entries if e["relation"] in ("covers", "partial")}
    mapped_by_test = {e["req_id"] for e in entries if e["relation"] in ("covers", "partial")
                      and e["unit_kind"] == "test" and e["strength"] not in ("coverage", "listed_only")}
    judged_all = {r for b in batch_records for r in b["requirements"]}
    stats = {
        "needs_test": len(p.all_needs),
        "by_construction": len(p.by_construction),
        "requirements_judged": len(judged_all),
        "requirements_without_candidates": sum(1 for r in p.all_needs if not p.cands.get(r.id)),
        "candidate_pairs": sum(len(v) for v in p.cands.values()),
        "judged_pairs": sum(len(b["requirements"]) * len(b["units"]) for b in batch_records),
        "batches": len(batch_records),
        "entries": len(entries),
        "covers": rel["covers"],
        "partial": rel["partial"],
        "conflicts": rel["conflicts"],
        "by_strength": dict(Counter(e["strength"] for e in entries)),
        "entries_citing_a_checker": sum(1 for e in entries if e.get("checkers")),
        "requirements_mapped": len(mapped),
        "requirements_mapped_by_a_test": len(mapped_by_test),
        "requirements_mapped_only_by_plans_coverage_or_lists": len(mapped - mapped_by_test),
        "requirements_unmapped": len(judged_all - mapped),
        "dropped": dict(drops),
        "plan_links": sum(1 for ln in plan_links if ln["source"] == "correlate"),
        "plan_links_from_dedup": sum(1 for ln in plan_links if ln["source"] == "dedup_name"),
        "plans": sum(1 for u in p.units if u.kind == "plan"),
        "plans_with_a_test": len({ln["plan_id"] for ln in plan_links}),
        "plan_link_dropped": dict(plan_drops),
        "register_categories": {k: v["status"] for k, v in (register_suites or {}).get("categories", {}).items()},
        "llm_calls": calls["llm"],
        "cached_calls": calls["cached"],
        "errors": len(errors),
        "cost_usd": round(llm.spent_usd, 4),
    }
    result = {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "model": p.model,
        "top_k": p.top_k,
        "stats": stats,
        "errors": errors,
        "register_suites": register_suites,
        "entries": entries,
        "plan_links": plan_links,
        "baseline": [{"req_id": rid, **v} for rid, v in sorted(p.baseline.items())],
        "candidates": {rid: v for rid, v in sorted(p.cands.items()) if v},
        "batches": batch_records,
    }
    out_path.write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return result
