"""Normalize, spec half: turn spec sources into atomic requirements with exact provenance.

Every piece of spec text in scope is classified by the LLM as part of a requirement,
informative text, a reference, or ambiguous text that cannot be turned into a testable
requirement (with a reason and a question for the architect). Code then locates every
quote in the source file (line and character offsets are computed here, never by the
LLM) and measures how much of each section's text the quotes cover. Text nobody quoted
is recorded as `unclassified`, so silent omissions still surface as gaps.

Sources are chunked by chipbag.chunks: natively for markdown, SystemRDL and register
Hjson (register properties become requirements without the LLM), and by a chunking agent
for any other format.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from chipbag.chunks import Chunk, body_span, chunk_source, line_offsets
from chipbag.index import _glob_match
from chipbag.registers import property_tier
from chipbag.llm import LLM, BudgetExceeded, LLMError

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_COST_USD = 5.0
MIN_UNCLASSIFIED_CHARS = 20  # ignore uncovered stretches shorter than this (punctuation, glue words)

# --------------------------------------------------------------------------
# LLM output schema
# --------------------------------------------------------------------------

AmbiguityCode = Literal["VAGUE", "WEAK_MODAL", "MISSING_DETAIL", "TBD", "UNCLEAR_REFERENT", "CONFLICT"]
Aspect = Literal[
    "functional", "access_control", "error_handling", "reset", "register", "interrupt",
    "timing", "configuration", "interface", "security", "other",
]


class Ambiguity(BaseModel):
    code: AmbiguityCode
    note: str = Field(description="What is under-specified, in one sentence.")
    question: str = Field(description="A concrete question an architect could answer to resolve it.")


class RequirementOut(BaseModel):
    statement: str = Field(description="The requirement restated as one short sentence. For display and search only.")
    quotes: list[str] = Field(description="Exact excerpts of the section text that state this requirement.")
    aspect: Aspect
    condition: str | None = Field(description="The condition or mode under which it applies, if any.")
    ambiguity: Ambiguity | None = Field(description="Set only if testable but under-specified.")


class OtherTextOut(BaseModel):
    quote: str = Field(description="Exact excerpt of the section text.")
    label: Literal["informative", "reference", "ambiguous"]
    reason: str = Field(description="One sentence: why this is not a testable requirement.")
    ambiguity: Ambiguity | None = Field(description="Required when label is 'ambiguous', otherwise null.")


class SectionOut(BaseModel):
    requirements: list[RequirementOut]
    other_text: list[OtherTextOut]


SYSTEM = """\
You turn one section of a hardware specification into atomic, testable requirements, and
you account for every piece of its text.

Requirements
- One requirement is one behavior that a test or assertion either checks or does not.
  Split compound sentences: separate conditions, separate actors, the normal path and
  each error path, set and clear, read and write.
- Register and table content is requirements too: access type, reset value, field
  meaning, side effects on read or write.
- Extract only what the text states. Do not add behavior the text implies but does not
  state; behavior the spec leaves undefined is not a requirement.
- `statement` restates the requirement in one short sentence for display and search.
  It is never used as evidence; the quotes are.
- If a requirement is testable but under-specified (e.g. no value range, no timing),
  keep it as a requirement and set `ambiguity`.

Other text (everything that is not part of a requirement)
- informative: introduction, overview, rationale, background, examples.
- reference: only points to another section, document or figure.
- ambiguous: written as if it states required behavior, but cannot be turned into a
  testable requirement as written. Give the ambiguity code, a one-sentence note and a
  concrete question for the architect. Codes:
  VAGUE (untestable wording such as "secure", "appropriate", "efficient"),
  WEAK_MODAL ("should", "may", "typically" where a requirement seems intended),
  MISSING_DETAIL (a value, range, width, count, timing or ordering is missing),
  TBD (placeholder or unfinished text),
  UNCLEAR_REFERENT (cannot tell which register, signal, agent or state it means),
  CONFLICT (contradicts other text in this section).

Quoting rules (quotes are checked by code against the source)
- Copy quotes verbatim from the section text. You may leave out markdown formatting
  characters (backticks, asterisks, link URLs, table pipes, list markers), but do not
  change, reorder or summarize words.
- A quote is one contiguous excerpt. If a requirement draws on non-adjacent text, give
  several quotes.
- Coverage: every sentence, list item and table row of the section must appear in at
  least one quote, in `requirements` or `other_text`. Text may be quoted by more than
  one requirement. Do not quote the section heading.

Structured sources (register definitions, data files, code)
- The section may be structured text rather than prose. When told that only the
  description text needs to be accounted for, quote and classify only the text inside
  description strings; the surrounding syntax is handled separately.
- When the prompt lists requirements already derived from properties (access type,
  reset value, side effects), do not repeat them. Description text that only restates
  one of them is informative. Description text that contradicts one is ambiguous with
  code CONFLICT; name the property in the note.
"""

# --------------------------------------------------------------------------
# Text normalization with an offset map back to the raw source
# --------------------------------------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)]*)\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_LIST_MARKER = re.compile(r"^[ \t]*([-*+]|\d+[.)])[ \t]+", re.M)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}.*$", re.M)
_DROP_CHARS = set("`*|#>\\")
_TRANSLATE = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})


def normalize(raw: str, markdown: bool = True) -> tuple[str, list[int]]:
    """Return (normalized text, index into raw for each normalized char).

    Always: maps typographic quotes and dashes to ASCII, lowercases, collapses whitespace.
    With markdown=True, also drops markdown formatting (link targets, emphasis, code ticks,
    table pipes and rules, list markers, html tags). Code must use markdown=False, or
    comparisons like `a < b ... c > d` would be stripped as html tags.
    """
    drop = [False] * len(raw)
    drop_chars = _DROP_CHARS if markdown else set()

    def drop_range(a: int, b: int) -> None:
        for k in range(a, b):
            drop[k] = True

    if markdown:
        for m in _LINK.finditer(raw):
            drop_range(m.start(), m.start(1))  # "[" or "!["
            drop_range(m.end(1), m.end())  # "](url)"
        for rx in (_HTML_TAG, _TABLE_RULE):
            for m in rx.finditer(raw):
                drop_range(m.start(), m.end())
        for m in _LIST_MARKER.finditer(raw):
            drop_range(m.start(1), m.end(1))

    out: list[str] = []
    idx: list[int] = []
    pending_space: int | None = None
    for i, ch in enumerate(raw):
        if drop[i] or ch in drop_chars:
            if ch in drop_chars and not drop[i] and ch == "|":
                pending_space = pending_space if pending_space is not None else i
            continue
        if ch.isspace():
            if out and pending_space is None:
                pending_space = i
            continue
        if pending_space is not None and out:
            out.append(" ")
            idx.append(pending_space)
        pending_space = None
        out.append(ch.translate(_TRANSLATE).lower())
        idx.append(i)
    return "".join(out), idx


_BRACKETED = re.compile(r"\[([^\]]*)\]")


def locate(norm_body: str, quote: str, prefer=None, markdown: bool = True) -> tuple[int, int] | None:
    """Find a quote in the normalized body; return its [start, end) in normalized coordinates.

    Fallback: a quote may keep a link's brackets but drop its URL ("[SRAM owner]"); the
    body has the link reduced to its text, so retry with brackets removed. Only used when
    the exact match fails, so bit ranges like DATA[31:0] still match exactly.
    When the quote occurs more than once, the first occurrence for which `prefer(a, b)` is
    true wins (used to prefer text not yet covered by another quote).
    """
    variants = [quote]
    if markdown and "[" in quote:
        variants.append(_BRACKETED.sub(r"\1", quote))
    for variant in variants:
        q = normalize(variant, markdown)[0].strip()
        for candidate in (q, q.rstrip(".,;:")):
            if 0 < len(candidate) < 3 and candidate.isalnum():
                m = re.search(rf"(?<![a-z0-9_]){re.escape(candidate)}(?![a-z0-9_])", norm_body)
                if m:
                    return m.start(), m.end()
            if len(candidate) >= 3:
                pos = norm_body.find(candidate)
                first = pos
                while pos >= 0:
                    if prefer is None or prefer(pos, pos + len(candidate)):
                        return pos, pos + len(candidate)
                    pos = norm_body.find(candidate, pos + 1)
                if first >= 0:
                    return first, first + len(candidate)
    return None


_FENCED = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.M | re.S)


def coverage_mask(chunk: Chunk, norm_idx: list[int]) -> list[bool]:
    """For each normalized char: does it count toward text coverage?

    Only the chunk's coverage ranges count when it has them (register descriptions,
    string values of structured files). Fenced code blocks in markdown never count:
    they are examples, and requirements they imply are stated in prose.
    """
    n = len(chunk.body)
    raw = [chunk.coverage_ranges is None] * (n + 1)
    for a, b in chunk.coverage_ranges or []:
        for i in range(max(a, 0), min(b, n)):
            raw[i] = True
    if chunk.path.lower().endswith((".md", ".markdown")):
        for m in _FENCED.finditer(chunk.body):
            for i in range(m.start(), m.end()):
                raw[i] = False
    return [raw[i] for i in norm_idx]


def markup_only(quote: str, markdown: bool = True) -> bool:
    """True if a quote is pure markup (e.g. an html comment) with no text once normalized."""
    return not normalize(quote, markdown)[0].strip()


_TABLE_HEADER = re.compile(r"^[^\n]*\|[^\n]*\n(?=[ \t]*\|?[ \t]*:?-{3,})", re.M)


def table_header_ranges(raw: str) -> list[tuple[int, int]]:
    """Raw [start, end) ranges of markdown table header rows (column labels, not content)."""
    return [(m.start(), m.end()) for m in _TABLE_HEADER.finditer(raw)]


def content_hash(chunk: Chunk) -> str:
    return hashlib.sha256(normalize(chunk.body)[0].encode()).hexdigest()


# --------------------------------------------------------------------------
# Locating, coverage and assembly
# --------------------------------------------------------------------------


def _span(chunk: Chunk, norm_idx: list[int], a: int, b: int, file_starts: list[int]) -> dict:
    return body_span(chunk, norm_idx[a], norm_idx[b - 1] + 1, file_starts)


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def assemble(chunk: Chunk, out: SectionOut, file_text: str) -> dict:
    """Locate quotes, compute coverage and unclassified text, and build output records."""
    norm, norm_idx = normalize(chunk.body)
    starts = line_offsets(file_text)
    covered = [False] * len(norm)
    counts = coverage_mask(chunk, norm_idx)
    # Markdown table header rows are labels, not content: count them as covered.
    if chunk.path.lower().endswith((".md", ".markdown")):
        for a, b in table_header_ranges(chunk.body):
            for k, raw_i in enumerate(norm_idx):
                if a <= raw_i < b:
                    covered[k] = True
    dropped: list[dict] = []

    def place(quote: str) -> dict | None:
        pos = locate(norm, quote, prefer=lambda a, b: not all(covered[a:b]))
        if pos is None:
            return None
        a, b = pos
        for k in range(a, b):
            covered[k] = True
        return {"text": quote, **_span(chunk, norm_idx, a, b, starts)}

    base = {"path": chunk.path, "heading_path": chunk.heading_path, "section_id": chunk.id}
    requirements = []
    for f in chunk.fixed:
        tier, tier_reason = property_tier(f["property"])
        requirements.append(
            {
                "id": "req_" + _short_hash(chunk.path, f["target"], f["property"], *(q["text"] for q in f["quotes"])),
                **base,
                "source": "property",
                "tier": tier,
                "tier_reason": tier_reason,
                "statement": f["statement"],
                "aspect": f["aspect"],
                "condition": None,
                "ambiguity": None,
                "target": f["target"],
                "property": f["property"],
                "value": f["value"],
                "quotes": f["quotes"],
                "quote_hash": _short_hash(*(q["text"] for q in f["quotes"])),
            }
        )
    for r in out.requirements:
        placed = [place(q) for q in r.quotes]
        bad = [q for q, p in zip(r.quotes, placed) if p is None]
        if bad or not placed:
            dropped.append({**base, "kind": "requirement", "statement": r.statement, "unverified_quotes": bad})
            continue
        quotes = sorted(placed, key=lambda q: q["chars"][0])
        norm_quotes = [normalize(q["text"])[0].strip() for q in quotes]
        requirements.append(
            {
                "id": "req_" + _short_hash(chunk.path, chunk.heading_path, *norm_quotes),
                **base,
                "source": "llm",
                "tier": "needs_test",
                "tier_reason": None,
                "statement": r.statement,
                "aspect": r.aspect,
                "condition": r.condition,
                "ambiguity": r.ambiguity.model_dump() if r.ambiguity else None,
                "quotes": quotes,
                "quote_hash": _short_hash(*norm_quotes),
            }
        )

    other = []
    for o in out.other_text:
        if markup_only(o.quote):
            continue  # e.g. an html comment: nothing to locate, nothing to account for
        placed = place(o.quote)
        if placed is None:
            dropped.append({**base, "kind": o.label, "unverified_quotes": [o.quote]})
            continue
        other.append(
            {
                **base,
                "label": o.label,
                "reason": o.reason,
                "ambiguity": o.ambiguity.model_dump() if o.ambiguity else None,
                "quote": placed,
            }
        )

    # Uncovered stretches of real text become `unclassified`.
    unclassified = []
    k = 0
    while k < len(norm):
        if covered[k] or not counts[k] or norm[k] == " ":
            k += 1
            continue
        j = k
        while j < len(norm) and not covered[j] and counts[j]:
            j += 1
        segment = norm[k:j].strip()
        if sum(1 for c in segment if c.isalnum()) >= MIN_UNCLASSIFIED_CHARS:
            span = _span(chunk, norm_idx, k, j, starts)
            unclassified.append({**base, **span, "text": chunk.body[norm_idx[k] : norm_idx[j - 1] + 1].strip()})
        k = j

    content = [i for i, c in enumerate(norm) if c != " " and counts[i]]
    coverage = sum(1 for i in content if covered[i]) / len(content) if content else 1.0
    section = {
        "id": chunk.id,
        "path": chunk.path,
        "heading_path": chunk.heading_path,
        "kind": chunk.kind,
        "chunker": chunk.chunker,
        "lines": [chunk.line_start, chunk.line_end],
        "content_hash": content_hash(chunk),
        "coverage": round(coverage, 3),
        "requirements": len(requirements),
        "property_requirements": len(chunk.fixed),
        "by_construction": sum(1 for q in requirements if q["tier"] == "by_construction"),
        "informative": sum(1 for o in other if o["label"] == "informative"),
        "references": sum(1 for o in other if o["label"] == "reference"),
        "ambiguous": sum(1 for o in other if o["label"] == "ambiguous"),
        "unclassified": len(unclassified),
        "dropped": len(dropped),
    }
    return {"section": section, "requirements": requirements, "other_text": other,
            "unclassified": unclassified, "dropped": dropped}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


async def spec_chunks(manifest: dict, work_dir: Path, *, model: str, section_glob: list[str] | str | None = None,
                      agent_paths: str | None = None, on_event=print) -> tuple[list[Chunk], list[dict]]:
    """Chunk every spec source: native chunker when the format has one, else the chunking agent."""
    root = Path(manifest["repo_root"]).resolve()
    chunks: list[Chunk] = []
    skipped: list[dict] = []
    for src in manifest["sources"]:
        if src["role"] != "spec":
            continue
        try:
            chunks.extend(await chunk_source(root, src, work_dir / "chunks", model=model,
                                             force_agent=bool(agent_paths and _glob_match(src["path"], agent_paths)),
                                             on_event=on_event))
        except Exception as e:  # noqa: BLE001 - record and continue with the other sources
            skipped.append({"path": src["path"], "reason": f"chunking failed: {e}"})
    if section_glob:
        globs = [section_glob] if isinstance(section_glob, str) else section_glob
        chunks = [c for c in chunks if any(_glob_match(c.heading_path, g) for g in globs)]
    return chunks, skipped


def _prompt(chunk: Chunk) -> str:
    text = (
        f"Document: {chunk.path}\nSection: {chunk.heading_path}\n"
        f"Section text (lines {chunk.line_start}-{chunk.line_end}):\n\n{chunk.body}"
    )
    if chunk.kind not in ("section", "register"):
        text += ("\n\nThis is structured data. Each statement must name its subject explicitly, using the "
                 "name the source gives it (the item, partition, parameter, field, register or signal), "
                 "never just 'the item' or 'the partition'.")
    if chunk.coverage_ranges is not None:
        text += ("\n\nOnly the description text in this section needs to be accounted for; "
                 "the property syntax is covered separately.")
    if chunk.fixed:
        listed = "\n".join(f"- {f['statement']}" for f in chunk.fixed)
        text += f"\n\nRequirements already derived from this section's properties (do not repeat them):\n{listed}"
    return text


def _needs_llm(chunk: Chunk) -> bool:
    """False when the chunk has no free text to classify (e.g. a register with no descriptions)."""
    if chunk.coverage_ranges is None:
        return bool(normalize(chunk.body)[0].strip())
    return any(normalize(chunk.body[a:b])[0].strip() for a, b in chunk.coverage_ranges)


def logged_responses(log_path: Path) -> dict[str, SectionOut]:
    """Latest logged extraction response per exact prompt, for re-assembly without new LLM calls."""
    import json

    found: dict[str, SectionOut] = {}
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("purpose") == "requirements" and entry.get("response"):
                found[entry["request"]] = SectionOut.model_validate(entry["response"])
    return found


async def extract(manifest: dict, work_dir: Path, *, model: str = DEFAULT_MODEL,
                  max_cost_usd: float = DEFAULT_MAX_COST_USD, section_glob: list[str] | str | None = None,
                  limit: int | None = None, from_log: bool = False, agent_chunking: str | None = None,
                  on_event=print) -> dict:
    """Extract requirements. With from_log, reuse logged responses for unchanged sections
    (same prompt text) and only call the LLM for the rest."""
    chunks, skipped = await spec_chunks(manifest, work_dir, model=model, section_glob=section_glob,
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
            if not _needs_llm(chunk):
                out = SectionOut(requirements=[], other_text=[])
            else:
                out = cached.get(user) or await llm.parse("requirements", SYSTEM, user, SectionOut)
        except (BudgetExceeded, LLMError) as e:
            on_event(f"  ! {chunk.heading_path}: {e}")
            return {"error": str(e), "section_id": chunk.id, "heading_path": chunk.heading_path}
        text = file_cache.setdefault(chunk.path, (root / chunk.path).read_text(encoding="utf-8", errors="replace"))
        result = assemble(chunk, out, text)
        s = result["section"]
        on_event(f"  {s['coverage']:>6.1%}  req={s['requirements']:<3} prop={s['property_requirements']:<3} amb={s['ambiguous']:<2} "
                 f"uncl={s['unclassified']:<2} drop={s['dropped']:<2} {chunk.heading_path}")
        return result

    on_event(f"Extracting requirements from {len(chunks)} sections with {model}")
    results = await asyncio.gather(*(one(c) for c in chunks))

    ok = [r for r in results if "section" in r]
    return {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "manifest_commit": manifest.get("repo_commit"),
        "stats": {
            "sections": len(ok),
            "requirements": sum(len(r["requirements"]) for r in ok),
            "property_requirements": sum(r["section"]["property_requirements"] for r in ok),
            "by_construction": sum(r["section"]["by_construction"] for r in ok),
            "ambiguous_text": sum(r["section"]["ambiguous"] for r in ok),
            "ambiguous_requirements": sum(1 for r in ok for q in r["requirements"] if q["ambiguity"]),
            "unclassified": sum(len(r["unclassified"]) for r in ok),
            "dropped": sum(len(r["dropped"]) for r in ok),
            "mean_coverage": round(sum(r["section"]["coverage"] for r in ok) / len(ok), 3) if ok else None,
            "errors": len(results) - len(ok),
            "llm_calls": llm.calls,
            "cost_usd": round(llm.spent_usd, 4),
        },
        "skipped_sources": skipped,
        "errors": [r for r in results if "error" in r],
        "sections": [r["section"] for r in ok],
        "requirements": [q for r in ok for q in r["requirements"]],
        "other_text": [o for r in ok for o in r["other_text"]],
        "unclassified": [u for r in ok for u in r["unclassified"]],
        "dropped": [d for r in ok for d in r["dropped"]],
    }


def write(result: dict, work_dir: Path) -> Path:
    path = work_dir / "requirements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return path
