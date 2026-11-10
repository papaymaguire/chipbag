"""Chunking: turn any manifest source into line-range chunks for extraction.

Native chunkers handle formats we can parse (markdown, SystemRDL, register Hjson).
Everything else, or anything a native chunker fails on, goes to a fallback agent that
can inspect the file however it needs (writing scripts with Bash, using installed
parsers) and submits line ranges. Code, not the agent, reads the text for each range and
validates the result; results are cached by file hash and scope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MAX_CHUNK_LINES = 400
MIN_LINE_COVERAGE = 0.95
CHUNK_CACHE_VERSION = 2  # bump when the agent's output format changes
SMALL_FILE_LINES = 800  # a text file up to this size is one chunk (e.g. one test)
ROWS_PER_CHUNK = 15  # table rows per chunk
ENTRY_BLOCK_MAX_LINES = 80  # largest structured entry kept whole
ENTRY_CHUNK_LINES = 150  # entry blocks per chunk, by total lines
DATA_SAMPLE_LINES = 150  # lines of a generated data file shown to extraction


@dataclass
class Chunk:
    path: str
    heading_path: str  # display/filter path, e.g. "Spec > Block > Section" or "regfile > REG"
    body_start: int  # char offset of the body in the file
    body: str
    line_start: int
    line_end: int
    kind: str = "section"  # section | register | entry | agent
    # Body-relative [start, end) ranges that count for text coverage; None = the whole body.
    coverage_ranges: list[tuple[int, int]] | None = None
    # Requirements derived deterministically (e.g. register properties), already located.
    fixed: list[dict] = field(default_factory=list)
    chunker: str = "native"
    # Synthetic bodies (table rows, gathered entries) are not a contiguous slice of the
    # file: line_map gives the source line (or table row) of each body line.
    line_map: list[int] | None = None
    sheet: str | None = None  # set for table chunks; line_map then holds row numbers

    @property
    def id(self) -> str:
        return "sec_" + hashlib.sha256(f"{self.path}\n{self.heading_path}".encode()).hexdigest()[:12]


def line_offsets(text: str) -> list[int]:
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def body_span(chunk: Chunk, a: int, b: int, file_starts: list[int]) -> dict:
    """Source location of body chars [a, b): lines and chars, or rows for table chunks."""
    if chunk.line_map is None:
        raw_a, raw_b = chunk.body_start + a, chunk.body_start + b
        return {"lines": [line_of(file_starts, raw_a), line_of(file_starts, max(raw_a, raw_b - 1))],
                "chars": [raw_a, raw_b]}
    body_starts = line_offsets(chunk.body)
    first = chunk.line_map[min(line_of(body_starts, a), len(chunk.line_map)) - 1]
    last = chunk.line_map[min(line_of(body_starts, max(a, b - 1)), len(chunk.line_map)) - 1]
    if chunk.sheet is not None:
        return {"rows": [first, last], "sheet": chunk.sheet}
    return {"lines": [first, last]}


def line_of(starts: list[int], offset: int) -> int:
    """1-based line containing `offset`, given line start offsets."""
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def _slice_lines(text: str, starts: list[int], first: int, last: int) -> tuple[int, str]:
    a = starts[first - 1]
    b = starts[last] if last < len(starts) else len(text)
    return a, text[a:b]


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def markdown_chunks(root: Path, rel: str, scope: dict) -> list[Chunk]:
    from chipbag.index import _glob_match, markdown_headings

    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    starts = line_offsets(text)
    n_lines = len(text.splitlines())
    heads = markdown_headings(text)

    if scope["kind"] == "whole_file":
        selected = list(heads)
    elif scope["kind"] == "sections":
        patterns = scope.get("sections") or []
        roots = [h for h in heads if any(_glob_match(h.path, p) for p in patterns)]
        selected = [h for h in heads if any(r.start <= h.start <= r.end for r in roots)]
    else:  # lines
        selected = [h for h in heads if not (h.end < scope["start"] or h.start > scope["end"])]

    heading_lines = sorted(h.start for h in heads)
    chunks = []
    # Text before the first heading (or the whole file, if it has no headings).
    pre_end = (heading_lines[0] - 1) if heading_lines else n_lines
    in_scope = scope["kind"] == "whole_file" or (scope["kind"] == "lines" and scope["start"] <= pre_end)
    if pre_end >= 1 and in_scope:
        first, last = 1, pre_end if scope["kind"] != "lines" else min(pre_end, scope["end"])
        if scope["kind"] == "lines":
            first = max(1, scope["start"])
        a, body = _slice_lines(text, starts, first, last)
        if body.strip():
            chunks.append(Chunk(rel, Path(rel).name, a, body, first, last))
    for h in selected:
        own_end = next((l - 1 for l in heading_lines if l > h.start), n_lines)
        first, last = h.start + 1, min(own_end, h.end)
        if first > last:
            continue
        a, body = _slice_lines(text, starts, first, last)
        if not body.strip():
            continue
        chunks.append(Chunk(rel, h.path, a, body, first, last))
    return chunks


# --------------------------------------------------------------------------
# Tables (xlsx/csv): one body line per row, "Header: value" pairs
# --------------------------------------------------------------------------

TABLES = {".xlsx", ".xlsm", ".csv"}
STRUCTURED = {".hjson", ".json", ".yaml", ".yml"}


def table_rows(path: Path, sheet: str | None) -> list[tuple[str | None, list[str], list[tuple[int, list[str]]]]]:
    """[(sheet, header, [(row number, cells)])] for a csv, one xlsx sheet, or every sheet.

    Row numbers are 1-based, as a spreadsheet shows them. The header is the first row
    with at least two non-empty cells.
    """
    import csv

    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            sheets = [(None, [list(r) for r in csv.reader(f)])]
    else:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            names = [sheet] if sheet else wb.sheetnames
            sheets = [(n, [["" if c is None else str(c) for c in row] for row in wb[n].iter_rows(values_only=True)])
                      for n in names]
        finally:
            wb.close()
    out = []
    for name, raw in sheets:
        for i, row in enumerate(raw):
            if sum(1 for c in row if c.strip()) >= 2:
                header = [c.strip() for c in row]
                rows = [(j + 1, r) for j, r in enumerate(raw) if j > i and any(c.strip() for c in r)]
                out.append((name, header, rows))
                break
    return out


def table_chunks(root: Path, rel: str, scope: dict) -> list[Chunk]:
    from chipbag.index import row_matches

    chunks = []
    for sheet, header, rows in table_rows(root / rel, scope.get("sheet")):
        if scope["kind"] == "rows":
            rows = [(n, r) for n, r in rows if row_matches(header, r, scope.get("column"), scope.get("contains") or [])]
        for g in range(0, len(rows), ROWS_PER_CHUNK):
            group = rows[g : g + ROWS_PER_CHUNK]
            lines = []
            for n, cells in group:
                padded = header + [""] * max(0, len(cells) - len(header))
                pairs = [f"{padded[k] or f'col{k + 1}'}: {' '.join(c.split())}" for k, c in enumerate(cells) if c.strip()]
                lines.append(f"[row {n}] " + " | ".join(pairs))
            label = f"{Path(rel).name}{f' > {sheet}' if sheet else ''} > rows {group[0][0]}-{group[-1][0]}"
            chunks.append(Chunk(rel, label, 0, "\n".join(lines) + "\n", group[0][0], group[-1][0], kind="rows",
                                line_map=[n for n, _ in group], sheet=sheet or ""))
    return chunks


# --------------------------------------------------------------------------
# Structured entries (hjson/json/yaml): the lines of each matching entry
# --------------------------------------------------------------------------

_QUOTES = {'"', "'"}


def _brace_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(first, last) 1-based line spans of {...} blocks, ignoring braces in quotes and // comments."""
    blocks, stack = [], []
    for n, line in enumerate(lines, 1):
        quote = None
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in _QUOTES:
                quote = ch
            elif ch == "/" and line[i : i + 2] == "//":
                break
            elif ch == "{":
                stack.append(n)
            elif ch == "}" and stack:
                blocks.append((stack.pop(), n))
    return blocks


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" -\t"))


def entry_chunks(root: Path, rel: str, scope: dict) -> list[Chunk]:
    """Each line naming a matching entry, expanded to its innermost {...} block (hjson/json)
    or its indentation block (yaml); blocks gathered into chunks with a line map."""
    from chipbag.index import _glob_match, load_structured, named_entries

    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    names = {n for n in named_entries(load_structured(root / rel))
             if any(_glob_match(n, p) for p in scope.get("match") or [])}
    blocks = [b for b in _brace_blocks(lines) if b[1] - b[0] < ENTRY_BLOCK_MAX_LINES]

    spans = []
    for n, line in enumerate(lines, 1):
        if not any(name in line for name in names):
            continue
        inner = [b for b in blocks if b[0] <= n <= b[1]]
        if inner:
            spans.append(min(inner, key=lambda b: b[1] - b[0]))
            continue
        end = n
        while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > _indent(line)):
            end += 1
        spans.append((n, min(end, n + ENTRY_BLOCK_MAX_LINES - 1)))

    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    chunks: list[Chunk] = []
    group: list[list[int]] = []

    def flush() -> None:
        if not group:
            return
        line_map = [n for a, b in group for n in range(a, b + 1)]
        body = "\n".join(lines[n - 1] for n in line_map) + "\n"
        label = f"{Path(rel).name} > entries L{line_map[0]}-{line_map[-1]}"
        chunks.append(Chunk(rel, label, 0, body, line_map[0], line_map[-1], kind="entries", line_map=line_map))
        group.clear()

    for a, b in merged:
        if group and sum(y - x + 1 for x, y in group) + (b - a + 1) > ENTRY_CHUNK_LINES:
            flush()
        group.append([a, b])
    flush()
    return chunks


# --------------------------------------------------------------------------
# Plain text (code, scripts, anything else) small enough for one chunk
# --------------------------------------------------------------------------


def is_text(path: Path) -> bool:
    try:
        return b"\0" not in path.read_bytes()[:8192]
    except OSError:
        return False


def text_chunks(root: Path, rel: str, scope: dict) -> list[Chunk] | None:
    """The whole file or the scoped line range as one chunk, if small enough; else None."""
    if scope["kind"] not in ("whole_file", "lines"):
        return None
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    n_lines = len(text.splitlines())
    first, last = (scope["start"], scope["end"]) if scope["kind"] == "lines" else (1, n_lines)
    if n_lines == 0 or last - first + 1 > SMALL_FILE_LINES:
        return None
    a, body = _slice_lines(text, line_offsets(text), first, last)
    label = Path(rel).name if scope["kind"] == "whole_file" else f"{Path(rel).name} > L{first}-{last}"
    # Hjson/JSON: only string values are content; keys and punctuation are syntax.
    ranges = string_ranges(body) if Path(rel).suffix.lower() in {".hjson", ".json"} else None
    return [Chunk(rel, label, a, body, first, last, kind="file", coverage_ranges=ranges)]


def string_ranges(text: str) -> list[tuple[int, int]]:
    """Inner [start, end) ranges of quoted string literals ("..", '..', '''..'''), skipping // comments."""
    from chipbag.registers import string_extent

    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        if ch in "\"'":
            ext = string_extent(text, i)
            if ext is None:
                break
            if text[ext[0] : ext[1]].strip():
                out.append(ext)
            i = ext[1] + (3 if text.startswith("'''", ext[1]) else 1)
            continue
        i += 1
    return out


# --------------------------------------------------------------------------
# Agent fallback
# --------------------------------------------------------------------------

CHUNK_INSTRUCTIONS = """\
# Task: chunk one file for requirement or verification-case extraction

You are working in a hardware design repository. Split the file named by the user into
chunks: contiguous line ranges that each hold one self-contained unit of meaning, then
submit them with the submit_chunks tool. Do not modify the repository.

What a chunk is
- One unit a reader would cite on its own: a document section, a register or memory
  with its fields, one table or a group of related table rows, one list entry
  (testpoint, test, parameter, interrupt), one test function, sequence or class.
- Prefer the file's own structure. Small adjacent units of the same kind may share a
  chunk; keep each chunk under {max_lines} lines.
- `title_path` names the chunk from its ancestors down, joined with " > ", using the
  file's own names (e.g. "Block > Registers > CTRL" or "my_testplan > smoke").
- `kind`: section, register, table, entry, function, class, or other.

File kind
- Set `file_kind` to `generated_data` when the file is machine-generated bulk data rather
  than authored content: test vectors, coverage exclusion lists produced by a tool,
  memory images, waveform or log dumps, large lookup tables. Otherwise `authored`.
- For generated data, chunk only the first part of the file (a representative sample
  with any header or format description, under {max_lines} lines). The coverage
  requirement does not apply.

Coverage
- Chunks must not overlap and must cover at least {coverage:.0%} of the non-blank lines
  in the requested range. Leave out only boilerplate: license headers, include lines,
  pure formatting.

Use whatever helps: read the file, inspect its structure with grep, or write and run
Python scripts (installed: openpyxl, hjson, pyyaml, systemrdl-compiler). Line numbers
are 1-based and inclusive, as printed by `cat -n` or `grep -n`.
"""


def validate_agent_chunks(text: str, chunks: list[dict], first: int, last: int,
                         require_coverage: bool = True) -> list[str]:
    lines = text.splitlines()
    errors = []
    ordered = sorted(chunks, key=lambda c: c.get("line_start", 0))
    prev_end = 0
    for c in ordered:
        a, b = c.get("line_start"), c.get("line_end")
        label = f"chunk {c.get('title_path')!r} ({a}-{b})"
        if not (isinstance(a, int) and isinstance(b, int) and first <= a <= b <= last):
            errors.append(f"{label}: range must satisfy {first} <= start <= end <= {last}")
            continue
        if b - a + 1 > MAX_CHUNK_LINES:
            errors.append(f"{label}: longer than {MAX_CHUNK_LINES} lines; split it")
        if a <= prev_end:
            errors.append(f"{label}: overlaps the previous chunk (ends at {prev_end})")
        prev_end = max(prev_end, b)
    if errors or not require_coverage:
        return errors
    if not chunks:
        return ["no chunks submitted"]
    covered = {n for c in ordered for n in range(c["line_start"], c["line_end"] + 1)}
    nonblank = [n for n in range(first, last + 1) if lines[n - 1].strip()]
    missing = [n for n in nonblank if n not in covered]
    if nonblank and 1 - len(missing) / len(nonblank) < MIN_LINE_COVERAGE:
        runs, start = [], None
        for n in missing + [None]:
            if start is None:
                start = prev = n
            elif n is not None and n == prev + 1:
                prev = n
            else:
                runs.append(f"{start}-{prev}")
                start = prev = n
        errors.append(f"only {1 - len(missing) / len(nonblank):.0%} of non-blank lines covered; "
                      f"uncovered: {', '.join(runs[:15])}")
    return errors


async def agent_chunks(root: Path, rel: str, scope: dict, cache_dir: Path, *, model: str,
                       max_budget_usd: float = 1.0, on_event: Callable[[str], None] = print) -> list[Chunk]:
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

    from chipbag.agent import Agent

    path = root / rel
    text = path.read_text(encoding="utf-8", errors="replace")
    n_lines = len(text.splitlines())
    first, last = (scope["start"], scope["end"]) if scope.get("kind") == "lines" else (1, n_lines)

    key = hashlib.sha256(text.encode() + json.dumps(scope, sort_keys=True).encode()
                         + str(CHUNK_CACHE_VERSION).encode()).hexdigest()[:16]
    cache = cache_dir / f"{Path(rel).name}.{key}.yaml"
    if cache.is_file():
        cached = yaml.safe_load(cache.read_text())
        submitted, file_kind = cached["chunks"], cached.get("file_kind", "authored")
    else:
        state: dict[str, Any] = {"accepted": None, "submissions": 0, "file_kind": "authored"}

        @tool(
            "submit_chunks",
            "Submit the chunks. Validated against the file; on failure you get the problems and may resubmit (3 total).",
            {
                "type": "object",
                "properties": {
                    "file_kind": {"type": "string", "enum": ["authored", "generated_data"]},
                    "chunks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title_path": {"type": "string"},
                                "kind": {"type": "string"},
                                "line_start": {"type": "integer"},
                                "line_end": {"type": "integer"},
                            },
                            "required": ["title_path", "kind", "line_start", "line_end"],
                        },
                    }
                },
                "required": ["file_kind", "chunks"],
            },
        )
        async def submit(args: dict[str, Any]) -> dict[str, Any]:
            if state["accepted"] is not None:
                return {"content": [{"type": "text", "text": "Already accepted. Stop now."}]}
            state["submissions"] += 1
            data = args.get("file_kind") == "generated_data"
            errors = validate_agent_chunks(text, args.get("chunks") or [], first, last, require_coverage=not data)
            if errors:
                return {"content": [{"type": "text", "text": "Rejected:\n- " + "\n- ".join(errors)}], "is_error": True}
            state["accepted"] = sorted(args["chunks"], key=lambda c: c["line_start"])
            state["file_kind"] = args.get("file_kind", "authored")
            return {"content": [{"type": "text", "text": f"Accepted {len(state['accepted'])} chunks. Stop now."}]}

        server = create_sdk_mcp_server("chunk", tools=[submit])
        options = ClaudeAgentOptions(
            cwd=root,
            model=model,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": CHUNK_INSTRUCTIONS.format(max_lines=MAX_CHUNK_LINES, coverage=MIN_LINE_COVERAGE)},
            tools=["Read", "Glob", "Grep", "Bash"],
            allowed_tools=["Read", "Glob", "Grep", "Bash", "mcp__chunk__submit_chunks"],
            permission_mode="dontAsk",
            mcp_servers={"chunk": server},
            strict_mcp_config=True,
            setting_sources=[],
            max_turns=30,
            max_budget_usd=max_budget_usd,
        )
        async with Agent(root=root, on_event=lambda e: None, options=options) as agent:
            result = await agent.send(f"File: {rel}\nChunk lines {first}-{last} of {n_lines}.")
        cost = result.total_cost_usd if result else None
        on_event(f"  chunking agent: {rel}: {state['submissions']} submission(s), ${cost or 0:.3f}")
        if state["accepted"] is None:
            raise RuntimeError(f"chunking agent produced no valid chunks for {rel}")
        submitted, file_kind = state["accepted"], state["file_kind"]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(yaml.safe_dump({"path": rel, "scope": scope, "model": model, "cost_usd": cost,
                                         "file_kind": file_kind, "chunks": submitted}, sort_keys=False))

    starts = line_offsets(text)
    if file_kind == "generated_data":
        # One sample chunk stands for the whole file.
        c = submitted[0]
        last_line = min(c["line_end"], c["line_start"] + DATA_SAMPLE_LINES - 1)
        a, body = _slice_lines(text, starts, c["line_start"], last_line)
        return [Chunk(rel, f"{Path(rel).name} (generated data sample)", a, body, c["line_start"], last_line,
                      kind="data_sample", chunker="agent")]
    out = []
    for c in submitted:
        a, body = _slice_lines(text, starts, c["line_start"], c["line_end"])
        out.append(Chunk(rel, c["title_path"], a, body, c["line_start"], c["line_end"], kind=c["kind"], chunker="agent"))
    return out


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

MARKDOWN = {".md", ".markdown"}


def native_chunks(root: Path, rel: str, scope: dict) -> list[Chunk] | None:
    """Chunk with a native parser, or None if the format has none (or the parser fails)."""
    from chipbag import registers

    suffix = Path(rel).suffix.lower()
    kind = scope["kind"]
    try:
        if suffix in MARKDOWN and kind in ("whole_file", "sections", "lines"):
            return markdown_chunks(root, rel, scope)
        if suffix == ".rdl":
            return registers.rdl_chunks(root, rel, scope)
        if suffix == ".hjson" and registers.is_register_hjson(root / rel):
            return registers.hjson_chunks(root, rel, scope)
        if suffix in TABLES:
            return table_chunks(root, rel, scope)
        if suffix in STRUCTURED and kind == "entries":
            return entry_chunks(root, rel, scope)
        if is_text(root / rel):
            return text_chunks(root, rel, scope)
    except Exception:  # parser failure: let the agent handle it
        return None
    return None


async def chunk_source(root: Path, src: dict, cache_dir: Path, *, model: str, force_agent: bool = False,
                       on_event: Callable[[str], None] = print) -> list[Chunk]:
    rel, scope = src["path"], src["scope"]
    chunks = None if force_agent else native_chunks(root, rel, scope)
    if chunks is None:
        if not is_text(root / rel):
            raise RuntimeError("binary file with no native chunker (needs a converter)")
        on_event(f"  no native chunker for {rel}{' (forced)' if force_agent else ''}; using the chunking agent")
        chunks = await agent_chunks(root, rel, scope, cache_dir, model=model, on_event=on_event)
    return [piece for c in chunks for piece in split_large(c)]


# --------------------------------------------------------------------------
# Size cap
# --------------------------------------------------------------------------

MAX_CHUNK_CHARS = 12000  # larger chunks risk truncated structured output; split them


def split_large(chunk: Chunk, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Split an oversized contiguous chunk at line boundaries into parts of at most
    max_chars, preferring to cut before the least-indented line near the end of each
    part (a new entry or block). Coverage ranges and fixed requirements go to the part
    that contains them. Synthetic chunks (line_map) are already bounded and left alone."""
    if len(chunk.body) <= max_chars or chunk.line_map is not None:
        return [chunk]
    lines = chunk.body.splitlines(keepends=True)
    parts: list[tuple[int, int]] = []  # [start, end) line indexes
    start = 0
    while start < len(lines):
        size, end = 0, start
        while end < len(lines) and size + len(lines[end]) <= max_chars:
            size += len(lines[end])
            end += 1
        end = max(end, start + 1)
        if end < len(lines):
            window = range(start + (end - start) * 2 // 3, end)
            cands = [i for i in window if lines[i].strip()]
            if cands:
                end = min(cands, key=lambda i: (len(lines[i]) - len(lines[i].lstrip()), -i))
                end = max(end, start + 1)
        parts.append((start, end))
        start = end

    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    out = []
    for k, (i, j) in enumerate(parts, 1):
        a, b = offsets[i], offsets[j]
        ranges = None
        if chunk.coverage_ranges is not None:
            ranges = [(max(x, a) - a, min(y, b) - a) for x, y in chunk.coverage_ranges if x < b and y > a]
        fixed = [f for f in chunk.fixed if a <= f["quotes"][0]["chars"][0] - chunk.body_start < b]
        out.append(Chunk(chunk.path, f"{chunk.heading_path} (part {k}/{len(parts)})", chunk.body_start + a,
                         chunk.body[a:b], chunk.line_start + i, chunk.line_start + j - 1, kind=chunk.kind,
                         coverage_ranges=ranges, fixed=fixed, chunker=chunk.chunker))
    return out
