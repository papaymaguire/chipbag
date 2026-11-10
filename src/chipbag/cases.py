"""Normalize, verification half: turn verification sources into verification cases.

A case is one test, testpoint, plan row, regression entry, assertion or coverage item.
Each case records what it drives (stimulus) and what it actually checks, with every
claim backed by an exact quote that code locates in the source. Planned cases (plans,
testpoints) are told apart from implemented ones (code that runs) and from listed ones
(test lists that only name a test to run).

Coverage works as on the spec side, with one difference: for plans and lists every
piece of text must be accounted for, while for code only substantial uncited blocks are
reported (setup and boilerplate need not be quoted).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from chipbag.chunks import Chunk, body_span, chunk_source, line_offsets
from chipbag.index import _glob_match
from chipbag.llm import LLM, BudgetExceeded, LLMError
from chipbag.requirements import Ambiguity, coverage_mask, locate, markup_only, normalize, table_header_ranges

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_COST_USD = 5.0
MIN_UNCLASSIFIED_CHARS = 20  # plans and lists: uncovered stretches shorter than this are ignored
MIN_UNCITED_CODE_LINES = 10  # code: uncited blocks shorter than this many non-blank lines are ignored

# --------------------------------------------------------------------------
# LLM output schema
# --------------------------------------------------------------------------


class StepOut(BaseModel):
    text: str = Field(description="What this step does, in one short sentence.")
    quotes: list[str] = Field(description="Exact excerpts of the source that perform or describe this step.")


class CheckOut(BaseModel):
    text: str = Field(description="What is compared, asserted or measured, and against what expectation.")
    quotes: list[str] = Field(description="Exact excerpts: the comparison, assertion or coverage item itself.")
    fails_test: Literal["yes", "no", "unknown"] = Field(
        description="yes: a mismatch makes the test fail (error, fatal, assert, error count, exit status). "
        "no: the value is only printed or logged. unknown: planned checks, or failure handling not visible here."
    )


class CaseOut(BaseModel):
    name: str = Field(description="The case's own name as written in the source (test, testpoint, row title).")
    kind: Literal["planned", "implemented", "listed"]
    anchor: str = Field(description="Exact excerpt that identifies the case: its name, definition line or row.")
    summary: str = Field(description="What this case verifies, in one or two sentences. For retrieval only.")
    intent: list[str] = Field(
        description="Exact excerpts of comments, descriptions or names that state what the case is meant to "
        "verify. Kept separate from checks so stated intent can be compared with what is checked."
    )
    stimulus: list[StepOut]
    checks: list[CheckOut]
    references: list[str] = Field(
        description="Names of other tests, testpoints, sequences or test files this case names "
        "(not library includes or helper functions)."
    )
    ambiguity: Ambiguity | None = Field(description="Set only for a planned case too vague to tell what it verifies.")


class OtherOut(BaseModel):
    quote: str = Field(description="Exact excerpt of the source.")
    label: Literal["informative", "boilerplate", "ambiguous"]
    reason: str = Field(description="One sentence.")
    ambiguity: Ambiguity | None = Field(description="Required when label is 'ambiguous', otherwise null.")


class ChunkCasesOut(BaseModel):
    content_type: Literal["plan", "list", "code", "other"] = Field(
        description="plan: describes what should be verified. list: names tests to run (test or regression "
        "lists). code: tests, sequences, assertions, coverage models. other: anything else."
    )
    cases: list[CaseOut]
    other_text: list[OtherOut]


SYSTEM = """\
You read one chunk of a hardware verification source and extract its verification cases.
The source can be a test plan, testpoint list, coverage plan, test list, regression list,
test program (C, SystemVerilog, Python, ...), sequence, assertion file or coverage model.

Cases
- One case is one test, testpoint, plan row, list entry, assertion property or coverage
  group. A program that is one test is one case; a file holding several tests or
  sequences has one case each. Helper routines are not cases; their steps count toward
  the case that calls them only if they are in this chunk.
- kind: `planned` when the text describes what should be verified (plan rows,
  testpoints, coverage plans, documentation); `implemented` when it is code that runs
  (tests, sequences, assertions, covergroups); `listed` when it only names a test to run
  (test lists, regression lists) without saying what it checks.
- `anchor` quotes what identifies the case: its name, definition line or row.

Stimulus and checks
- stimulus: what the case drives or configures, in order, each step with quotes.
- checks: only what is actually compared, asserted or measured. For code, quote the
  comparison or assertion itself, not the surrounding prints. A value that is read and
  printed but never compared is not a check. A comment that says something is checked
  is not a check; record it under `intent`.
- fails_test: `yes` only if a mismatch makes the test fail (fatal or error report,
  assert, error counter, exit status); `no` if the outcome is only printed or logged;
  `unknown` for planned checks or when failure handling is not visible in this chunk.
- For planned cases, the described expectations are checks with fails_test `unknown`.
- intent: quotes of comments, descriptions or names that state what the case means to
  verify. Keep intent and checks separate even when they agree.
- Do not infer behavior from names alone. Extract only what the source states or does.

Other text
- informative (overview, rationale), boilerplate (license headers, includes, setup that
  verifies nothing), or ambiguous (a planned item too vague to tell what it verifies;
  give the ambiguity code, a note and a question for the plan owner).

Quoting rules (quotes are checked by code against the source)
- Copy quotes verbatim, as one contiguous excerpt each; give several quotes for
  non-adjacent text. Whitespace and line breaks may differ; words and symbols may not.
- Table chunks are shown as "[row N] Header: value | ..." lines; quote cell values, not
  the "[row N]" prefix or the header names.
- Coverage: for plans and lists, every row, entry, sentence and list item must appear in
  some quote, in `cases` or `other_text`. For code, quote every case's anchor, stimulus
  steps and checks; setup and boilerplate need not be quoted.
"""

# --------------------------------------------------------------------------
# Assembly: locate quotes, compute coverage, build records
# --------------------------------------------------------------------------


# Labels in rendered table rows: the "[row N] " prefix and each "Header: " label. Cells
# are joined by " | "; a header may itself contain "|" without spaces ("Directed|Random").
_ROW_LABEL = re.compile(r"^\[row \d+\] |(?:(?<=\] )|(?<= \| ))(?:[^:|\n]|\|(?! )){1,60}?: ", re.M)


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def is_markdown_like(chunk: Chunk) -> bool:
    """Markdown normalization for markdown and rendered table rows; plain for everything else."""
    return chunk.sheet is not None or chunk.path.lower().endswith((".md", ".markdown"))


def content_hash(chunk: Chunk) -> str:
    return hashlib.sha256(normalize(chunk.body, is_markdown_like(chunk))[0].encode()).hexdigest()


def _boilerplate_line(line: str) -> bool:
    """Comment, preprocessor or brace-only line (C/C++/SV/Verilog/Python/shell style)."""
    t = line.strip()
    return not t or t.startswith(("//", "/*", "*", "#", "`include", "`define", "`ifdef", "`endif", "`ifndef")) or t in ("{", "}", "};", "end")


def assemble(chunk: Chunk, out: ChunkCasesOut, file_text: str) -> dict:
    md = is_markdown_like(chunk)
    norm, norm_idx = normalize(chunk.body, md)
    file_starts = line_offsets(file_text)
    covered = [False] * len(norm)
    counts = coverage_mask(chunk, norm_idx)
    if chunk.kind == "data_sample":
        counts = [False] * len(norm)  # a sample of generated data: nothing to account for
    # Labels are not content: "[row N]" prefixes and "Header:" labels in table chunks,
    # header rows of markdown tables.
    labels: list[tuple[int, int]] = []
    if chunk.sheet is not None:
        labels = [m.span() for m in _ROW_LABEL.finditer(chunk.body)]
    elif chunk.path.lower().endswith((".md", ".markdown")):
        labels = table_header_ranges(chunk.body)
    for a, b in labels:
        for k, raw_i in enumerate(norm_idx):
            if a <= raw_i < b:
                covered[k] = True

    failures: list[dict] = []
    base = {"path": chunk.path, "chunk": chunk.heading_path, "chunk_id": chunk.id}

    def place(quote: str) -> dict | None:
        pos = locate(norm, quote, prefer=lambda a, b: not all(covered[a:b]), markdown=md)
        if pos is None:
            return None
        a, b = pos
        for k in range(a, b):
            covered[k] = True
        return {"text": quote, **body_span(chunk, norm_idx[a], norm_idx[b - 1] + 1, file_starts)}

    def place_all(quotes: list[str], what: str, case_name: str) -> list[dict] | None:
        placed = [place(q) for q in quotes]
        bad = [q for q, p in zip(quotes, placed) if p is None]
        if bad:
            failures.append({**base, "case": case_name, "item": what, "unverified_quotes": bad})
        good = [p for p in placed if p is not None]
        return good or None

    cases = []
    for c in out.cases:
        anchor = place(c.anchor)
        if anchor is None:
            failures.append({**base, "case": c.name, "item": "anchor", "unverified_quotes": [c.anchor]})
            continue
        stimulus = [{"text": s.text, "quotes": q} for s in c.stimulus if (q := place_all(s.quotes, "stimulus", c.name))]
        checks = [{"text": k.text, "fails_test": k.fails_test, "quotes": q}
                  for k in c.checks if (q := place_all(k.quotes, "check", c.name))]
        intent = [p for q in c.intent if (p := place(q))]
        for ref in c.references:  # a named test/testpoint is accounted for by the reference
            if len(ref) >= 3:
                place(ref)
        cases.append(
            {
                "id": "vc_" + _short_hash(chunk.path, c.name, normalize(c.anchor, md)[0].strip()),
                **base,
                "name": c.name,
                "kind": c.kind,
                "summary": c.summary,
                "anchor": anchor,
                "intent": intent,
                "stimulus": stimulus,
                "checks": checks,
                "references": c.references,
                "ambiguity": c.ambiguity.model_dump() if c.ambiguity else None,
                "check_count": len(checks),
                "failing_checks": sum(1 for k in checks if k["fails_test"] == "yes"),
            }
        )

    other = []
    for o in out.other_text:
        if markup_only(o.quote, md):
            continue
        placed = place(o.quote)
        if placed is None:
            failures.append({**base, "case": None, "item": o.label, "unverified_quotes": [o.quote]})
            continue
        other.append({**base, "label": o.label, "reason": o.reason,
                      "ambiguity": o.ambiguity.model_dump() if o.ambiguity else None, "quote": placed})

    # Uncovered text: every stretch for plans and lists (`unclassified`); for code, only
    # large blocks that are not comments/preprocessor (`uncited_code`, a review aid).
    is_code = out.content_type in ("code", "other")  # configs, scripts: only large blocks
    unclassified, uncited_code = [], []
    k = 0
    while k < len(norm):
        if covered[k] or not counts[k] or norm[k] == " ":
            k += 1
            continue
        j = k
        while j < len(norm) and not covered[j] and counts[j]:
            j += 1
        a, b = norm_idx[k], norm_idx[j - 1] + 1
        raw = chunk.body[a:b]
        record = {**base, **body_span(chunk, a, b, file_starts), "text": raw.strip()}
        if is_code:
            if sum(1 for line in raw.splitlines() if not _boilerplate_line(line)) >= MIN_UNCITED_CODE_LINES:
                uncited_code.append(record)
        elif sum(1 for ch in norm[k:j] if ch.isalnum()) >= MIN_UNCLASSIFIED_CHARS:
            unclassified.append(record)
        k = j

    content = [i for i, ch in enumerate(norm) if ch != " " and counts[i]]
    coverage = sum(1 for i in content if covered[i]) / len(content) if content else 1.0
    chunk_rec = {
        "id": chunk.id,
        "path": chunk.path,
        "heading_path": chunk.heading_path,
        "kind": chunk.kind,
        "chunker": chunk.chunker,
        "content_type": out.content_type,
        "lines" if chunk.sheet is None else "rows": [chunk.line_start, chunk.line_end],
        **({"sheet": chunk.sheet} if chunk.sheet is not None else {}),
        "content_hash": content_hash(chunk),
        "coverage": round(coverage, 3),
        "cases": len(cases),
        "ambiguous": sum(1 for o in other if o["label"] == "ambiguous"),
        "unclassified": len(unclassified),
        "uncited_code": len(uncited_code),
        "citation_failures": len(failures),
    }
    return {"chunk": chunk_rec, "cases": cases, "other_text": other, "unclassified": unclassified,
            "uncited_code": uncited_code, "citation_failures": failures}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


async def verification_chunks(manifest: dict, work_dir: Path, *, model: str, chunk_glob: list[str] | None = None,
                              agent_paths: str | None = None, on_event=print) -> tuple[list[Chunk], list[dict]]:
    root = Path(manifest["repo_root"]).resolve()
    chunks: list[Chunk] = []
    skipped: list[dict] = []
    for src in manifest["sources"]:
        if src["role"] != "verification":
            continue
        try:
            chunks.extend(await chunk_source(root, src, work_dir / "chunks", model=model,
                                             force_agent=bool(agent_paths and _glob_match(src["path"], agent_paths)),
                                             on_event=on_event))
        except Exception as e:  # noqa: BLE001 - record and continue
            skipped.append({"path": src["path"], "reason": f"chunking failed: {e}"})
    if chunk_glob:
        chunks = [c for c in chunks if any(_glob_match(c.path, g) or _glob_match(c.heading_path, g) for g in chunk_glob)]
    return chunks, skipped


def _prompt(chunk: Chunk) -> str:
    where = f"rows {chunk.line_start}-{chunk.line_end}" if chunk.sheet is not None else f"lines {chunk.line_start}-{chunk.line_end}"
    if chunk.line_map is not None and chunk.sheet is None:
        where = f"selected entries from lines {chunk.line_start}-{chunk.line_end}"
    text = f"Source: {chunk.path}\nChunk: {chunk.heading_path}\nText ({where}):\n\n{chunk.body}"
    if chunk.kind == "data_sample":
        text += ("\n\nThis is the first part of a machine-generated data file (e.g. test vectors or a "
                 "tool-generated exclusion list). Return at most one case describing the file as a whole: what "
                 "it provides and which test or flow uses it. Do not extract individual records.")
    return text


def logged_responses(log_path: Path) -> dict[str, ChunkCasesOut]:
    found: dict[str, ChunkCasesOut] = {}
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("purpose") == "cases" and entry.get("response"):
                found[entry["request"]] = ChunkCasesOut.model_validate(entry["response"])
    return found


async def extract(manifest: dict, work_dir: Path, *, model: str = DEFAULT_MODEL,
                  max_cost_usd: float = DEFAULT_MAX_COST_USD, chunk_glob: list[str] | None = None,
                  limit: int | None = None, from_log: bool = False, agent_chunking: str | None = None,
                  on_event=print) -> dict:
    chunks, skipped = await verification_chunks(manifest, work_dir, model=model, chunk_glob=chunk_glob,
                                                agent_paths=agent_chunking, on_event=on_event)
    if limit:
        chunks = chunks[:limit]
    root = Path(manifest["repo_root"]).resolve()
    llm = LLM(model, work_dir / "llm_calls.jsonl", max_cost_usd)
    cached = logged_responses(work_dir / "llm_calls.jsonl") if from_log else {}
    file_cache: dict[str, str] = {}

    async def one(chunk: Chunk) -> dict:
        user = _prompt(chunk)
        try:
            out = cached.get(user) or await llm.parse("cases", SYSTEM, user, ChunkCasesOut, max_tokens=32000)
        except (BudgetExceeded, LLMError) as e:
            on_event(f"  ! {chunk.heading_path}: {e}")
            return {"error": str(e), "chunk_id": chunk.id, "heading_path": chunk.heading_path}
        text = file_cache.get(chunk.path)
        if text is None:
            p = root / chunk.path
            text = p.read_text(encoding="utf-8", errors="replace") if chunk.sheet is None else ""
            file_cache[chunk.path] = text
        result = assemble(chunk, out, text)
        s = result["chunk"]
        on_event(f"  {s['coverage']:>6.1%}  {s['content_type']:5} cases={s['cases']:<3} "
                 f"checks={sum(c['check_count'] for c in result['cases']):<3} amb={s['ambiguous']:<2} "
                 f"uncl={s['unclassified']:<2} uncited={s['uncited_code']:<2} cite_fail={s['citation_failures']:<2} {chunk.heading_path}")
        return result

    on_event(f"Extracting verification cases from {len(chunks)} chunks with {model}")
    results = await asyncio.gather(*(one(c) for c in chunks))
    ok = [r for r in results if "chunk" in r]
    cases = [c for r in ok for c in r["cases"]]
    return {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "manifest_commit": manifest.get("repo_commit"),
        "stats": {
            "chunks": len(ok),
            "cases": len(cases),
            "by_kind": {k: sum(1 for c in cases if c["kind"] == k) for k in ("planned", "implemented", "listed")},
            "checks": sum(c["check_count"] for c in cases),
            "failing_checks": sum(c["failing_checks"] for c in cases),
            "implemented_without_failing_checks": sum(1 for c in cases if c["kind"] == "implemented" and not c["failing_checks"]),
            "ambiguous": sum(r["chunk"]["ambiguous"] for r in ok),
            "unclassified": sum(len(r["unclassified"]) for r in ok),
            "uncited_code": sum(len(r["uncited_code"]) for r in ok),
            "citation_failures": sum(len(r["citation_failures"]) for r in ok),
            "mean_coverage": round(sum(r["chunk"]["coverage"] for r in ok) / len(ok), 3) if ok else None,
            "errors": len(results) - len(ok),
            "llm_calls": llm.calls,
            "cost_usd": round(llm.spent_usd, 4),
        },
        "skipped_sources": skipped,
        "errors": [r for r in results if "error" in r],
        "chunks": [r["chunk"] for r in ok],
        "cases": cases,
        "other_text": [o for r in ok for o in r["other_text"]],
        "unclassified": [u for r in ok for u in r["unclassified"]],
        "uncited_code": [u for r in ok for u in r["uncited_code"]],
        "citation_failures": [f for r in ok for f in r["citation_failures"]],
    }


def write(result: dict, work_dir: Path) -> Path:
    path = work_dir / "cases.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return path
