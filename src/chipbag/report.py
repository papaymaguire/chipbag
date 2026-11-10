"""Report: the verified findings, written for a human to confirm.

The report states each gap and shows the evidence for it and how it was checked. It does not
say what to do about the gap. Each finding shows:
  - the claim, in one or two sentences (from the analysis record, not the verifier's advice);
  - the evidence: every quote at its file and line (all were checked against the files);
  - why it was verified: the verifier's conclusion, what it searched, and what it ruled out.

Left out on purpose: the verifier's `resolution`, the triage `gap` text (phrased as a test to
write) and the ambiguity `impact` text. Findings with the same subject (same requirement, or
the same quoted spec text) are shown once, under the lowest category number. Withdrawn and
inconclusive findings are listed after the main sections so the review covers them too.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import yaml

CATEGORY_TITLES = {
    "spec_ambiguity": "Spec too ambiguous to test",
    "spec_contradiction": "Spec contradicts itself",
    "test_inconsistency": "Test contradicts its own description",
    "spec_test_mismatch": "Spec and test disagree",
    "untested": "Spec not tested",
}
CATEGORY_ORDER = list(CATEGORY_TITLES)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
TYPE_LABELS = {
    "missing_detail": "missing detail", "vague": "vague", "unclear_referent": "unclear referent",
    "weak_modal": "weak modal", "within_section": "within one section",
    "description_vs_definition": "description vs. definition", "partial_contradiction": "test checks a different value",
    "test_conflict": "test expects the opposite", "check_cannot_fail": "check cannot fail",
    "comment_vs_code": "comment vs. code", "message_vs_condition": "message vs. condition",
    "plan_vs_test": "plan vs. test", "name_vs_behavior": "name vs. behavior",
    "untested": "no test", "partially_tested": "partly tested", "planned_only": "planned, no test",
    "weak_only": "only weak evidence", "plan_item_not_implemented": "plan item with no test",
    "register_suite": "register suite", "reclassified": "found during verification",
}
GROUP_LABELS = {
    "untested": "No test found", "partially_tested": "Partly tested",
    "planned_only": "In a test plan, but no test found",
    "weak_only": "Only weak evidence (no check that can fail)",
    "plan_item_not_implemented": "Test plan items with no test",
}
STRENGTH_LABELS = {"non_failing": "check does not fail the test", "stimulus_only": "stimulus only, no check",
                   "planned": "plan item only", "coverage": "coverage only", "listed_only": "listed, no source",
                   "unknown": "check, failure path unknown", "failing": "failing check"}


# ---------------------------------------------------------------- locations and quotes

class Locator:
    """Formats file locations, with links to the hosted source at the manifest commit."""

    def __init__(self, root: Path | None, commit: str | None):
        self.base = None
        if root and commit:
            try:
                url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                url = ""
            m = re.match(r"(?:https://|git@)(github\.com|gitlab\.com)[/:](.+?)(?:\.git)?$", url)
            if m:
                self.base = f"https://{m.group(1)}/{m.group(2)}/blob/{commit}/"
        self.refs: dict[str, int] = {}

    def label(self, e: dict) -> str:
        path = e["path"]
        a, b = _lines(e)
        if e.get("sheet"):
            where = f"{path} [{e['sheet']}]" + (f" row {a}" + (f"-{b}" if b and b != a else "") if a else "")
        elif a:
            where = f"{path}:{a}" + (f"-{b}" if b and b != a else "")
        else:
            where = path
        if not self.base:
            return f"`{where}`"
        anchor = "" if (e.get("sheet") or not a or path.endswith((".xlsx", ".xls"))) else \
            f"#L{a}" + (f"-L{b}" if b and b != a else "")
        n = self.refs.setdefault(f"{self.base}{path}{anchor}", len(self.refs) + 1)
        return f"[`{where}`][{n}]"

    def definitions(self) -> list[str]:
        return [f"[{n}]: {url}" for url, n in self.refs.items()]


def _lines(e: dict) -> tuple[int | None, int | None]:
    if e.get("line_start"):
        return e["line_start"], e.get("line_end") or e["line_start"]
    if e.get("lines"):
        return e["lines"][0], e["lines"][-1]
    if e.get("rows"):
        return e["rows"][0], e["rows"][-1]
    return None, None


def _key(e: dict) -> tuple:
    a, b = _lines(e)
    return (e["path"], e.get("sheet"), a, b)


def _overlaps(e: dict, seen: list[dict]) -> bool:
    a, b = _lines(e)
    for s in seen:
        if s["path"] != e["path"] or s.get("sheet") != e.get("sheet"):
            continue
        sa, sb = _lines(s)
        if a is None or sa is None or (a <= sb and sa <= b):
            return True
    return False


def _fence(text: str, indent: str) -> list[str]:
    text = (text or "").rstrip("\n")
    lines = text.splitlines() or [""]
    # Strip the common indentation so code quotes stay readable.
    pad = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=0)
    return [f"{indent}~~~"] + [f"{indent}{line[pad:]}" for line in lines] + [f"{indent}~~~"]


def _code(text: str) -> str:
    """An inline code span that survives backticks in the quoted text."""
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (run + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _one_line(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _first_sentence(text: str | None) -> str:
    text = _one_line(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


class Writer:
    def __init__(self, loc: Locator):
        self.loc = loc
        self.out: list[str] = []

    def line(self, s: str = "") -> None:
        self.out.append(s)

    def quote(self, e: dict, caption: str | None = None, indent: str = "") -> None:
        """One evidence quote: location, an optional caption, the exact text."""
        text = e.get("quote") if e.get("quote") is not None else e.get("text")
        cap = f" — {caption}" if caption else ""
        if text and "\n" not in text.strip() and len(text) <= 240:
            self.line(f"{indent}- {self.loc.label(e)}: {_code(text.strip())}{cap}")
            return
        self.line(f"{indent}- {self.loc.label(e)}{cap}")
        if text:
            self.out.extend(_fence(text, indent + "  "))

    def checked(self, v: dict, shown: list[dict], indent: str = "") -> None:
        """Why it was verified: the conclusion, the searches and what was ruled out."""
        if v.get("summary"):
            self.line(f"{indent}**Verified:** {_one_line(v['summary'])}")
            self.line()
        extra = [e for e in v.get("evidence") or [] if e.get("role") in ("supports", "refutes")
                 and not _overlaps(e, shown)]
        if extra:
            self.line(f"{indent}**Further evidence found by the verifier:**")
            self.line()
            for e in extra:
                self.quote(e, _one_line(e.get("note")), indent)
                shown.append(e)
            self.line()
        if v.get("searches"):
            self.line(f"{indent}**Searched:**")
            self.line()
            for s in v["searches"]:
                self.line(f"{indent}- {_one_line(s.get('what'))} in `{_one_line(s.get('where'))}`: "
                          f"{_one_line(s.get('found'))}")
            self.line()
        ruled = []
        for n in v.get("near_misses") or []:
            if not _overlaps(n, shown):
                ruled.append((self.loc.label(n), n.get("why_not")))
                shown.append(n)
        if ruled:
            self.line(f"{indent}**Looked at and ruled out:**")
            self.line()
            for label, why in ruled:
                self.line(f"{indent}- {label}: {_one_line(why)}")
            self.line()


# ---------------------------------------------------------------- finding blocks

def _dedup_quotes(quotes: list[dict]) -> list[dict]:
    """Drop repeated quotes and join quotes on consecutive lines of one file into one."""
    seen, out = set(), []
    for q in quotes or []:
        k = (_key(q), _one_line(q.get("text") or q.get("quote")))
        if k in seen:
            continue
        seen.add(k)
        a, b = _lines(q)
        prev = out[-1] if out else None
        if prev and a and prev["path"] == q["path"] and not q.get("sheet") and not prev.get("sheet") \
                and _lines(prev)[1] is not None and a == _lines(prev)[1] + 1 and not q.get("kind") and not prev.get("kind"):
            out[-1] = {"path": q["path"], "lines": [_lines(prev)[0], b],
                       "text": f"{prev.get('text') or prev.get('quote')} {q.get('text') or q.get('quote')}"}
            continue
        out.append(q)
    return out


def _claim(f: dict) -> str:
    d = f.get("detail") or {}
    t = f["type"]
    if f["category"] == "spec_ambiguity":
        return _one_line(d.get("note"))
    if f["category"] == "spec_contradiction":
        return _one_line(d.get("explanation") or d.get("note"))
    if f["category"] == "test_inconsistency":
        return _one_line(d.get("description") or d.get("explanation"))
    if f["category"] == "spec_test_mismatch":
        if t == "partial_contradiction":
            return _one_line(d.get("contradiction") or d.get("missing"))
        return _one_line(d.get("rationale") or d.get("explanation"))
    if t == "register_suite":
        status = {"some_registers_only": "the suites found cover only some of the registers",
                  "no_suite": "no suite was found", "planned_only": "a suite is planned but not implemented"}
        return (f"{d.get('requirements')} register properties of this kind are not covered by a test that runs over "
                f"every register: {status.get(d.get('status'), _one_line(d.get('status')))}.")
    return _one_line(d.get("explanation"))


def _heading(f: dict) -> str:
    d = f.get("detail") or {}
    if f["category"] == "spec_ambiguity":
        return _one_line(d.get("text") or f["title"])
    if f["category"] == "test_inconsistency" and d.get("test"):
        return f"{d['test']}: {TYPE_LABELS.get(f['type'], f['type'])}"
    if f["category"] in ("spec_contradiction", "spec_test_mismatch") and d.get("statement"):
        return _one_line(d["statement"])
    if f["type"] == "register_suite":
        cat = (f.get("subject") or {}).get("register_category", "")
        return f"Register {cat.replace('_', ' ')}: no suite covers every register"
    return _one_line(f["title"])


def render_single(w: Writer, f: dict, num: str, related: list[dict]) -> None:
    d = f.get("detail") or {}
    w.line(f"### {num} {_heading(f)}")
    w.line()
    tags = [f"`{f['id']}`", TYPE_LABELS.get(f["type"], f["type"]), f"severity {f['severity']}"]
    if f.get("section"):
        tags.append(f"section: {_one_line(f['section'])}")
    w.line(" · ".join(tags))
    w.line()
    claim = _claim(f)
    if claim:
        w.line(f"**Gap:** {claim}")
        w.line()
    if f.get("labels"):
        w.line("Labels in this finding: " + "; ".join(
            f"{lab} = " + ", ".join(w.loc.label(x) for x in locs[:3]) for lab, locs in f["labels"].items()))
        w.line()
    shown: list[dict] = []
    _evidence(w, f, shown)
    if f["category"] in ("spec_ambiguity", "spec_contradiction") and d.get("question"):
        w.line(f"**Not answered by the spec:** {_one_line(d['question'])}")
        w.line()
    w.checked(f.get("verify") or {}, shown)
    for r in related:
        w.line(f"**Same subject, also found as {CATEGORY_TITLES[r['category']].lower()}** "
               f"(`{r['id']}`, {TYPE_LABELS.get(r['type'], r['type'])}): {_claim(r)}")
        w.line()
        _evidence(w, r, shown)
        rv = r.get("verify") or {}
        w.checked({"summary": rv.get("summary"), "evidence": rv.get("evidence")}, shown)


def _evidence(w: Writer, f: dict, shown: list[dict]) -> None:
    spec = [q for q in _dedup_quotes(f.get("spec")) if not _overlaps(q, shown)]
    prose = [q for q in _dedup_quotes(f.get("prose")) if not _overlaps(q, shown)]
    tests = [q for q in _dedup_quotes(f.get("tests")) if not _overlaps(q, shown)]
    reclassified = [q for q in f.get("evidence") or [] if not _overlaps(q, shown)]
    if spec:
        w.line("**Spec says:**")
        w.line()
        for q in spec:
            w.quote(q)
        w.line()
        shown += spec
    if prose:
        w.line("**Test description says:**")
        w.line()
        for q in prose:
            w.quote(q, _one_line(q.get("statement")) if q.get("kind") == "plan" else None)
        w.line()
        shown += prose
    if tests:
        w.line("**Test does:**")
        w.line()
        for q in tests:
            w.quote(q, _test_caption(q))
        w.line()
        shown += tests
    if reclassified:
        w.line("**Evidence:**")
        w.line()
        for q in reclassified:
            w.quote(q, _one_line(q.get("note")))
        w.line()
        shown += reclassified
    if f["type"] == "register_suite":
        suites = (f.get("detail") or {}).get("suites") or []
        w.line(f"**Suites that touch these registers:** {', '.join(f'`{s}`' for s in suites) or 'none'}")
        w.line()


def _short_section(section: str) -> str:
    """'docs/x.md > A > B > C … (3 sections)' -> 'x.md > C (and 2 more sections)'."""
    m = re.match(r"(.*?)(?: … \((\d+) sections\))?$", _one_line(section))
    parts = m.group(1).split(" > ")
    short = f"{Path(parts[0]).name} > {parts[-1]}" if len(parts) > 1 else parts[0]
    n = int(m.group(2)) - 1 if m.group(2) else 0
    return short + (f" (and {n} more section{'s' if n > 1 else ''})" if n else "")


def _test_caption(q: dict) -> str | None:
    parts = []
    if q.get("kind"):
        parts.append(q["kind"].replace("_", " "))
    if q.get("fails_test") == "no":
        parts.append("does not fail the test")
    if q.get("statement"):
        parts.append(_one_line(q["statement"]))
    return "; ".join(parts) or None


def render_group(w: Writer, f: dict, num: str) -> list[dict]:
    """A group of untested requirements (or plan items). Returns the items not verified."""
    items = f.get("items") or []
    kept = [it for it in items if it.get("status") == "verified"]
    w.line(f"### {num} {GROUP_LABELS.get(f['type'], f['type'])}: {len(kept)} "
           f"{'plan item' if f['type'] == 'plan_item_not_implemented' else 'requirement'}{'s' if len(kept) != 1 else ''}"
           + (f" in {_short_section(f['section'])}" if f.get("section") else ""))
    w.line()
    w.line(" · ".join([f"`{f['id']}`", f"severity {f['severity']}"]))
    w.line()
    shown: list[dict] = []
    for i, it in enumerate(items, 1):
        if it.get("status") != "verified":
            continue
        if f["type"] == "plan_item_not_implemented":
            w.line(f"{i}. **Plan item:** {_one_line(it.get('name'))} — {_one_line(it.get('summary'))} "
                   f"({', '.join(f'`{s}`' for s in it.get('sources') or [])})")
        else:
            w.line(f"{i}. **Requirement** `{it.get('req_id')}`: {_one_line(it.get('statement'))}  ")
            if f.get("section", "").endswith("sections)") and it.get("section"):
                w.line(f"   Section: {_one_line(it['section'])}")
            w.line()
            for q in _dedup_quotes(it.get("spec")):
                w.quote(q, indent="   ")
                shown.append(q)
        if f["type"] == "partially_tested":
            for tb in it.get("tested_by") or []:
                w.line()
                w.line(f"   **Tested by `{tb.get('unit')}`:** {_one_line(tb.get('rationale'))}  ")
                w.line(f"   **Not tested:** {_one_line(tb.get('missing'))}")
        weak = it.get("weak_evidence") or []
        if weak:
            w.line()
            w.line("   **Nearest evidence (not counted as a test):** " + "; ".join(
                f"`{x.get('unit')}` ({STRENGTH_LABELS.get(x.get('strength'), x.get('strength'))})" for x in weak))
        w.line()
        w.line(f"   **Verified:** {_one_line(it.get('verify_reason'))}")
        for e in it.get("verify_evidence") or []:
            if not _overlaps(e, shown):
                w.quote(e, _one_line(e.get("note")), indent="   ")
                shown.append(e)
        w.line()
    gone = [str(i) for i, it in enumerate(items, 1) if it.get("status") != "verified"]
    if gone:
        w.line(f"Item{'s' if len(gone) > 1 else ''} {', '.join(gone)} of this group "
               f"{'were' if len(gone) > 1 else 'was'} not verified (see Withdrawn and Inconclusive).")
        w.line()
    v = dict(f.get("verify") or {})
    v.pop("evidence", None)  # per-item evidence is shown with each item
    v["summary"] = None      # the overall summary mixes in the items that were withdrawn
    w.checked(v, shown)
    return [dict(it, _group=f) for it in items if it.get("status") != "verified"]


# ---------------------------------------------------------------- grouping

def _related(findings: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Show findings with the same subject once: the same requirement, or the same quoted text."""
    singles = [f for f in findings if not f.get("items")]
    parent = {f["id"]: f["id"] for f in singles}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[tuple, str] = {}
    for f in singles:
        s = f.get("subject") or {}
        keys = [("req", r) for r in s.get("requirements") or []]
        if s.get("quote"):
            keys.append(("quote", s["quote"]))
        if f["category"] == "test_inconsistency":
            keys += [("test_line", tuple(s.get("units") or []), q["path"], _lines(q)[0])
                     for q in f.get("tests") or [] if q.get("kind") == "check"]
        for k in keys:
            if k in owner:
                parent[find(f["id"])] = find(owner[k])
            else:
                owner[k] = f["id"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in singles:
        groups[find(f["id"])].append(f)
    primary_of: dict[str, list[dict]] = {}
    hidden = set()
    for members in groups.values():
        members.sort(key=_order)
        primary_of[members[0]["id"]] = members[1:]
        hidden.update(m["id"] for m in members[1:])
    shown = [f for f in findings if f["id"] not in hidden]
    return shown, primary_of


def _order(f: dict) -> tuple:
    return (CATEGORY_ORDER.index(f["category"]), SEVERITY_ORDER.get(f["severity"], 3), f.get("section") or "",
            f["id"])


# ---------------------------------------------------------------- report

def _git_head(root: Path | None) -> str | None:
    if not root:
        return None
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _workspace_spend(work_dir: Path) -> dict:
    """Total LLM spend recorded in the workspace, by purpose, plus the verify agents."""
    by: Counter = Counter()
    log = work_dir / "llm_calls.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            by[r.get("purpose") or "?"] += r.get("cost_usd") or 0.0
    for v in (work_dir / "verify_runs").glob("*/verdict.yaml"):
        by["verify agents"] += (_load(v).get("cost_usd") or 0.0)
    return dict(by)


def build(work_dir: Path, root: Path | None, manifest_path: Path | None, run_record: dict | None = None) -> str:
    findings_doc = _load(work_dir / "findings.yaml")
    if not findings_doc:
        raise FileNotFoundError(f"{work_dir / 'findings.yaml'} not found; run verify first")
    manifest = _load(manifest_path) if manifest_path else {}
    canonical = _load(work_dir / "canonical.yaml")
    mapping = _load(work_dir / "mapping.yaml")
    commit = manifest.get("repo_commit")
    loc = Locator(root, commit)
    w = Writer(loc)
    all_findings = findings_doc["findings"]
    verified = sorted([f for f in all_findings if f["status"] == "verified"], key=_order)
    shown, related = _related(verified)
    other = [f for f in all_findings if f["status"] != "verified"]

    topic = manifest.get("topic") or work_dir.name
    w.line(f"# Spec and test gaps: {topic}")
    w.line()
    head = _git_head(root)
    w.line(f"Repository `{manifest.get('repo_root', '?')}` at commit `{(commit or '?')[:12]}`. "
           f"Report generated {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M UTC')} by chipbag.")
    if commit and head and head != commit:
        w.line()
        w.line(f"**Note:** the repository is now at `{head[:12]}`, not the manifest commit. Line numbers refer to "
               f"`{commit[:12]}`.")
    w.line()
    w.line("Each finding below gives the gap, the evidence for it as exact quotes at their file and line, and how it "
           "was verified: an agent searched the repository to refute the finding, and the finding was kept only if "
           "that failed. Every quote was checked against the file at the cited lines. The report does not recommend "
           "a fix.")
    w.line()

    # Summary
    w.line("## Summary")
    w.line()
    w.line("| # | Category | Verified | Withdrawn | Inconclusive |")
    w.line("|---|---|---:|---:|---:|")
    for i, cat in enumerate(CATEGORY_ORDER, 1):
        st = Counter(f["status"] for f in all_findings if f["category"] == cat)
        n_shown = sum(1 for f in shown if f["category"] == cat)
        items = sum(1 for f in shown if f["category"] == cat for it in f.get("items") or []
                    if it.get("status") == "verified")
        label = f"{n_shown}" + (f" ({items} items)" if items else "")
        w.line(f"| {i} | [{CATEGORY_TITLES[cat]}](#{i}-{_slug(CATEGORY_TITLES[cat])}) | {label} | "
               f"{st.get('withdrawn', 0) + st.get('reclassified', 0)} | {st.get('inconclusive', 0)} |")
    w.line()
    merged = sum(len(v) for v in related.values())
    if merged:
        w.line(f"{merged} verified finding{'s' if merged != 1 else ''} with the same subject as another are shown "
               "together with it and counted once.")
        w.line()

    # Scope
    w.line("## Scope")
    w.line()
    srcs = manifest.get("sources") or []
    for role, title in (("spec", "Spec sources"), ("verification", "Verification sources")):
        group = [s for s in srcs if s["role"] == role]
        if group and len(group) <= 8:
            w.line(f"**{title} ({len(group)}):** " + ", ".join(f"`{s['path']}`" for s in group))
            w.line()
        elif group:
            dirs = Counter(str(Path(s["path"]).parent) for s in group)
            w.line(f"**{title} ({len(group)} files):** " + ", ".join(
                f"`{d}/` ({n})" for d, n in sorted(dirs.items(), key=lambda x: (-x[1], x[0]))) +
                ". The full list is in the manifest.")
            w.line()
    rs, cs = canonical.get("requirements_stats") or {}, canonical.get("cases_stats") or {}
    ms = mapping.get("stats") or {}
    if rs:
        w.line(f"{rs.get('canonical')} requirements after merging duplicates; {rs.get('needs_test')} need a test "
               f"(the rest hold by construction). {cs.get('tests')} tests ({cs.get('tests_implemented')} with source), "
               f"{cs.get('plans')} test plan items. {ms.get('requirements_mapped_by_a_test', '?')} requirements are "
               f"matched to a test.")
        w.line()
    in_manifest = {s["path"] for s in srcs}
    additions = [p for p in findings_doc.get("manifest_additions") or [] if p not in in_manifest]
    if additions:
        w.line("Files the verifier used as evidence that are not in the manifest: " +
               ", ".join(f"`{p}`" for p in additions) + ".")
        w.line()

    # Findings by category
    for i, cat in enumerate(CATEGORY_ORDER, 1):
        fs = [f for f in shown if f["category"] == cat]
        w.line(f"## {i}. {CATEGORY_TITLES[cat]}")
        w.line()
        if not fs:
            w.line("No verified findings.")
            w.line()
            continue
        for j, f in enumerate(fs, 1):
            num = f"{i}.{j}"
            if f.get("items"):
                render_group(w, f, num)
            else:
                render_single(w, f, num, related.get(f["id"], []))
            w.line("---")
            w.line()

    # Not verified
    w.line("## Inconclusive")
    w.line()
    w.line("The verifier could neither confirm nor refute these. They need a human decision.")
    w.line()
    inc = [f for f in other if f["status"] == "inconclusive" and not f.get("items")]
    inc_items = [(f, it) for f in verified + other for it in f.get("items") or [] if it.get("status") == "inconclusive"]
    if not inc and not inc_items:
        w.line("None.")
        w.line()
    for f in sorted(inc, key=_order):
        w.line(f"- `{f['id']}` ({CATEGORY_TITLES[f['category']].lower()}) {_heading(f)} — "
               f"{_one_line((f.get('verify') or {}).get('summary'))}")
    for f, it in inc_items:
        what = it.get("statement") or it.get("name")
        w.line(f"- `{f['id']}` ({GROUP_LABELS.get(f['type'], f['type']).lower()}) {_one_line(what)} — "
               f"{_one_line(it.get('verify_reason'))}")
    w.line()

    w.line("## Withdrawn")
    w.line()
    w.line("Candidates the verifier refuted, with the reason. Listed so the refutations can be checked too.")
    w.line()
    wd = [f for f in other if f["status"] in ("withdrawn", "reclassified")]
    for f in sorted(wd, key=_order):
        v = f.get("verify") or {}
        ref = [e for e in v.get("evidence") or [] if e.get("role") == "refutes"][:2]
        where = (" (" + ", ".join(loc.label(e) for e in ref) + ")") if ref else ""
        extra = " Reclassified; see the finding marked *found during verification*." if f["status"] == "reclassified" else ""
        w.line(f"- `{f['id']}` ({CATEGORY_TITLES[f['category']].lower()}) {_heading(f)} — "
               f"{_first_sentence(v.get('summary'))}{where}{extra}")
    wd_items = [(f, it) for f in verified + other for it in f.get("items") or [] if it.get("status") == "withdrawn"]
    if wd_items:
        w.line()
        w.line(f"Withdrawn items from the groups in section 5 ({len(wd_items)}):")
        w.line()
        for f, it in wd_items:
            what = it.get("statement") or it.get("name")
            ref = [e for e in it.get("verify_evidence") or [] if e.get("role") == "refutes"][:1]
            where = (" (" + loc.label(ref[0]) + ")") if ref else ""
            w.line(f"- {_one_line(what)} — {_first_sentence(it.get('verify_reason'))}{where}")
    unchecked = [f for f in other if f["status"] == "unchecked"]
    if unchecked:
        w.line()
        w.line(f"**Not checked:** {len(unchecked)} candidate findings were not verified (cost cap or limit) and are "
               "not in this report.")
    w.line()

    # Run record
    w.line("## Run record")
    w.line()
    a = _load(work_dir / "analysis.yaml")
    rows = [("Spec extraction", _load(work_dir / "requirements.yaml").get("model")),
            ("Test extraction", _load(work_dir / "cases.yaml").get("model")),
            ("Merge and link", canonical.get("model")), ("Correlate", mapping.get("model")),
            ("Analyze", ", ".join(sorted(set((a.get("models") or {}).values()))) if isinstance(a.get("models"), dict)
             else a.get("models")),
            ("Verify", findings_doc.get("model"))]
    w.line("| Stage | Model |")
    w.line("|---|---|")
    for stage, model in rows:
        w.line(f"| {stage} | {model or '?'} |")
    w.line()
    spend = _workspace_spend(work_dir)
    if run_record:
        w.line(f"This run: ${run_record.get('cost_usd', 0):.2f} in {run_record.get('seconds', 0):.0f} s "
               f"(cached results reused where the inputs were unchanged). ")
    w.line(f"All LLM spend recorded for this workspace: ${sum(spend.values()):.2f}.")
    w.line()
    if manifest_path and manifest_path.is_file():
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()[:12]
        w.line(f"Manifest `{manifest_path}` (sha256 `{digest}`); findings `{work_dir / 'findings.yaml'}`; "
               f"verifier transcripts in `{work_dir / 'verify_runs'}/<finding id>/`.")
    w.line()
    w.out += loc.definitions()
    w.line()
    return "\n".join(w.out)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", s.lower()).strip().replace(" ", "-")


def write(work_dir: Path, root: Path | None, manifest_path: Path | None, run_record: dict | None = None,
          pdf: bool = False, on_event=print) -> list[Path]:
    text = build(work_dir, root, manifest_path, run_record)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = work_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{stamp}.md"
    md.write_text(text, encoding="utf-8")
    (out_dir / "latest.md").write_text(text, encoding="utf-8")
    paths = [md]
    if pdf:
        p = to_pdf(md, on_event)
        if p:
            paths.append(p)
    return paths


# ---------------------------------------------------------------- PDF

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font: 10.5pt/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; max-width: 100%; }
h1 { font-size: 20pt; margin: 0 0 6pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
code { font: 9pt/1.35 "DejaVu Sans Mono", Menlo, Consolas, monospace; background: #f3f3f3; padding: 0 2px; }
pre { background: #f6f6f6; border-left: 3px solid #bbb; padding: 4pt 6pt; margin: 3pt 0 6pt;
      white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 1px solid #ccc; padding: 3pt 6pt; text-align: left; }
hr { border: 0; border-top: 1px solid #ddd; margin: 10pt 0; }
a { color: #0b57d0; text-decoration: none; }
ul, ol { padding-left: 18pt; } li { margin: 2pt 0; }
"""


def to_pdf(md_path: Path, on_event=print) -> Path | None:
    """Markdown -> HTML -> PDF with a headless Chromium. Returns None if no browser is installed."""
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        on_event("  pdf: markdown-it-py is not installed; skipped")
        return None
    browser = next((b for b in ("google-chrome", "chromium", "chromium-browser", "chrome") if shutil.which(b)), None)
    if not browser:
        on_event("  pdf: no Chrome or Chromium found; skipped")
        return None
    body = MarkdownIt("commonmark").enable("table").render(md_path.read_text(encoding="utf-8"))
    # Heading ids as GitHub makes them, so the summary table links work in the PDF too.
    body = re.sub(r"<(h[23])>(.*?)</\1>",
                  lambda m: f'<{m.group(1)} id="{_slug(re.sub(r"<[^>]+>", "", m.group(2)))}">{m.group(2)}</{m.group(1)}>',
                  body)
    title = md_path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
    html_path = md_path.with_suffix(".html")
    html_path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
                         f"<style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")
    pdf_path = md_path.with_suffix(".pdf")
    try:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()],
                       capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        on_event(f"  pdf: {browser} failed: {e}")
        return None
    if not pdf_path.is_file():
        on_event(f"  pdf: {browser} produced no file")
        return None
    return pdf_path
