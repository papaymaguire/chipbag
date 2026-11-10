"""Dedup: turn extracted requirements and verification cases into canonical units.

Normalize extracts per chunk, so the same behavior stated in several documents (a spec,
an integration guide, a register description, a generated doc) becomes several
requirements, and one test shows up as a plan row, an implementation (sometimes split
across files) and several regression listings. Correlating raw units would multiply
every judgment, so this stage groups them first.

1. Candidate clusters (code, recall-oriented): deterministic keys (register target +
   property, identical quote text, test names and references), plus TF-IDF similarity
   with identifier-aware tokens, blocked on shared rare identifiers. Union-find, capped.
2. Merge (LLM, one call per cluster with two or more members): the model returns the
   final groups. Contradictions are never merged; they become conflict records.
   Broader/narrower pairs stay separate and are linked as refinements.
3. Validation and assembly (code): every member appears exactly once; anything the model
   dropped stays a singleton. Canonical units keep every member's quotes.

Raw extractions are not modified; canonical.yaml references them by ID.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from chipbag.llm import LLM, BudgetExceeded, LLMError

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_COST_USD = 5.0
MAX_CLUSTER = 25  # members per LLM call
TOP_K = 8  # similarity neighbors considered per unit
SIM_REQ = 0.35  # requirement pair: min cosine, with a shared rare identifier
SIM_REQ_STRONG = 0.6  # requirement pair: min cosine without a shared identifier
SIM_PLAN_PLAN = 0.5  # planned <-> planned case
HUB_KEY_MAX = 20  # a name key shared by more cases than this links nothing
HUB_REF_MAX = 5  # a name referenced by more cases than this is a base class or library
GENERIC_NAMES = {"main", "test_main", "run", "body", "setup", "init", "test", "pre_start", "post_start"}
QUOTE_CHARS = 300  # quote text shown per member in prompts

# --------------------------------------------------------------------------
# Tokens and identifiers
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"0x[0-9a-fA-F_]+|\d+'[bdhBDH][0-9a-fA-F_]+|[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _looks_like_identifier(tok: str) -> bool:
    return "_" in tok.strip("_") or bool(_CAMEL.search(tok)) or (tok.isupper() and len(tok) >= 3)


def tokens(text: str) -> list[str]:
    """Lowercased words plus identifiers kept whole and split into their parts."""
    out = []
    for m in _TOKEN.finditer(text):
        t = m.group()
        low = t.lower()
        if low in ENGLISH_STOP_WORDS or len(low) < 2:
            continue
        out.append(low)
        if _looks_like_identifier(t):
            out += [p.lower() for p in re.split(r"_+", _CAMEL.sub("_", t)) if len(p) > 1 and p.lower() not in ENGLISH_STOP_WORDS]
    return out


def identifiers(text: str) -> set[str]:
    return {m.group().lower() for m in _TOKEN.finditer(text) if _looks_like_identifier(m.group())}


def _similarity(texts: list[str]) -> np.ndarray:
    if len(texts) < 2:
        return np.zeros((len(texts), len(texts)))
    vec = TfidfVectorizer(analyzer=tokens, sublinear_tf=True)
    x = vec.fit_transform(texts)
    sim = (x @ x.T).toarray()
    np.fill_diagonal(sim, 0.0)
    return sim


def _rare_identifiers(texts: list[str]) -> list[set[str]]:
    """Identifiers per text, minus those so common they don't discriminate."""
    ids = [identifiers(t) for t in texts]
    df: dict[str, int] = defaultdict(int)
    for s in ids:
        for i in s:
            df[i] += 1
    limit = max(5, len(texts) // 20)
    return [{i for i in s if df[i] <= limit} for s in ids]


# --------------------------------------------------------------------------
# Union-find with a size cap
# --------------------------------------------------------------------------


def _components(n: int, edges: list[tuple[int, int, float]]) -> list[list[int]]:
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, _ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def clusters(n: int, edges: list[tuple[int, int, float]], cap: int = MAX_CLUSTER) -> list[list[int]]:
    """Connected components; an oversized component is re-split by dropping its weakest
    edges until every part fits, falling back to slicing (which may separate duplicates)."""
    out = []
    for comp in _components(n, edges):
        if len(comp) <= cap:
            out.append(comp)
            continue
        members = set(comp)
        sub = sorted((e for e in edges if e[0] in members and e[1] in members), key=lambda e: -e[2])
        while len(sub) > 1:
            sub = sub[: len(sub) * 3 // 4]  # keep the strongest 75%
            index = {m: k for k, m in enumerate(comp)}
            parts = _components(len(comp), [(index[a], index[b], w) for a, b, w in sub])
            if max(len(p) for p in parts) <= cap:
                out += [[comp[k] for k in p] for p in parts]
                break
        else:
            out += [comp[k : k + cap] for k in range(0, len(comp), cap)]
    return out


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def _norm_quote(text: str) -> str:
    return " ".join(re.sub(r"[`*|#>\\]", " ", text.lower()).split())


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------

_PROP_CANON = {"sw": "swaccess", "swaccess": "swaccess", "hw": "hwaccess", "hwaccess": "hwaccess",
               "reset": "reset", "resval": "reset"}


def _target_key(target: str) -> str:
    """Register/field path without the register-file prefix: 'blk.REG.field' -> 'reg.field'."""
    return ".".join(target.lower().replace("-", "_").split(".")[-2:])


def requirement_edges(reqs: list[dict]) -> list[tuple[int, int, float]]:
    texts = [r["statement"] + " " + " ".join(q["text"] for q in r["quotes"]) for r in reqs]
    sim = _similarity(texts)
    rare = _rare_identifiers(texts)
    edges: dict[tuple[int, int], float] = {}

    def add(a: int, b: int, w: float) -> None:
        if a != b:
            key = (min(a, b), max(a, b))
            edges[key] = max(edges.get(key, 0.0), w)

    # Same register target and property, across register formats.
    by_prop: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, r in enumerate(reqs):
        if r["source"] == "property":
            by_prop[(_target_key(r["target"]), _PROP_CANON.get(r["property"], r["property"]))].append(i)
    for members in by_prop.values():
        for a in members[1:]:
            add(members[0], a, 1.0)
    # Identical quoted text in different places (e.g. a generated doc and its source).
    by_quote: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(reqs):
        for q in r["quotes"]:
            nq = _norm_quote(q["text"])
            if len(nq) >= 30:
                by_quote[nq].append(i)
    for members in by_quote.values():
        for a in set(members[1:]):
            add(members[0], a, 1.0)
    # Similar text. Property requirements are templated, so two of them are only linked
    # by key; similarity links properties to text-derived requirements and text to text.
    for i in range(len(reqs)):
        for j in np.argsort(-sim[i])[:TOP_K]:
            j = int(j)
            s = float(sim[i, j])
            if reqs[i]["source"] == "property" and reqs[j]["source"] == "property":
                continue
            if s >= SIM_REQ_STRONG or (s >= SIM_REQ and rare[i] & rare[j]):
                add(i, j, s)
    return [(a, b, w) for (a, b), w in edges.items()]


class ReqGroupOut(BaseModel):
    member_ids: list[str] = Field(description="Labels of the requirements in this group (r1, r2, ...).")
    statement: str = Field(description="One canonical statement of the shared behavior. For a single member, its own statement.")
    resolved_ambiguity: str | None = Field(
        description="If one member is vague and another member pins it down, say how; otherwise null."
    )


class ReqConflictOut(BaseModel):
    member_ids: list[str] = Field(description="Labels of requirements that contradict each other (at least two).")
    subject: str = Field(description="What they are about, e.g. 'reset value of LOCK'.")
    difference: str = Field(description="What exactly differs.")
    question: str = Field(description="A concrete question an architect could answer to resolve it.")


class ReqRefinesOut(BaseModel):
    general_id: str
    specific_id: str
    note: str = Field(description="What the specific one adds.")


class ReqClusterOut(BaseModel):
    groups: list[ReqGroupOut]
    conflicts: list[ReqConflictOut]
    refinements: list[ReqRefinesOut]


REQ_SYSTEM = """\
You merge duplicate hardware requirements. You get a cluster of requirements extracted
from different places (specifications, integration guides, register definitions,
generated documents). Each has a label, its source, a statement and the exact quotes it
was extracted from. Return the final grouping.

Groups
- Put requirements in one group only if they state the same testable behavior: a test
  that verifies one would verify the others. Wording, document and level of formality
  may differ.
- Every label appears in exactly one group. A requirement with no duplicate is a group
  of one.
- Each group gets one canonical statement: short, precise, using register and signal
  names as the sources do. Do not add behavior no member states.
- If a member is vague (e.g. 'the counter increments') and another member of the same
  group pins it down (e.g. 'increments by STEP on each tick'), give the canonical
  statement the precise form and explain in `resolved_ambiguity`.

Never merge these
- Contradictions: same subject, incompatible content (different reset values, access
  types, widths, conditions, orderings). Keep them in separate groups and add a
  conflict record naming the labels. A register property and a description that
  disagree are a conflict.
- Broader and narrower statements: if one states the behavior generally and another
  adds a condition or detail the first lacks, keep both and add a refinement
  (general_id, specific_id). Do not merge them.
- Related but different behaviors: set vs clear, read vs write, normal vs error path,
  different registers, fields or instances (e.g. MBOX0 vs MBOX1, timer 1 vs timer 2),
  unless a member explicitly states that the behavior is shared.
"""


def _req_prompt(members: list[dict]) -> tuple[str, dict[str, str]]:
    labels = {}
    parts = []
    for k, r in enumerate(sorted(members, key=lambda r: r["id"]), 1):
        label = f"r{k}"
        labels[label] = r["id"]
        quotes = " | ".join(q["text"][:QUOTE_CHARS] for q in r["quotes"])
        origin = "register property" if r["source"] == "property" else "text"
        parts.append(f"[{label}] {r['path']} > {r['heading_path']} ({origin})\n"
                     f"  statement: {r['statement']}\n  quotes: {quotes}")
    return "Requirements in this cluster:\n\n" + "\n\n".join(parts), labels


def _relabel(text: str | None, labels: dict[str, str]) -> str | None:
    """Replace cluster-local labels (r3, c12) in model notes with the real unit IDs."""
    if not text:
        return text
    return re.sub(r"\b[rc]\d+\b", lambda m: labels.get(m.group(), m.group()), text)


def _assemble_requirements(reqs: list[dict], outs: list[tuple[dict[str, str], ReqClusterOut | None]]) -> dict:
    by_id = {r["id"]: r for r in reqs}
    groups: list[tuple[list[str], str | None, str | None]] = []  # (member ids, statement, resolved)
    conflicts_raw, refines_raw, issues = [], [], []
    for labels, out in outs:
        seen: set[str] = set()
        if out is not None:
            for g in out.groups:
                ids = [labels[x] for x in g.member_ids if x in labels and labels[x] not in seen]
                unknown = [x for x in g.member_ids if x not in labels]
                if unknown:
                    issues.append(f"unknown labels {unknown}")
                if ids:
                    seen.update(ids)
                    groups.append((ids, g.statement if len(ids) > 1 else None,
                                   _relabel(g.resolved_ambiguity, labels) if len(ids) > 1 else None))
            conflicts_raw += [(c, labels) for c in out.conflicts]
            refines_raw += [(f, labels) for f in out.refinements]
        missing = [rid for rid in labels.values() if rid not in seen]
        if out is not None and missing:
            issues.append(f"{len(missing)} members not grouped by the model; kept as singletons")
        groups += [([rid], None, None) for rid in missing]

    canonical, member_of = [], {}
    for ids, statement, resolved in groups:
        ids = sorted(ids)
        rs = [by_id[i] for i in ids]
        cid = "cr_" + _hash(*ids)
        for i in ids:
            member_of[i] = cid
        canonical.append(
            {
                "id": cid,
                "statement": statement or rs[0]["statement"],
                "merged": len(ids) > 1,
                "tier": "needs_test" if any(r["tier"] == "needs_test" for r in rs) else "by_construction",
                "aspect": rs[0]["aspect"],
                "members": ids,
                "sources": sorted({f"{r['path']} > {r['heading_path']}" for r in rs}),
                "ambiguities": [r["ambiguity"] for r in rs if r.get("ambiguity")],
                "ambiguity_resolved": resolved,
                "quotes": [{"path": r["path"], "req_id": r["id"], **q} for r in rs for q in r["quotes"]],
            }
        )
    conflicts = []
    for c, labels in conflicts_raw:
        cids = sorted({member_of[labels[x]] for x in c.member_ids if x in labels})
        if len(cids) < 2:
            issues.append(f"conflict within one group dropped: {c.subject}")
            continue
        conflicts.append({"id": "cx_" + _hash(*cids), "requirements": cids, "subject": c.subject,
                          "difference": _relabel(c.difference, labels), "question": _relabel(c.question, labels),
                          "members": sorted(labels[x] for x in c.member_ids if x in labels)})
    refinements, seen_pairs = [], set()
    for f, labels in refines_raw:
        if f.general_id in labels and f.specific_id in labels:
            g, s = member_of[labels[f.general_id]], member_of[labels[f.specific_id]]
            if g != s and (g, s) not in seen_pairs:
                seen_pairs.add((g, s))
                refinements.append({"general": g, "specific": s, "note": _relabel(f.note, labels)})
    return {"requirements": canonical, "conflicts": conflicts, "refinements": refinements, "issues": issues}


# --------------------------------------------------------------------------
# Verification cases
# --------------------------------------------------------------------------


def _name_keys(text: str) -> set[str]:
    """Normalized test-name keys: basename without extension, plus variants without a
    trailing instance number (smoke_test_x0 -> smoke_test_x) or a sequence/test suffix."""
    base = text.strip().replace("\\", "/").split("/")[-1].split(":")[-1]
    base = re.sub(r"\.(c|cc|cpp|sv|svh|v|py|hjson|yml|yaml)$", "", base, flags=re.I).lower()
    if len(base) < 4 or base in GENERIC_NAMES:
        return set()
    keys = {base}
    # Common naming conventions: numbered instances (x0, x_1) and sequence/test suffixes
    # (UVM `<name>_vseq` run by test `<name>`).
    no_suffix = re.sub(r"_(vseq|seq|sequence|test|tests)$", "", base)
    for variant in (re.sub(r"_?\d+$", "", base), no_suffix,
                    re.sub(r"(?<=[a-z])\d+(?=_|$)", "", base)):  # instance number inside a segment: mbox0_x
        if len(variant) >= 4:
            keys.add(variant)
    return keys


def case_edges(cases: list[dict]) -> list[tuple[int, int, float]]:
    own = []
    for c in cases:
        keys = _name_keys(c["name"])
        if c["kind"] == "implemented":
            keys |= _name_keys(Path(c["path"]).stem)
        own.append(keys)
    refs = [set().union(*(_name_keys(r) for r in c["references"])) if c["references"] else set() for c in cases]
    edges: dict[tuple[int, int], float] = {}

    def add(a: int, b: int, w: float) -> None:
        if a != b:
            key = (min(a, b), max(a, b))
            edges[key] = max(edges.get(key, 0.0), w)

    # Shared names (strong), or one case naming another (weaker). A name that many cases
    # reference is a base class or library, not a test: references to it link nothing.
    owners: dict[str, set[int]] = defaultdict(set)
    referrers: dict[str, set[int]] = defaultdict(set)
    for i in range(len(cases)):
        for k in own[i]:
            owners[k].add(i)
        for k in refs[i]:
            referrers[k].add(i)
    for k, named in owners.items():
        if len(named) > HUB_KEY_MAX:
            continue
        ms = sorted(named)
        for a in ms[1:]:
            add(ms[0], a, 1.0)
        if len(referrers.get(k, ())) <= HUB_REF_MAX:
            for i in referrers.get(k, ()):
                for j in ms:
                    add(i, j, 0.7)
    # Implemented cases in the same small directory (e.g. two halves of one test).
    by_dir: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cases):
        if c["kind"] == "implemented":
            by_dir[str(Path(c["path"]).parent)].append(i)
    for members in by_dir.values():
        files = {cases[i]["path"] for i in members}
        if 2 <= len(files) and len(members) <= 4:
            for a in members[1:]:
                add(members[0], a, 0.9)
    # Planned <-> planned by text (the same testpoint in two plans). Planned <-> test links
    # come only from names and references here: whether a test realizes a plan item that
    # doesn't name it is a semantic judgment, made in correlate with the test's checks.
    texts = [f"{c['name']} {c['summary']}" for c in cases]
    sim = _similarity(texts)
    for i, c in enumerate(cases):
        if c["kind"] != "planned":
            continue
        for j in np.argsort(-sim[i])[:TOP_K]:
            j = int(j)
            if cases[j]["kind"] == "planned" and float(sim[i, j]) >= SIM_PLAN_PLAN:
                add(i, j, float(sim[i, j]))
    return [(a, b, w) for (a, b), w in edges.items()]


class PlanGroupOut(BaseModel):
    member_ids: list[str] = Field(description="Labels of planned items that describe the same testpoint.")
    name: str
    summary: str


class TestOut(BaseModel):
    name: str = Field(description="The test's name as the sources use it.")
    implemented_ids: list[str] = Field(description="Implemented items that together are this one test (e.g. two halves).")
    listed_ids: list[str] = Field(description="Test-list or regression entries that run this test.")
    planned_ids: list[str] = Field(description="Planned items this test realizes (may be shared with other tests).")
    summary: str = Field(description="What the test verifies, in one or two sentences.")


class CaseClusterOut(BaseModel):
    plans: list[PlanGroupOut]
    tests: list[TestOut]


CASE_SYSTEM = """\
You resolve verification cases into tests. You get a cluster of cases extracted from
test plans, test lists, regression lists and test code. Each has a label, a kind
(planned, implemented or listed), its source, a name, a summary and the names it
references. Return plan groups and tests.

Plans
- Group planned items that describe the same testpoint (e.g. the same scenario in two
  plans). Every planned label appears in exactly one plan group; an item with no
  duplicate is a group of one.

Tests
- A test is one runnable test. Its implementation may be split across several
  implemented items: two programs that run together as one test (for example one per
  processor or agent), or a test and the sequence it runs. Put those in one test.
- Add the listed items (test-list and regression entries) that run this test. Entries
  for numbered variants or instances of the same test belong to it.
- Every implemented and every listed label appears in exactly one test. A listed entry
  whose implementation is not in the cluster is a test with only listed items. An
  implemented item that is a helper, a coverage group, an assertion or a checker is a
  test of its own unless it is clearly part of one test.
- planned_ids: the plan items this test realizes, only where a name or reference ties
  them together (the plan names the test, or the test names the plan item). A plan item
  may be realized by several tests, or by none here; links by content alone are made
  in a later step.
- Match on what the sources say, not on similar-sounding names alone.
"""


def _case_prompt(members: list[dict]) -> tuple[str, dict[str, str]]:
    labels = {}
    parts = []
    for k, c in enumerate(sorted(members, key=lambda c: c["id"]), 1):
        label = f"c{k}"
        labels[label] = c["id"]
        refs = ", ".join(c["references"][:8]) or "-"
        checks = f"; {c['check_count']} checks" if c["kind"] == "implemented" else ""
        parts.append(f"[{label}] {c['kind']} | {c['path']} | {c['name']}{checks}\n"
                     f"  summary: {c['summary']}\n  references: {refs}")
    return "Cases in this cluster:\n\n" + "\n\n".join(parts), labels


def _assemble_cases(cases: list[dict], outs: list[tuple[dict[str, str], CaseClusterOut | None]]) -> dict:
    by_id = {c["id"]: c for c in cases}
    plans_raw: list[tuple[list[str], str | None, str | None]] = []
    tests_raw: list[tuple[list[str], list[str], list[str], str | None, str | None]] = []
    issues = []
    for labels, out in outs:
        kind = {lab: by_id[cid]["kind"] for lab, cid in labels.items()}
        seen_plan: set[str] = set()
        seen_test: set[str] = set()
        if out is not None:
            for p in out.plans:
                ids = [labels[x] for x in p.member_ids if kind.get(x) == "planned" and labels[x] not in seen_plan]
                if ids:
                    seen_plan.update(ids)
                    plans_raw.append((ids, p.name, p.summary))
            for t in out.tests:
                impl = [labels[x] for x in t.implemented_ids if kind.get(x) == "implemented" and labels[x] not in seen_test]
                listed = [labels[x] for x in t.listed_ids if kind.get(x) == "listed" and labels[x] not in seen_test]
                planned = [labels[x] for x in t.planned_ids if kind.get(x) == "planned"]
                if impl or listed:
                    seen_test.update(impl + listed)
                    tests_raw.append((impl, listed, planned, t.name, t.summary))
        for lab, cid in labels.items():
            if kind[lab] == "planned" and cid not in seen_plan:
                plans_raw.append(([cid], None, None))
            elif kind[lab] in ("implemented", "listed") and cid not in seen_test:
                if out is not None:
                    issues.append(f"{cid} not placed by the model; kept on its own")
                tests_raw.append(([cid], [], [], None, None) if kind[lab] == "implemented" else ([], [cid], [], None, None))

    # Repair: a listed-only test whose entry names match exactly one implemented test of
    # the same cluster belongs to it (e.g. an instance variant the model split off).
    cluster_of = {cid: k for k, (labels, _) in enumerate(outs) for cid in labels.values()}

    def keys_of(ids: list[str], with_stem: bool) -> set[str]:
        out: set[str] = set()
        for i in ids:
            out |= _name_keys(by_id[i]["name"])
            if with_stem:
                out |= _name_keys(Path(by_id[i]["path"]).stem)
        return out

    implemented = [t for t in tests_raw if t[0]]
    kept = []
    for t in tests_raw:
        if t[0] or not t[1]:
            kept.append(t)
            continue
        k = cluster_of[t[1][0]]
        mine = keys_of(t[1], with_stem=False)
        hosts = [h for h in implemented if cluster_of[h[0][0]] == k and keys_of(h[0], with_stem=True) & mine]
        if len(hosts) == 1:
            hosts[0][1].extend(t[1])
            issues.append(f"attached listed entries {t[1]} to test {hosts[0][3] or hosts[0][0][0]} by name")
        else:
            kept.append(t)
    tests_raw = kept

    plans, plan_of = [], {}
    for ids, name, summary in plans_raw:
        ids = sorted(ids)
        ms = [by_id[i] for i in ids]
        pid = "cp_" + _hash(*ids)
        for i in ids:
            plan_of[i] = pid
        plans.append({"id": pid, "name": name or ms[0]["name"], "summary": summary or ms[0]["summary"],
                      "merged": len(ids) > 1, "members": ids, "sources": sorted({m["path"] for m in ms}),
                      "checks": [{"case_id": m["id"], **k} for m in ms for k in m["checks"]],
                      "ambiguities": [m["ambiguity"] for m in ms if m.get("ambiguity")],
                      "implemented_by": []})
    plan_by_id = {p["id"]: p for p in plans}
    tests = []
    for impl, listed, planned, name, summary in tests_raw:
        ids = sorted(impl + listed)
        ms = [by_id[i] for i in sorted(impl)] or [by_id[i] for i in sorted(listed)]
        tid = "ct_" + _hash(*ids)
        realizes = sorted({plan_of[p] for p in planned if p in plan_of})
        for p in realizes:
            plan_by_id[p]["implemented_by"].append(tid)
        tests.append(
            {
                "id": tid,
                "name": name or ms[0]["name"],
                "summary": summary or ms[0]["summary"],
                "status": "implemented" if impl else "listed_only",
                "implemented": sorted(impl),
                "listed": sorted(listed),
                "listings": sorted({by_id[i]["path"] for i in listed}),
                "realizes": realizes,
                "files": sorted({by_id[i]["path"] for i in impl}),
                "checks": [{"case_id": i, **k} for i in sorted(impl) for k in by_id[i]["checks"]],
                "failing_checks": sum(by_id[i]["failing_checks"] for i in impl),
            }
        )
    return {"plans": plans, "tests": tests, "issues": issues}


# --------------------------------------------------------------------------
# Shared checkers: which tests each scoreboard / assertion file / coverage model runs with
# --------------------------------------------------------------------------
#
# A checker (scoreboard, reference model, bound assertions) has checks but drives no
# stimulus; it is active while other tests run. It is shared by many tests, so it is not
# merged into them (canonical units are a partition, and its ID would churn every test's
# ID). Instead each test gets `checked_by` links, and checker-like units get a `role`.


class CheckerOut(BaseModel):
    label: str = Field(description="Label of a checker or coverage unit (t1, ...).")
    role: Literal["checker", "coverage"] = Field(
        description="checker: passively compares or asserts design behavior while other tests run "
        "(scoreboard, reference model, monitor check, bound assertions). coverage: only samples coverage."
    )
    scope_prefixes: list[str] = Field(
        description="Directory paths (copied from those shown) whose tests all run with this unit, e.g. the testbench root."
    )
    tests: list[str] = Field(description="Labels of further individual tests outside those directories that run with it.")
    rationale: str = Field(description="One sentence: how you know which tests it runs with.")


class CheckerScopeOut(BaseModel):
    units: list[CheckerOut]


CHECKER_SYSTEM = """\
You find the shared checkers of a testbench and the tests they run with. You get the
verification units of one design block (t1, ...): name, files (or the lists that
schedule them), a summary and how many stimulus steps and checks were extracted.

Most units are tests: they drive stimulus. Some are passive and active while other tests
run: scoreboards and reference models (including their individual check functions or
phases), monitors with checks, assertion files bound into the design, and coverage
collectors (covergroups, coverpoints). Steps extracted from a passive unit describe what
it observes or predicts, not stimulus it drives.

Return only the passive units, with:
- role: checker if it compares or asserts design behavior; coverage if it only samples
  coverage.
- its scope: the tests that run with it. Use scope_prefixes for directories whose tests
  all run with it (copy a directory from the paths shown), and `tests` for individual
  tests outside those directories. Prefer the testbench root (the directory holding the
  environment, its tests and sequences, and its test list) over the directory of the
  checker file itself. A checker in a testbench environment runs with the
  tests and sequences of that environment and with the list entries that run them; an
  assertion file bound into a top-level testbench runs with every test of that
  testbench. A test on a different testbench or platform does not run with it. Leave
  the scope empty when you cannot tell.
"""


def _test_steps(test: dict, by_case: dict[str, dict]) -> int:
    return sum(len(by_case[i]["stimulus"]) for i in test["implemented"] if i in by_case)


def _checker_prompt(tests: list[dict], by_case: dict[str, dict]) -> tuple[str, dict[str, str]]:
    labels, parts = {}, ["Verification units:"]
    for k, t in enumerate(tests, 1):
        labels[f"t{k}"] = t["id"]
        where = ", ".join(t["files"] or [f"listed in {p}" for p in t["listings"]])
        counts = (f"{_test_steps(t, by_case)} steps, {len(t['checks'])} checks" if t["status"] == "implemented"
                  else "list entry only")
        parts.append(f"[t{k}] {t['name']} | {where} | {counts}\n  {t['summary'][:240]}")
    return "\n\n".join(parts), labels


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.strip().rstrip("/")
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


def assemble_checkers(tests: list[dict], labels: dict[str, str], out: CheckerScopeOut | None) -> list[str]:
    """Set `role` on every test and `checked_by` links; return issues."""
    issues = []
    by_id = {t["id"]: t for t in tests}
    for t in tests:
        t["role"] = "test"
        t["checked_by"] = []
        t.pop("scope", None)
    if out is None:
        return issues
    passive = {}
    for u in out.units:
        cid = labels.get(u.label)
        if cid not in by_id or by_id[cid]["status"] != "implemented":
            issues.append(f"checker scope: {u.label} is not an implemented unit")
            continue
        passive[cid] = u
        by_id[cid]["role"] = u.role
    paths = lambda t: t["files"] + t["listings"]  # noqa: E731
    for cid, u in passive.items():
        checker = by_id[cid]
        prefixes = []
        for p in u.scope_prefixes:
            if any(_under(x, p) for t in tests for x in paths(t)):
                prefixes.append(p.strip().rstrip("/"))
            else:
                issues.append(f"checker scope: prefix {p!r} of {checker['name']} matches no test")
        scoped = {t["id"] for t in tests if any(_under(x, p) for p in prefixes for x in paths(t))}
        for lab in u.tests:
            if labels.get(lab) in by_id:
                scoped.add(labels[lab])
            else:
                issues.append(f"checker scope: unknown test label {lab}")
        scoped -= set(passive)  # passive units don't run each other
        checker["scope"] = {"prefixes": prefixes, "tests": len(scoped), "rationale": u.rationale}
        if u.role == "checker":
            for tid in scoped:
                by_id[tid]["checked_by"].append(cid)
    for t in tests:
        t["checked_by"].sort()
    return issues


async def scope_checkers(tests: list[dict], cases: list[dict], llm: LLM, cached: dict,
                         on_event: Callable[[str], None]) -> dict:
    by_case = {c["id"]: c for c in cases}
    ordered = sorted(tests, key=lambda t: t["id"])
    out, labels = None, {}
    if any(t["status"] == "implemented" and t["checks"] for t in ordered) and len(ordered) > 1:
        prompt, labels = _checker_prompt(ordered, by_case)
        try:
            out = cached.get(prompt) or await llm.parse("dedup_checkers", CHECKER_SYSTEM, prompt, CheckerScopeOut)
        except (BudgetExceeded, LLMError) as e:
            on_event(f"  ! dedup_checkers: {e}")
    issues = assemble_checkers(tests, labels, out)
    roles = defaultdict(int)
    for t in tests:
        roles[t["role"]] += 1
    linked = sum(1 for t in tests if t["checked_by"])
    on_event(f"  dedup_checkers: roles {dict(roles)}; {linked} tests have a checker")
    return {"roles": dict(roles), "tests_with_checker": linked, "issues": issues}


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def logged_responses(log_path: Path, purpose: str, model: type[BaseModel]) -> dict[str, BaseModel]:
    found: dict[str, BaseModel] = {}
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("purpose") == purpose and entry.get("response"):
                found[entry["request"]] = model.model_validate(entry["response"])
    return found


async def _merge(units: list[dict], edges, prompt_fn, system: str, output: type[BaseModel], purpose: str,
                 llm: LLM, cached: dict, on_event: Callable[[str], None]) -> tuple[list, dict]:
    comps = clusters(len(units), edges)
    sizes = sorted((len(c) for c in comps), reverse=True)
    on_event(f"  {purpose}: {len(units)} units -> {len(comps)} clusters "
             f"({sum(1 for s in sizes if s > 1)} to merge; largest {sizes[:5]})")

    async def one(comp: list[int]):
        members = [units[i] for i in comp]
        prompt, labels = prompt_fn(members)
        if len(members) == 1:
            return labels, None
        try:
            out = cached.get(prompt) or await llm.parse(purpose, system, prompt, output)
        except (BudgetExceeded, LLMError) as e:
            on_event(f"  ! {purpose} cluster of {len(members)}: {e}")
            return labels, None
        return labels, out

    outs = await asyncio.gather(*(one(c) for c in comps))
    return outs, {"units": len(units), "clusters": len(comps), "clusters_merged": sum(1 for s in sizes if s > 1),
                  "largest_cluster": sizes[0] if sizes else 0}


async def run(work_dir: Path, *, what: str = "both", model: str = DEFAULT_MODEL,
              max_cost_usd: float = DEFAULT_MAX_COST_USD, from_log: bool = False,
              on_event: Callable[[str], None] = print) -> dict:
    out_path = work_dir / "canonical.yaml"
    result = yaml.safe_load(out_path.read_text()) if out_path.is_file() else {}
    result = result or {}
    llm = LLM(model, work_dir / "llm_calls.jsonl", max_cost_usd)
    log = work_dir / "llm_calls.jsonl"

    if what in ("both", "requirements"):
        reqs = yaml.safe_load((work_dir / "requirements.yaml").read_text())["requirements"]
        cached = logged_responses(log, "dedup_requirements", ReqClusterOut) if from_log else {}
        outs, st = await _merge(reqs, requirement_edges(reqs), _req_prompt, REQ_SYSTEM, ReqClusterOut,
                                "dedup_requirements", llm, cached, on_event)
        r = _assemble_requirements(reqs, outs)
        merged = [c for c in r["requirements"] if c["merged"]]
        result["requirements_stats"] = {
            **st,
            "canonical": len(r["requirements"]),
            "merged_groups": len(merged),
            "members_in_merged_groups": sum(len(c["members"]) for c in merged),
            "needs_test": sum(1 for c in r["requirements"] if c["tier"] == "needs_test"),
            "conflicts": len(r["conflicts"]),
            "refinements": len(r["refinements"]),
            "ambiguities_resolved": sum(1 for c in r["requirements"] if c["ambiguity_resolved"]),
            "issues": len(r["issues"]),
        }
        result.update({"requirements": r["requirements"], "conflicts": r["conflicts"],
                       "refinements": r["refinements"], "requirement_issues": r["issues"]})

    if what in ("both", "cases"):
        cases = yaml.safe_load((work_dir / "cases.yaml").read_text())["cases"]
        cached = logged_responses(log, "dedup_cases", CaseClusterOut) if from_log else {}
        outs, st = await _merge(cases, case_edges(cases), _case_prompt, CASE_SYSTEM, CaseClusterOut,
                                "dedup_cases", llm, cached, on_event)
        c = _assemble_cases(cases, outs)
        result["cases_stats"] = {
            **st,
            "plans": len(c["plans"]),
            "plans_merged": sum(1 for p in c["plans"] if p["merged"]),
            "plans_unimplemented": sum(1 for p in c["plans"] if not p["implemented_by"]),
            "tests": len(c["tests"]),
            "tests_implemented": sum(1 for t in c["tests"] if t["status"] == "implemented"),
            "tests_listed_only": sum(1 for t in c["tests"] if t["status"] == "listed_only"),
            "tests_multi_file": sum(1 for t in c["tests"] if len(t["files"]) > 1),
            "tests_unlisted": sum(1 for t in c["tests"] if t["status"] == "implemented" and not t["listed"]),
            "issues": len(c["issues"]),
        }
        result.update({"plans": c["plans"], "tests": c["tests"], "case_issues": c["issues"]})

    if what in ("both", "cases", "checkers"):
        if "tests" not in result:
            raise RuntimeError("canonical.yaml has no tests; run dedup for cases first")
        cases = yaml.safe_load((work_dir / "cases.yaml").read_text())["cases"]
        cached = logged_responses(log, "dedup_checkers", CheckerScopeOut) if from_log else {}
        st = await scope_checkers(result["tests"], cases, llm, cached, on_event)
        result["checker_stats"] = {k: v for k, v in st.items() if k != "issues"}
        result["checker_issues"] = st["issues"]

    result["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["model"] = model
    result["cost_usd"] = round(llm.spent_usd, 4)
    ordered = {k: result[k] for k in ("generated_at", "model", "cost_usd", "requirements_stats", "cases_stats",
                                      "checker_stats", "requirements", "conflicts", "refinements",
                                      "requirement_issues", "plans", "tests", "case_issues", "checker_issues")
               if k in result}
    out_path.write_text(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return ordered
