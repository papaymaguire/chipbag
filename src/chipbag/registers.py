"""Register definition chunkers: SystemRDL and register Hjson.

Register properties (access, reset value, side effects, write enables, ...) become
requirements deterministically, each quoted from its exact source span. Only the
description strings are left for the LLM; each chunk's coverage counts description text
only, so property syntax never shows up as unclassified.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hjson

from chipbag.chunks import Chunk, line_offsets

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


# Verification tier of a property requirement.
# Register blocks, RAL models and headers are generated from the definition, so plain
# storage semantics (access type, reset value, read/write side effects, encoding) hold
# by construction and are exercised by generic, RAL-driven CSR tests. Properties that
# expose a hardware-side input or depend on logic outside the generated block (hardware
# set/clear/enable, write-enable signals, counters, interrupts, external registers) only
# hold if hand-written logic drives them correctly, so they need a specific test.
NEEDS_TEST_PROPERTIES = {
    # SystemRDL
    "hwset", "hwclr", "hwenable", "hwmask", "swwe", "swwel", "we", "wel", "precedence",
    "counter", "incrvalue", "decrvalue", "sticky", "stickybit", "intr",
    # register Hjson
    "hwext", "hwqe", "hwre",
}


def property_tier(prop: str) -> tuple[str, str]:
    """(tier, reason) for a register property requirement."""
    if prop in NEEDS_TEST_PROPERTIES:
        return "needs_test", "depends on logic outside the generated register block"
    return "by_construction", "generated from the register definition; exercised by generic CSR tests"


def string_extent(text: str, start: int) -> tuple[int, int] | None:
    """Inner [start, end) of the string literal beginning at or after `start`.

    Handles "double", 'single' and '''triple''' quoted strings with backslash escapes.
    """
    m = re.compile(r"'''|\"|'").search(text, start)
    if m is None:
        return None
    delim = m.group(0)
    i = m.end()
    if delim == "'''":
        end = text.find("'''", i)
        return (i, end) if end >= 0 else None
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == delim:
            return m.end(), i
        i += 1
    return None


def _fmt(value: Any, prop: str = "") -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return hex(value) if prop == "reset" and value > 9 else str(value)
    if isinstance(value, type):  # user-defined enum class
        return getattr(value, "type_name", None) or value.__name__
    if callable(getattr(value, "get_path", None)):  # node or signal reference
        return value.get_path()
    if hasattr(value, "name") and not isinstance(value, str):  # enum member, e.g. OnReadType.rclr
        return str(value.name)
    return str(value)


def _quote_record(file_text: str, starts: list[int], a: int, b: int) -> dict:
    line_a = file_text.count("\n", 0, a) + 1
    line_b = file_text.count("\n", 0, max(a, b - 1)) + 1
    return {"text": file_text[a:b], "lines": [line_a, line_b], "chars": [a, b]}


# --------------------------------------------------------------------------
# SystemRDL
# --------------------------------------------------------------------------

RDL_PROPS: dict[str, str] = {
    "sw": "software access is {v}",
    "hw": "hardware access is {v}",
    "reset": "reset value is {v}",
    "onread": "a software read has side effect {v}",
    "onwrite": "a software write has side effect {v}",
    "rclr": "a software read clears the field",
    "rset": "a software read sets the field",
    "woclr": "writing 1 clears the corresponding bit",
    "woset": "writing 1 sets the corresponding bit",
    "singlepulse": "writing 1 produces a single-cycle pulse, then the field clears",
    "hwset": "hardware can set the field ({v})",
    "hwclr": "hardware can clear the field ({v})",
    "hwenable": "hardware updates are enabled per bit by {v}",
    "hwmask": "hardware updates are masked per bit by {v}",
    "swwe": "software writes take effect only when {v} is asserted",
    "swwel": "software writes take effect only when {v} is deasserted",
    "we": "hardware writes take effect only when the write enable is asserted ({v})",
    "wel": "hardware writes take effect only when the write enable is deasserted ({v})",
    "precedence": "on simultaneous hardware and software updates, {v} takes precedence",
    "encode": "field values are encoded by enumeration {v}",
    "swmod": "hardware is notified when software modifies the field",
    "swacc": "hardware is notified when software accesses the field",
    "counter": "the field is a counter",
    "incrvalue": "the counter increments by {v}",
    "decrvalue": "the counter decrements by {v}",
    "sticky": "the field is sticky (holds a non-zero value until cleared)",
    "stickybit": "each bit is sticky (holds 1 until cleared)",
    "intr": "the field is an interrupt",
    "mementries": "the memory has {v} entries",
    "memwidth": "each memory entry is {v} bits wide",
}


def rdl_chunks(root: Path, rel: str, scope: dict) -> list[Chunk]:
    """One chunk per register/memory. Raises systemrdl.RDLCompileError if the file does not compile."""
    from systemrdl import RDLCompiler
    from systemrdl.node import FieldNode, MemNode, RegNode

    path = root / rel
    file_text = path.read_text(encoding="utf-8", errors="replace")
    starts = line_offsets(file_text)
    rdlc = RDLCompiler()
    rdlc.compile_file(str(path))
    top = rdlc.elaborate()

    def offset(ref) -> int | None:
        if ref is None or Path(ref.filename).resolve() != path.resolve():
            return None
        return starts[ref.line - 1] + ref.line_selection[0]

    def prop_quote(ref) -> tuple[int, int] | None:
        a = offset(ref)
        if a is None:
            return None
        line_end = file_text.find("\n", a)
        line_end = len(file_text) if line_end < 0 else line_end
        stop = min([p for p in (file_text.find(";", a, line_end), file_text.find("}", a, line_end)) if p >= 0] or [line_end])
        return a, stop

    chunks: list[Chunk] = []
    nodes = [n for n in top.descendants() if isinstance(n, (RegNode, MemNode))]
    all_spans: list[tuple[int, int]] = []  # every register in the file, in scope or not
    for node in nodes:
        refs = [node.inst.inst_src_ref, node.inst.def_src_ref]
        lines = [r.line for r in refs if r is not None and offset(r) is not None]
        if not lines:
            continue
        first, last = min(lines), max(lines)
        all_spans.append((first, last))
        if not _in_scope(scope, node.inst_name, first, last):
            continue

        reg_name = node.get_path()
        targets = [(node, None)] + [(f, f.inst_name) for f in (node.fields() if isinstance(node, RegNode) else [])]
        fixed, desc_ranges = [], []
        for item, field_name in targets:
            for prop, ref in item.inst.property_src_ref.items():
                if prop == "desc":
                    a = offset(ref)
                    ext = string_extent(file_text, a) if a is not None else None
                    if ext:
                        desc_ranges.append(ext)
                    continue
                if prop not in RDL_PROPS or ref.line < first or ref.line > last:
                    continue  # defaults declared outside the register are not quoted here
                span = prop_quote(ref)
                if span is None:
                    continue
                value = item.get_property(prop)
                who = f"{reg_name}.{field_name}" if field_name else reg_name
                fixed.append(
                    {
                        "statement": f"{who}: {RDL_PROPS[prop].format(v=_fmt(value, prop))}.",
                        "aspect": "register",
                        "property": prop,
                        "value": _fmt(value, prop),
                        "target": who,
                        "quotes": [_quote_record(file_text, starts, *span)],
                    }
                )
        # Field reset given as an instance assignment (`lock = 0;`) has no property src ref.
        for f in node.fields() if isinstance(node, RegNode) else []:
            if "reset" in f.inst.property_src_ref or f.get_property("reset") is None:
                continue
            at = offset(f.inst.inst_src_ref)
            m = re.compile(rf"{re.escape(f.inst_name)}\s*(\[[^\]]*\]\s*)?=\s*[^;]+").match(file_text, at) if at is not None else None
            if m is None:
                continue
            who = f"{reg_name}.{f.inst_name}"
            value = _fmt(f.get_property("reset"), "reset")
            fixed.append({"statement": f"{who}: {RDL_PROPS['reset'].format(v=value)}.", "aspect": "register",
                          "property": "reset", "value": value, "target": who,
                          "quotes": [_quote_record(file_text, starts, m.start(), m.end())]})
        fixed.sort(key=lambda r: r["quotes"][0]["chars"][0])
        a, b = starts[first - 1], starts[last] if last < len(starts) else len(file_text)
        chunks.append(
            Chunk(
                path=rel,
                heading_path=f"{Path(rel).stem} > {node.inst_name}",
                body_start=a,
                body=file_text[a:b],
                line_start=first,
                line_end=last,
                kind="register",
                coverage_ranges=[(x - a, y - a) for x, y in desc_ranges if a <= x < b],
                fixed=fixed,
            )
        )

    # Descriptions outside any register (signals, address maps, shared enums): one chunk
    # per gap between registers, description text only.
    if scope.get("kind") != "entries":
        spans = sorted(all_spans)
        gaps, prev = [], 0
        for s0, e0 in spans + [(len(starts) + 1, len(starts) + 1)]:
            if s0 > prev + 1:
                gaps.append((prev + 1, s0 - 1))
            prev = max(prev, e0)
        for first, last in gaps:
            first = max(first, 1)
            last = min(last, len(file_text.splitlines()))
            if first > last or not _in_scope(scope, "", first, last):
                continue
            a, b = starts[first - 1], starts[last] if last < len(starts) else len(file_text)
            ranges = []
            for m in re.finditer(r"\bdesc\s*=\s*", file_text[a:b]):
                ext = string_extent(file_text, a + m.end())
                if ext and ext[1] <= b:
                    ranges.append((ext[0] - a, ext[1] - a))
            if ranges:
                chunks.append(Chunk(rel, f"{Path(rel).stem} > (lines {first}-{last})", a, file_text[a:b],
                                    first, last, kind="entry", coverage_ranges=ranges))
    return chunks


def _in_scope(scope: dict, name: str, first: int, last: int) -> bool:
    from chipbag.index import _glob_match

    kind = scope.get("kind")
    if kind == "lines":
        return not (last < scope["start"] or first > scope["end"])
    if kind == "entries":
        return any(_glob_match(name, p) for p in scope.get("match") or [])
    return True


# --------------------------------------------------------------------------
# Register Hjson (OpenTitan-style: top-level `registers` list)
# --------------------------------------------------------------------------

HJSON_PROPS: dict[str, str] = {
    "swaccess": "software access is {v}",
    "hwaccess": "hardware access is {v}",
    "resval": "reset value is {v}",
    "hwqe": "hardware receives a write-qualifier pulse on software writes ({v})",
    "hwre": "hardware receives a read-enable pulse on software reads ({v})",
    "hwext": "the register is implemented outside the register block ({v})",
    "regwen": "software writes are enabled only while {v} allows them",
    "shadowed": "the register is shadowed ({v})",
    "bits": "occupies bits {v}",
}
_KEY = r"(?<![\w])[\"']?{key}[\"']?\s*:\s*"


def is_register_hjson(path: Path) -> bool:
    try:
        data = hjson.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("registers"), (list, dict))


def _desc_ranges(text: str, a: int, b: int) -> list[tuple[int, int]]:
    out = []
    for m in re.finditer(r"\bdesc\s*:\s*", text[a:b]):
        start = a + m.end()
        if text.startswith(("'''", '"', "'"), start):
            ext = string_extent(text, start)
            if ext and ext[1] <= b:
                out.append(ext)
        else:  # unquoted hjson string: to end of line
            end = text.find("\n", start)
            out.append((start, b if end < 0 else min(end, b)))
    return out


def _find_prop(text: str, key: str, a: int, b: int) -> tuple[int, int] | None:
    m = re.compile(_KEY.format(key=re.escape(key)) + r"(\"[^\"]*\"|'[^']*'|[^,\n}\]]+)").search(text, a, b)
    return (m.start(), m.end()) if m else None


def _register_entries(registers: Any) -> list[dict]:
    """Flatten an OpenTitan `registers` value into register dicts (multireg unwrapped)."""
    items = registers if isinstance(registers, list) else [r for v in registers.values() for r in v]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "multireg" in item:
            out.append(item["multireg"])
        elif "name" in item and "fields" in item:
            out.append(item)
        elif "window" in item and "name" in item["window"]:
            out.append(item["window"])
    return out


def hjson_chunks(root: Path, rel: str, scope: dict) -> list[Chunk]:
    path = root / rel
    text = path.read_text(encoding="utf-8", errors="replace")
    starts = line_offsets(text)
    data = hjson.loads(text)
    line_of = lambda off: text.count("\n", 0, off) + 1  # noqa: E731

    # Locate each register's name in order; a register's span runs to the next one.
    regs = _register_entries(data["registers"])
    reg_start = text.find("registers")
    located = []
    cursor = max(reg_start, 0)
    for reg in regs:
        m = re.compile(_KEY.format(key="name") + rf"[\"']?{re.escape(reg['name'])}[\"']?(?![\w])").search(text, cursor)
        if m is None:
            continue
        located.append((reg, text.rfind("\n", 0, m.start()) + 1))
        cursor = m.end()

    chunks: list[Chunk] = []
    for i, (reg, a) in enumerate(located):
        b = located[i + 1][1] if i + 1 < len(located) else len(text)
        first, last = line_of(a), line_of(max(a, b - 1))
        if not _in_scope(scope, reg["name"], first, last):
            continue
        fixed = []
        # Locate fields in order within the register span.
        field_spans = []
        fcursor = a
        for field in reg.get("fields", []):
            fname = field.get("name") or reg["name"]
            m = re.compile(_KEY.format(key="name") + rf"[\"']?{re.escape(fname)}[\"']?(?![\w])").search(text, fcursor, b)
            if m is None:
                m = re.compile(_KEY.format(key="bits")).search(text, fcursor, b)  # single unnamed field
            if m is None:
                continue
            field_spans.append((field, fname, text.rfind("{", a, m.start()) + 1 or m.start()))
            fcursor = m.end()
        for j, (field, fname, fa) in enumerate(field_spans):
            fb = field_spans[j + 1][2] if j + 1 < len(field_spans) else b
            for key, template in HJSON_PROPS.items():
                own = key in field
                inherited = not own and key in reg and key != "bits"
                if not (own or inherited):
                    continue
                span = _find_prop(text, key, fa, fb) if own else _find_prop(text, key, a, field_spans[0][2] if field_spans else b)
                if span is None:
                    continue
                value = field[key] if own else reg[key]
                who = f"{reg['name']}.{fname}"
                fixed.append(
                    {
                        "statement": f"{who}: {template.format(v=value)}.",
                        "aspect": "register",
                        "property": key,
                        "value": str(value),
                        "target": who,
                        "quotes": [_quote_record(text, starts, *span)],
                    }
                )
        chunks.append(
            Chunk(
                path=rel,
                heading_path=f"{Path(rel).stem} > {reg['name']}",
                body_start=a,
                body=text[a:b],
                line_start=first,
                line_end=last,
                kind="register",
                coverage_ranges=[(x - a, y - a) for x, y in _desc_ranges(text, a, b)],
                fixed=fixed,
            )
        )

    # Other top-level entries with descriptions (interrupts, alerts, parameters,
    # features, countermeasures, ...): description text only, no fixed requirements.
    m = re.search(r"^([ \t]*)registers\s*:", text, re.M)
    indent = m.group(1) if m else "  "
    keys = list(re.finditer(rf"^{indent}([A-Za-z_][\w]*)\s*:", text, re.M))
    for k, km in enumerate(keys):
        key = km.group(1)
        if key == "registers":
            continue
        a = text.rfind("\n", 0, km.start()) + 1
        b = keys[k + 1].start() if k + 1 < len(keys) else len(text)
        ranges = _desc_ranges(text, a, b)
        if not ranges or scope.get("kind") == "entries":
            continue
        first, last = line_of(a), line_of(max(a, b - 1))
        if not _in_scope(scope, key, first, last):
            continue
        chunks.append(
            Chunk(
                path=rel,
                heading_path=f"{Path(rel).stem} > {key}",
                body_start=a,
                body=text[a:b],
                line_start=first,
                line_end=last,
                kind="entry",
                coverage_ranges=[(x - a, y - a) for x, y in ranges],
            )
        )
    return chunks
