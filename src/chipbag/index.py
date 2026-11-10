"""Index phase: agentic discovery of the spec and verification sources for a topic.

An agent (Claude Code's own prompt and tools, plus task instructions) explores a design repo and submits a manifest of
sources (spec or verification), each with a scope and a one-line reason. Code,
not the agent, validates every scope against the files and computes hashes, so
a manifest that reaches disk always resolves. See MVDv2.md §2.1.

Each run is saved under `work/<name>/index_runs/<stamp>/` (manifest plus a
transcript) so repeated runs can be compared by hand. The confirmed manifest is
written to `work/<name>/manifest.yaml`.
"""

from __future__ import annotations

import csv
import datetime as dt
import fnmatch
import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hjson
import openpyxl
import yaml
from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from chipbag.agent import Agent

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TURNS = 40
DEFAULT_MAX_BUDGET_USD = 3.0
MAX_SUBMISSIONS = 3

MARKDOWN = {".md", ".markdown"}
TABLES = {".xlsx", ".xlsm", ".csv"}
STRUCTURED = {".hjson", ".json", ".yaml", ".yml"}
SCOPE_KINDS = ("whole_file", "sections", "rows", "entries", "lines")
BUILTIN_TOOLS = ["Read", "Glob", "Grep", "Bash"]
MCP_SERVER = "index"

# --------------------------------------------------------------------------
# File structure helpers (deterministic; shared by the agent tools and validation)
# --------------------------------------------------------------------------


@dataclass
class Heading:
    path: str  # "A > B > C"
    level: int
    start: int  # 1-based line of the heading
    end: int  # last line of the section, including subsections
    chars: int = 0


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ANCHOR = re.compile(r"\s*\{#[^}]*\}\s*$")


def markdown_headings(text: str) -> list[Heading]:
    lines = text.splitlines()
    found: list[tuple[int, str, int]] = []  # (level, title, line)
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            found.append((len(m.group(1)), _ANCHOR.sub("", m.group(2)).strip(), i))

    headings: list[Heading] = []
    stack: list[tuple[int, str]] = []
    for idx, (level, title, line) in enumerate(found):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = len(lines)
        for later_level, _, later_line in found[idx + 1 :]:
            if later_level <= level:
                end = later_line - 1
                break
        chars = sum(len(l) + 1 for l in lines[line - 1 : end])
        headings.append(Heading(" > ".join(t for _, t in stack), level, line, end, chars))
    return headings


def _glob_match(value: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(value.lower(), pattern.lower())


def load_table(path: Path, sheet: str | None) -> tuple[list[str], list[list[str]]]:
    """Return (header, data rows) for a csv or one xlsx sheet.

    The header is the first row with at least two non-empty cells.
    """
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            raw = [list(r) for r in csv.reader(f)]
    else:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet is None:
                raise ValueError(f"sheet is required for {path.name}; sheets: {wb.sheetnames}")
            if sheet not in wb.sheetnames:
                raise ValueError(f"sheet {sheet!r} not found; sheets: {wb.sheetnames}")
            raw = [
                ["" if c is None else str(c) for c in row]
                for row in wb[sheet].iter_rows(values_only=True)
            ]
        finally:
            wb.close()
    for i, row in enumerate(raw):
        if sum(1 for c in row if c.strip()) >= 2:
            header = [c.strip() for c in row]
            rows = [r for r in raw[i + 1 :] if any(c.strip() for c in r)]
            return header, rows
    return [], []


def row_matches(header: list[str], row: list[str], column: str | None, contains: list[str]) -> bool:
    if column is not None:
        if column not in header:
            return False
        idx = header.index(column)
        cells = [row[idx] if idx < len(row) else ""]
    else:
        cells = row
    text = " ".join(cells).lower()
    return any(term.lower() in text for term in contains) if contains else True


def load_structured(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".hjson":
        return hjson.loads(text)
    if suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def named_entries(data: Any) -> list[str]:
    """Entry names anywhere in the document: every `name` value of an object (testpoints,
    registers, fields), every mapping key, and every string item of a list (e.g. test
    paths in a test list)."""
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str):
                names.append(name)
            for k, v in node.items():
                if isinstance(k, str):
                    names.append(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    names.append(v)
                else:
                    walk(v)

    walk(data)
    return names


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def resolve_in_root(root: Path, rel: str) -> Path | None:
    """Resolve `rel` against root; None if it escapes the root."""
    p = Path(rel)
    p = (p if p.is_absolute() else root / p).resolve()
    return p if p == root or root in p.parents else None


# --------------------------------------------------------------------------
# Manifest validation
# --------------------------------------------------------------------------


def validate_scope(path: Path, scope: dict[str, Any]) -> tuple[list[str], int]:
    """Return (errors, unit count) for one source's scope."""
    kind = scope.get("kind")
    suffix = path.suffix.lower()
    if kind not in SCOPE_KINDS:
        return [f"scope.kind must be one of {SCOPE_KINDS}"], 0

    if kind == "whole_file":
        return [], 1

    if kind == "sections":
        if suffix not in MARKDOWN:
            return ["scope 'sections' only applies to markdown files"], 0
        patterns = scope.get("sections") or []
        if not patterns:
            return ["scope 'sections' needs at least one heading glob"], 0
        heads = markdown_headings(path.read_text(encoding="utf-8", errors="replace"))
        errors = [
            f"heading glob {p!r} matches no heading path (paths look like 'Title > Subtitle > Sub-subtitle')"
            for p in patterns
            if not any(_glob_match(h.path, p) for h in heads)
        ]
        matched = sum(1 for h in heads if any(_glob_match(h.path, p) for p in patterns))
        return errors, matched

    if kind == "rows":
        if suffix not in TABLES:
            return ["scope 'rows' only applies to xlsx/csv files"], 0
        try:
            header, rows = load_table(path, scope.get("sheet"))
        except ValueError as e:
            return [str(e)], 0
        column = scope.get("column")
        if column is not None and column not in header:
            return [f"column {column!r} not in header {header}"], 0
        matched = sum(1 for r in rows if row_matches(header, r, column, scope.get("contains") or []))
        return ([] if matched else ["row filter matches no rows"]), matched

    if kind == "entries":
        if suffix not in STRUCTURED:
            return ["scope 'entries' only applies to hjson/json/yaml files"], 0
        patterns = scope.get("match") or []
        if not patterns:
            return ["scope 'entries' needs at least one name glob in 'match'"], 0
        try:
            names = named_entries(load_structured(path))
        except Exception as e:  # parser errors vary by format
            return [f"could not parse file: {e}"], 0
        errors = [f"name glob {p!r} matches no entry" for p in patterns if not any(_glob_match(n, p) for n in names)]
        matched = sum(1 for n in names if any(_glob_match(n, p) for p in patterns))
        return errors, matched

    # lines
    start, end = scope.get("start"), scope.get("end")
    n_lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= n_lines):
        return [f"lines scope needs 1 <= start <= end <= {n_lines}"], 0
    return [], 1


def _clean_scope(scope: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "whole_file": (),
        "sections": ("sections",),
        "rows": ("sheet", "column", "contains"),
        "entries": ("match",),
        "lines": ("start", "end"),
    }[scope["kind"]]
    out: dict[str, Any] = {"kind": scope["kind"]}
    for k in keep:
        v = scope.get(k)
        if v not in (None, [], ""):
            out[k] = sorted(v) if isinstance(v, list) else v
    return out


def validate_submission(root: Path, submission: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Check every source; return (errors, canonical sources with hashes and unit counts)."""
    errors: list[str] = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, src in enumerate(submission.get("sources") or []):
        rel = str(src.get("path", "")).strip()
        where = f"sources[{i}] ({rel or 'no path'})"
        path = resolve_in_root(root, rel) if rel else None
        if path is None or not path.is_file():
            errors.append(f"{where}: not a file inside the repo")
            continue
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            errors.append(f"{where}: duplicate path; merge its scopes into one entry")
            continue
        seen.add(rel)
        if src.get("role") not in ("spec", "verification"):
            errors.append(f"{where}: role must be 'spec' or 'verification'")
            continue
        if not str(src.get("reason", "")).strip():
            errors.append(f"{where}: reason is required")
            continue
        scope = src.get("scope") or {}
        scope_errors, units = validate_scope(path, scope)
        if scope_errors:
            errors.extend(f"{where}: {e}" for e in scope_errors)
            continue
        sources.append(
            {
                "path": rel,
                "role": src["role"],
                "scope": _clean_scope(scope),
                "reason": " ".join(str(src["reason"]).split()),
                "units": units,
                "sha256": sha256_file(path),
            }
        )
    if not errors and not sources:
        errors.append("manifest has no sources")
    if sources and not any(s["role"] == "spec" for s in sources):
        errors.append("manifest has no spec source")
    sources.sort(key=lambda s: (s["role"], s["path"]))
    return errors, sources


# --------------------------------------------------------------------------
# Agent setup
# --------------------------------------------------------------------------

INDEX_INSTRUCTIONS = """\
# Task: index a hardware design repository for one topic

You are working in a hardware design repository (your working directory). The user
will name a topic: a block, IP, subsystem or feature. Find every file that specifies
the topic and every file that verifies it, then submit them as a manifest with the
submit_manifest tool. The manifest is the only output; do not modify the repository.

Repositories differ in layout, language and methodology. Do not assume any particular
directory structure; the conventions below are what to search for, not where things
must be.

## 1. Spec sources: files that state required behavior

Include:
- Architecture, hardware, micro-architecture and integration specifications; theory
  of operation; datasheets; programmer's guides and programming models; interface,
  protocol and bus specifications written for the topic.
- Register and memory-map definitions in their source form: SystemRDL (.rdl),
  register Hjson/YAML/JSON, IP-XACT (.xml), CSV/xlsx register tables, or register
  sections of a spec.
- Interrupt, alert, error, reset, clock and power behavior descriptions for the topic.

## 2. Verification sources: files that plan, perform, run or measure checks

Include each category that exists for the topic:

a. Plans: verification/test plans in any format (spreadsheets, CSV, Hjson/YAML
   testplans, markdown or other documents, vPlan/vManager plans, requirement trace
   tables). If the topic's plan imports or includes shared plans (common CSR, reset,
   interrupt, alert or security testpoints), include those shared plans too; they
   apply to the topic.
b. Simulation tests and stimulus:
   - UVM/SystemVerilog: tests, sequences and virtual sequences, directed test tasks
     or .svh test bodies, test-specific configuration classes.
   - cocotb or other Python testbenches; C/C++ testbenches (Verilator, SystemC);
     VHDL testbenches (OSVVM, UVVM, VUnit).
   - Software/firmware tests that run on a processor in the design (C, assembly,
     Rust) to exercise the topic, at block, subsystem or chip/top level.
   - Compliance or conformance suites, golden/known-answer test vectors, and
     protocol test suites, when they target the topic.
c. Checkers: scoreboards, reference/predictor models (SV, C/C++ via DPI, Python),
   monitors or protocol checkers that contain checks, and checker classes.
d. Assertions and formal: SVA/PSL assertion files, bind files, assertion modules or
   interfaces, formal property files and formal setup (SymbiYosys .sby, JasperGold
   or other formal-tool scripts), equivalence-checking setups for the topic.
e. Coverage: functional coverage models (covergroups, coverpoints, cover properties,
   coverage interfaces or bind files), coverage plans, and coverage documentation or
   exclusion/waiver files for the topic.
f. Regression and test lists: files that name which tests run, in which suite, tier
   or level (YAML/Hjson/JSON/CSV lists, simulation configuration files such as
   dvsim/sim_cfg, Makefile or CMake test targets, CI job definitions that select
   topic tests).
g. Post-silicon, emulation or FPGA validation tests and scripts for the topic, when
   they live in the repository.

Do not include as verification sources:
- Pure testbench plumbing without checks: drivers, agents' interface wiring, packages
  of type definitions, clock/reset generators, build files, simulator setup.
- Shared verification libraries and base classes that are not specific to the topic
  (unless a scoped part is).
- Per-test run configuration files that only set parameters for one test, when that
  test's source is already included. Include them only if no source exists.
- Lint, CDC, RDC, synthesis or timing constraints and waivers (not functional checks).

## 3. Never sources
RTL implementation (including inline assertions in RTL; note notable ones in
excluded_notable), generated files of any kind (generated register headers,
packages, RAL models, rendered documentation) when their source form exists,
templates when a rendered checked-in copy exists, build outputs, release notes,
changelogs, top-level READMEs, and vendored copies under third_party/, vendor/ or
similar unless they are the only copy.

## 4. Inclusion policy (apply the same decision every time)
- Include every test source file that targets the topic, each as its own source.
  When a test is split across several source files (one per processor, agent or
  side of an interface), include each file.
- Include tests at every level (block, subsystem, chip/top, software) whose purpose
  is the topic. Exclude tests that only use the topic incidentally while testing
  something else; record the closest of those in excluded_notable.
- A file dedicated to the topic: scope kind "whole_file". A shared file (a plan,
  register file, regression list, coverage model, assertion file or spec covering
  several blocks): include it, scoped to the topic's part.
- When unsure whether a file is generated, look for a header saying so or a
  generator/template that produces it; prefer the source form.

## 5. Procedure (follow it in order, so repeated runs find the same things)
1. Orient: look at the top-level layout and find where documentation, design,
   verification, formal, software tests and regression configuration live.
2. Identify the topic's identifiers from its spec: block/module/IP names and their
   abbreviations, instance names, register and signal prefixes, and test-name
   prefixes. Verification files often use a different form of the name than the spec
   (abbreviations, instance numbers, suffixes); search for every form.
3. Spec: read the heading structure of each candidate document and scope to the
   matching sections. Find the register definitions, including shared register files.
4. Verification: for each category a-g in section 2, search for the identifiers
   using common naming conventions (for example *test*, *seq*, *vseq*, *tb*, *env*,
   *scoreboard*, *sb*, *model*, *chk*, *sva*, *assert*, *bind*, *fpv*, *formal*, *.sby,
   *cov*, *testplan*, *vplan*, regression or test lists, sim/dv/verif/tb/test/tests/
   formal/fv directories). Open spreadsheets with Python (openpyxl is installed).
5. Before submitting, go through categories a-g and the register definitions and
   confirm you searched for each. In notes, list any category you searched for and
   found nothing.
6. Record near-misses (similarly named but different blocks, files that only use the
   topic incidentally, generated or vendored copies you rejected) in excluded_notable.
7. Submit. If validation fails, fix exactly the reported problems and resubmit.

## 6. Scope syntax
- "whole_file": the file is dedicated to the topic.
- "sections" (markdown): heading-path globs. A heading path joins the titles of the
  heading and its ancestors with " > ", e.g. "SoC Spec > Timer > Interrupts*"; a
  matching heading includes its subsections.
- "rows" (xlsx/csv): sheet, optional column, and `contains` terms. The header row is
  the first row with at least two non-empty cells.
- "entries" (hjson/json/yaml): globs over entry names: the `name` field of any
  object, any mapping key, or any string item of a list (e.g. test paths in a list).
- "lines" (any text file): start/end, for a shared file whose topic part is a
  contiguous range (e.g. a covergroup or a register block in a shared file).

Reasons are one factual line: what the file is and what part of the topic it covers.

## 7. Budget
You have about {max_turns} turns. Avoid reading whole large files; search and read
line ranges, and batch several searches into one command where you can. If you are
running low, submit what you have with incomplete=true and say what you did not get
to in notes.
"""

SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(SCOPE_KINDS)},
        "sections": {"type": "array", "items": {"type": "string"}, "description": "heading globs (sections)"},
        "sheet": {"type": "string", "description": "sheet name (rows, xlsx)"},
        "column": {"type": "string", "description": "column to filter on (rows); omit to search all cells"},
        "contains": {"type": "array", "items": {"type": "string"}, "description": "case-insensitive terms (rows)"},
        "match": {"type": "array", "items": {"type": "string"}, "description": "globs over object `name` fields, mapping keys or string list items (entries)"},
        "start": {"type": "integer", "description": "first line, 1-based (lines)"},
        "end": {"type": "integer", "description": "last line, inclusive (lines)"},
    },
    "required": ["kind"],
}

SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "path relative to the repo root"},
                    "role": {"type": "string", "enum": ["spec", "verification"]},
                    "scope": SCOPE_SCHEMA,
                    "reason": {"type": "string"},
                },
                "required": ["path", "role", "scope", "reason"],
            },
        },
        "excluded_notable": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["path", "reason"],
            },
        },
        "incomplete": {"type": "boolean"},
        "notes": {"type": "string", "description": "categories searched with nothing found; what was not covered, if incomplete"},
    },
    "required": ["sources", "excluded_notable", "incomplete"],
}


def _text(msg: str, error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": msg}]}
    if error:
        out["is_error"] = True
    return out


@dataclass
class _Session:
    root: Path
    submissions: int = 0
    accepted: dict[str, Any] | None = None
    last_errors: list[str] = field(default_factory=list)


def _build_tools(state: _Session):
    root = state.root

    @tool(
        "submit_manifest",
        "Submit the final manifest. It is validated against the files; on failure you get "
        f"the problems and may resubmit (at most {MAX_SUBMISSIONS} submissions in total).",
        SUBMIT_SCHEMA,
    )
    async def submit_tool(args: dict[str, Any]) -> dict[str, Any]:
        if state.accepted is not None:
            return _text("A manifest was already accepted. Stop now.")
        state.submissions += 1
        errors, sources = validate_submission(root, args)
        if errors:
            state.last_errors = errors
            left = MAX_SUBMISSIONS - state.submissions
            msg = "Manifest rejected:\n- " + "\n- ".join(errors)
            msg += f"\n{left} submission(s) left." if left > 0 else "\nNo submissions left. Stop now."
            return _text(msg, error=True)
        state.accepted = {
            "sources": sources,
            "excluded_notable": sorted(
                ({"path": e["path"], "reason": " ".join(e["reason"].split())} for e in args.get("excluded_notable") or []),
                key=lambda e: e["path"],
            ),
            "incomplete": bool(args.get("incomplete")),
            "notes": (args.get("notes") or "").strip() or None,
        }
        n_spec = sum(1 for s in sources if s["role"] == "spec")
        return _text(f"Manifest accepted: {n_spec} spec and {len(sources) - n_spec} verification sources. Stop now.")

    return [submit_tool]


def make_index_options(root: Path, model: str, max_turns: int, max_budget_usd: float, state: _Session) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(MCP_SERVER, tools=_build_tools(state))
    tools = BUILTIN_TOOLS + [f"mcp__{MCP_SERVER}__submit_manifest"]
    return ClaudeAgentOptions(
        cwd=root,
        model=model,
        # Keep Claude Code's own system prompt and append the task instructions.
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": INDEX_INSTRUCTIONS.format(max_turns=max_turns),
        },
        tools=BUILTIN_TOOLS,
        allowed_tools=tools,
        permission_mode="dontAsk",
        mcp_servers={MCP_SERVER: server},
        strict_mcp_config=True,  # ignore any user-level MCP servers
        setting_sources=[],
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


@dataclass
class IndexResult:
    manifest: dict[str, Any] | None  # None if no valid manifest was accepted
    transcript: list[dict[str, Any]]
    cost_usd: float | None
    turns: int | None
    submissions: int
    last_errors: list[str]
    started_at: str


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_index(
    repo_root: Path,
    topic: str,
    *,
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    on_event: Callable[[str], None] = print,
) -> IndexResult:
    root = repo_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"repo root not found: {repo_root}")
    started = _now()
    state = _Session(root=root)
    transcript: list[dict[str, Any]] = []

    def record(event: str) -> None:
        transcript.append({"t": _now(), "event": event})
        on_event(event)

    options = make_index_options(root, model, max_turns, max_budget_usd, state)
    async with Agent(root=root, on_event=record, options=options) as agent:
        result = await agent.send(
            f"Topic: {topic}\nRepository root: {root.name} (your working directory).\n"
            "Index the spec and verification sources for this topic and submit the manifest."
        )

    manifest = None
    if state.accepted is not None:
        manifest = {
            "topic": topic,
            "repo_root": _display_path(repo_root),
            "repo_commit": repo_commit(root),
            "generated_at": started,
            "model": model,
            "confirmed": False,
            **state.accepted,
        }
    return IndexResult(
        manifest=manifest,
        transcript=transcript,
        cost_usd=result.total_cost_usd if result else None,
        turns=result.num_turns if result else None,
        submissions=state.submissions,
        last_errors=state.last_errors,
        started_at=started,
    )


def _display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def default_name(repo_root: Path, topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return f"{repo_root.resolve().name}-{slug}"


def _dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def save_run(result: IndexResult, work_dir: Path) -> Path:
    """Save one run (manifest or failure, transcript, stats) for later comparison."""
    run_dir = work_dir / "index_runs" / result.started_at.replace(":", "")
    run_dir.mkdir(parents=True, exist_ok=True)
    _dump_yaml(
        {
            "accepted": result.manifest is not None,
            "cost_usd": result.cost_usd,
            "turns": result.turns,
            "submissions": result.submissions,
            "last_errors": result.last_errors,
        },
        run_dir / "run.yaml",
    )
    if result.manifest is not None:
        _dump_yaml(result.manifest, run_dir / "manifest.yaml")
    with (run_dir / "transcript.jsonl").open("w", encoding="utf-8") as f:
        for entry in result.transcript:
            f.write(json.dumps(entry) + "\n")
    return run_dir


def write_manifest(manifest: dict[str, Any], work_dir: Path) -> Path:
    """Write the confirmed manifest that later stages read."""
    path = work_dir / "manifest.yaml"
    _dump_yaml({**manifest, "confirmed": True}, path)
    return path


def summarize(manifest: dict[str, Any]) -> str:
    lines = [f"Topic: {manifest['topic']}  ({manifest['repo_root']} @ {(manifest.get('repo_commit') or '?')[:10]})"]
    for role in ("spec", "verification"):
        group = [s for s in manifest["sources"] if s["role"] == role]
        lines.append(f"\n{role.upper()} ({len(group)} sources, {sum(s['units'] for s in group)} units)")
        for s in group:
            scope = s["scope"]
            detail = ", ".join(f"{k}={v}" for k, v in scope.items() if k != "kind")
            lines.append(f"  {s['path']}  [{scope['kind']}{': ' + detail if detail else ''}]  units={s['units']}")
            lines.append(f"      {s['reason']}")
    if manifest["excluded_notable"]:
        lines.append("\nEXCLUDED (notable)")
        lines.extend(f"  {e['path']}: {e['reason']}" for e in manifest["excluded_notable"])
    if manifest["incomplete"]:
        lines.append(f"\nINCOMPLETE: {manifest.get('notes') or 'no notes'}")
    return "\n".join(lines)
