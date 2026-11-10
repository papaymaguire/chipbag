"""`chipbag run`: every stage from manifest to report, reusing cached work.

Stages: index (only when the workspace has no manifest) -> requirements -> cases -> dedup ->
correlate -> analyze -> verify -> report. Each stage reuses logged LLM responses for prompts
that have not changed, and verify reuses verdicts for findings that have not changed, so a
re-run on an unchanged workspace costs nothing and only rebuilds the files.

The workspace scope (which spec sections and verification chunks to extract) is kept in
<workspace>/scope.yaml so that later runs extract the same thing. --section/--chunk set it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

STAGES = ["requirements", "cases", "dedup", "correlate", "analyze", "verify", "report"]
EST_VERIFY_USD = 0.09        # mean cost of one verify agent, measured on two workspaces
CONFIRM_ABOVE_USD = 3.0


class Stop(Exception):
    pass


@dataclass
class Run:
    work_dir: Path
    max_cost_usd: float
    say: Callable[[str], None]
    detail: Callable[[str], None]
    confirm: Callable[[str], bool]
    spent: float = 0.0
    stages: list[dict] = field(default_factory=list)

    def remaining(self, stage_cap: float) -> float:
        left = self.max_cost_usd - self.spent
        if left <= 0:
            raise Stop(f"cost cap ${self.max_cost_usd:.2f} reached")
        return min(stage_cap, left)

    def record(self, n: int, name: str, t0: float, cost: float, summary: str) -> None:
        secs = time.monotonic() - t0
        self.spent += cost
        self.stages.append({"stage": name, "seconds": round(secs, 1), "cost_usd": round(cost, 4), "summary": summary})
        self.say(f"[{n}/{len(STAGES)}] {name:<13} {summary}  (${cost:.2f}, {secs:.1f}s)")


def load_scope(work_dir: Path) -> dict:
    path = work_dir / "scope.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_scope(work_dir: Path, sections: list[str] | None, chunks: list[str] | None) -> None:
    scope = load_scope(work_dir)
    if sections:
        scope["sections"] = sections
    if chunks:
        scope["chunks"] = chunks
    (work_dir / "scope.yaml").write_text(yaml.safe_dump(scope, sort_keys=False, allow_unicode=True, width=200),
                                         encoding="utf-8")


def _verify_todo(work_dir: Path) -> int:
    from chipbag import verify as vf
    analysis = yaml.safe_load((work_dir / "analysis.yaml").read_text(encoding="utf-8"))
    n = 0
    for f in analysis["findings"]:
        path = work_dir / "verify_runs" / f["id"] / "verdict.yaml"
        rec = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
        if not (rec and rec.get("verdict") and rec.get("finding_hash") == vf.finding_hash(f)):
            n += 1
    return n


async def run_all(work_dir: Path, manifest_path: Path, *, max_cost_usd: float, fresh: bool, pdf: bool,
                  verify_limit: int | None, say: Callable[[str], None], detail: Callable[[str], None],
                  confirm: Callable[[str], bool]) -> dict:
    from chipbag import analyze as an
    from chipbag import cases as vc
    from chipbag import correlate as cr
    from chipbag import dedup as dd
    from chipbag import report as rp
    from chipbag import requirements as rq
    from chipbag import verify as vf

    r = Run(work_dir, max_cost_usd, say, detail, confirm)
    started = time.monotonic()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    root = cr.repo_root(work_dir, manifest_path, detail)
    scope = load_scope(work_dir)
    from_log = not fresh
    say(f"Workspace {work_dir}  ·  {manifest.get('topic')}  ·  {manifest.get('repo_root')} @ "
        f"{(manifest.get('repo_commit') or '?')[:10]}  ·  cost cap ${max_cost_usd:.2f}")
    if scope:
        say(f"Scope from {work_dir / 'scope.yaml'}: {len(scope.get('sections') or [])} section patterns, "
            f"{len(scope.get('chunks') or [])} chunk patterns")
    status = "complete"
    try:
        t0 = time.monotonic()
        res = await rq.extract(manifest, work_dir, max_cost_usd=r.remaining(rq.DEFAULT_MAX_COST_USD),
                               section_glob=scope.get("sections"), from_log=from_log, on_event=detail)
        rq.write(res, work_dir)
        s = res["stats"]
        r.record(1, "requirements", t0, s["cost_usd"],
                 f"{s['requirements']} requirements from {s['sections']} spec sections")

        t0 = time.monotonic()
        res = await vc.extract(manifest, work_dir, max_cost_usd=r.remaining(vc.DEFAULT_MAX_COST_USD),
                               chunk_glob=scope.get("chunks"), from_log=from_log, on_event=detail)
        vc.write(res, work_dir)
        s = res["stats"]
        r.record(2, "cases", t0, s["cost_usd"],
                 f"{s['cases']} verification cases from {s['chunks']} chunks; {s['checks']} checks")

        t0 = time.monotonic()
        res = await dd.run(work_dir, what="both", max_cost_usd=r.remaining(dd.DEFAULT_MAX_COST_USD),
                           from_log=from_log, on_event=detail)
        rs, cs = res.get("requirements_stats") or {}, res.get("cases_stats") or {}
        r.record(3, "dedup", t0, res["cost_usd"],
                 f"{rs.get('canonical')} requirements ({rs.get('needs_test')} need a test); "
                 f"{cs.get('tests')} tests, {cs.get('plans')} plan items")

        t0 = time.monotonic()
        prepared = cr.prepare(work_dir, manifest=manifest_path, from_log=from_log, on_event=detail)
        if prepared.estimate_usd > CONFIRM_ABOVE_USD and not confirm(
                f"correlate: estimated ${prepared.estimate_usd:.2f} for uncached batches. Continue?"):
            raise Stop("declined at correlate")
        res = await cr.execute(prepared, max_cost_usd=r.remaining(cr.DEFAULT_MAX_COST_USD), on_event=detail)
        s = res["stats"]
        r.record(4, "correlate", t0, s["cost_usd"],
                 f"{s['requirements_mapped_by_a_test']} of {s['needs_test']} requirements matched to a test")

        t0 = time.monotonic()
        res = await an.run(work_dir, max_cost_usd=r.remaining(an.DEFAULT_MAX_COST_USD), from_log=from_log,
                           repo_root=root, on_event=detail)
        s = res["stats"]
        by = {c: sum(t.values()) for c, t in s["by_category"].items()}
        r.record(5, "analyze", t0, s["cost_usd"],
                 f"{s['findings']} candidate findings: " + ", ".join(f"{an.CATEGORIES[c]}) {n}" for c, n in by.items()))

        t0 = time.monotonic()
        todo = _verify_todo(work_dir) if not fresh else len(res["findings"])
        if verify_limit is not None:
            todo = min(todo, verify_limit)
        est = todo * EST_VERIFY_USD
        if est > CONFIRM_ABOVE_USD and not confirm(f"verify: {todo} findings to check, estimated ${est:.2f}. Continue?"):
            raise Stop("declined at verify")
        if todo:
            say(f"[6/{len(STAGES)}] verify        checking {todo} findings "
                f"({'none cached' if fresh else 'the rest cached'}); progress below")
        res = await vf.run(work_dir, root, max_cost_usd=r.remaining(vf.DEFAULT_MAX_COST_USD), limit=verify_limit,
                           force=fresh, on_event=say if todo else detail)
        s = res["stats"]
        st = s["by_status"]
        r.record(6, "verify", t0, s["cost_usd_this_run"],
                 f"{st.get('verified', 0)} verified, {st.get('withdrawn', 0)} withdrawn, "
                 f"{st.get('inconclusive', 0)} inconclusive")
    except Stop as e:
        status = f"stopped: {e}"
        say(f"Stopped: {e}")

    t0 = time.monotonic()
    record = {"cost_usd": r.spent, "seconds": time.monotonic() - started}
    paths: list[Path] = []
    if (work_dir / "findings.yaml").is_file():
        paths = rp.write(work_dir, root, manifest_path, record, pdf=pdf, on_event=say)
        r.record(7, "report", t0, 0.0, "written to " + ", ".join(str(p) for p in paths))
    else:
        say("No findings.yaml yet; no report written.")
    out = {"started_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status,
           "cost_usd": round(r.spent, 4), "seconds": round(time.monotonic() - started, 1), "stages": r.stages,
           "report": [str(p) for p in paths]}
    with (work_dir / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    return out


def run(work_dir: Path, manifest_path: Path, **kw) -> dict:
    return asyncio.run(run_all(work_dir, manifest_path, **kw))
