"""CFML (ColdFusion / Lucee) extractor for ``.cfc`` components and ``.cfm`` templates.

Parsed with tree-sitter-cfml, an optional extra (``pip install graphifyy[cfml]``).
The package ships two grammars and the extractor picks one per file:

- ``cfml``     tag-based components, templates, and ``<cfscript>`` islands inside them.
- ``cfscript`` script-style components (``component extends="Base" { ... }``). The
               ``cfml`` grammar leaves such a file as a single opaque node, so a
               file whose top-level node is ``component_file`` is re-parsed with
               the ``cfscript`` grammar.

Extracted structure:

- a component node (``.cfc`` files) with ``extends`` / ``implements`` edges;
- function nodes (``<cffunction>`` and script functions) with ``method`` edges;
- ``calls`` between functions of the same file (unscoped, ``this.`` and ``variables.``);
- ``instantiates`` for ``createObject("component", ...)``, ``new a.b.C()`` and
  ``<cfobject>``, ``calls`` for ``<cfinvoke component=...>``, ``includes`` for
  ``<cfinclude>``, ``<cfmodule>`` and the script ``include`` statement;
- WireBox wiring: ``depends_on`` for ``property inject="..."`` /
  ``<cfproperty inject="...">`` and ``uses`` for ``getInstance("...")``.

Resolution is deliberately local, as the project guidelines require. A target is
bound to a file only when it resolves relative to the current file's directory
(CFML's own first search location): a relative ``template`` path, or a component
name that maps to ``Name.cfc`` / ``sub/Name.cfc`` beside the source. Names that
need a server mapping, a web-root path or the ColdBox/WireBox registry are kept
as unresolved ``INFERRED`` references for a later cross-file pass: a sourceless
stub node (kind ``reference``) labelled with the raw name, and an edge whose
``context`` is ``cfml_component:<name>``, ``cfml_template:<name>`` or
``cfml_wirebox:<name>``. Nothing here reads server configuration.

Known gaps (documented, not guessed): ``<cfinterface>`` is not supported by the
grammar, a malformed tag is skipped rather than repaired, and dynamic names
(``createObject("component", variables.cls)``) have no static target.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from graphify.extractors.base import _make_id, _read_source_bytes, _read_text
from graphify.extractors.engine import _get_parser

_TAG_NAME_RE = re.compile(r"<cf(\w+)", re.IGNORECASE)
_START_TAG_TYPES = frozenset({"cf_start_tag", "cf_selfclose_tag", "cf_start_tag_with_selfclose"})


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _component_nid(path: Path) -> str:
    return _make_id(str(path), path.stem)


def _stub_nid(kind: str, raw: str) -> str:
    """Id of an unresolved reference, namespaced so it can never merge with another language's node."""
    return _make_id("cfml", kind, raw)


class _Cache:
    """Per-extraction directory listings, so case-insensitive lookups stay cheap."""

    def __init__(self) -> None:
        self._dirs: dict[str, dict[str, list[str]]] = {}

    def children(self, directory: Path) -> dict[str, list[str]]:
        key = str(directory)
        listing = self._dirs.get(key)
        if listing is None:
            listing = {}
            try:
                for entry in sorted(os.listdir(directory)):
                    listing.setdefault(entry.lower(), []).append(entry)
            except OSError:
                pass
            self._dirs[key] = listing
        return listing


def _find(cache: _Cache, directory: Path, segments: list[str], suffixes: tuple[str, ...]) -> Path | None:
    """Walk ``segments`` below ``directory`` matching names case-insensitively.

    CFML resolves names case-insensitively on every platform it commonly runs on.
    A segment that matches more than one entry only by case is ambiguous and
    resolves to nothing rather than to a guess.
    """
    current = directory
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        listing = cache.children(current)
        candidates: list[str] = []
        for suffix in (suffixes if last else ("",)):
            wanted = (seg + suffix).lower()
            matches = listing.get(wanted, [])
            if matches:
                candidates = matches
                break
        if len(candidates) != 1:
            return None
        current = current / candidates[0]
        if last:
            return current if current.is_file() else None
        if not current.is_dir():
            return None
    return None


class _Extraction:
    def __init__(self, path: Path, source: bytes) -> None:
        self.path = path
        self.source = source
        self.str_path = str(path)
        self.file_nid = _make_id(self.str_path)
        self.is_component = path.suffix.lower() == ".cfc"
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._node_ids: set[str] = set()
        self._edge_keys: set[tuple[str, str, str]] = set()
        self.functions: dict[str, str] = {}
        self.pending_calls: list[tuple[str, str, int]] = []
        self.cache = _Cache()
        self.owner_nid = self.file_nid
        self.add_node(self.file_nid, path.name, 1, "file")
        if self.is_component:
            self.owner_nid = _component_nid(path)
            self.add_node(self.owner_nid, path.stem, 1, "component")
            self.add_edge(self.file_nid, self.owner_nid, "contains", 1)

    # -- graph building -----------------------------------------------------
    def add_node(self, nid: str, label: str, line: int | None, kind: str, *,
                 source_backed: bool = True, callable_node: bool = False) -> None:
        if nid in self._node_ids:
            return
        self._node_ids.add(nid)
        item: dict = {
            "id": nid,
            "label": label,
            "file_type": "code",
            "source_location": f"L{line}" if line and source_backed else None,
            "metadata": {"language": "cfml", "kind": kind},
        }
        # An unresolved reference is a stub: it is named here but defined elsewhere,
        # so it must not claim this file as its source.
        if source_backed:
            item["source_file"] = self.str_path
        if callable_node:
            item["_callable"] = True
        self.nodes.append(item)

    def add_edge(self, src: str, tgt: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", context: str | None = None) -> None:
        key = (src, tgt, relation)
        if src == tgt or key in self._edge_keys:
            return
        self._edge_keys.add(key)
        edge: dict = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "source_file": self.str_path,
            "source_location": f"L{line}",
            "weight": 1.0,
        }
        if context:
            edge["context"] = context
        self.edges.append(edge)

    def add_unresolved(self, src: str, relation: str, kind: str, raw: str, line: int) -> None:
        """Record a reference no file in reach answers: a sourceless stub node plus an
        INFERRED edge whose ``context`` names what a later cross-file pass must find."""
        stub = _stub_nid(kind, raw)
        self.add_node(stub, raw, None, "reference", source_backed=False)
        self.add_edge(src, stub, relation, line, "INFERRED", f"cfml_{kind}:{raw}")

    def add_function(self, name: str, line: int) -> str:
        key = name.lower()
        existing = self.functions.get(key)
        if existing:
            return existing
        if self.is_component:
            nid = _make_id(self.owner_nid, name)
            self.add_node(nid, f".{name}()", line, "function", callable_node=True)
            self.add_edge(self.owner_nid, nid, "method", line)
        else:
            nid = _make_id(self.file_nid, name)
            self.add_node(nid, f"{name}()", line, "function", callable_node=True)
            self.add_edge(self.file_nid, nid, "contains", line)
        self.functions[key] = nid
        return nid

    # -- references ---------------------------------------------------------
    def ref_component(self, src: str, relation: str, raw: str | None, line: int) -> None:
        name = (raw or "").strip()
        if not name or "#" in name:  # empty, or a runtime-computed name
            return
        segments = name.split(".")
        if all(segments):
            hit = _find(self.cache, self.path.parent, segments, (".cfc",))
            if hit is not None:
                self.add_edge(src, _component_nid(hit), relation, line, "INFERRED")
                return
        self.add_unresolved(src, relation, "component", name, line)

    def ref_template(self, src: str, raw: str | None, line: int) -> None:
        name = (raw or "").strip().replace("\\", "/")
        if not name or "#" in name:
            return
        if not name.startswith("/"):
            parts = [p for p in name.split("/") if p and p != "."]
            base = self.path.parent
            while parts and parts[0] == "..":
                base = base.parent
                parts.pop(0)
            if parts and ".." not in parts:
                hit = _find(self.cache, base, parts, ("",))
                if hit is not None:
                    self.add_edge(src, _make_id(str(hit)), "includes", line)
                    return
        self.add_unresolved(src, "includes", "template", name, line)

    def ref_wirebox(self, src: str, relation: str, raw: str | None, line: int) -> None:
        name = (raw or "").strip()
        if not name or "#" in name:
            return
        self.add_unresolved(src, relation, "wirebox", name, line)

    def finish(self) -> dict:
        for src, name, line in self.pending_calls:
            tgt = self.functions.get(name.lower())
            if tgt:
                self.add_edge(src, tgt, "calls", line)
        return {"nodes": self.nodes, "edges": self.edges}


# -- tag grammar ---------------------------------------------------------------
def _tag_attributes(node, source: bytes) -> dict[str, str]:
    """Direct ``cf_attribute`` children (also through ``cf_tag_attributes``) of a tag node."""
    found: dict[str, str] = {}
    stack = list(node.children)
    while stack:
        child = stack.pop(0)
        if child.type == "cf_attribute":
            name = next((c for c in child.children if c.type == "cf_attribute_name"), None)
            value = next((c for c in child.children if "value" in c.type), None)
            if name is not None:
                found[_read_text(name, source).strip().lower()] = (
                    _unquote(_read_text(value, source)) if value is not None else ""
                )
        elif child.type == "cf_tag_attributes":
            stack = list(child.children) + stack
    return found


def _string_args(node, source: bytes) -> list[str | None]:
    args = next((c for c in node.children if c.type == "arguments"), None)
    if args is None:
        return []
    out: list[str | None] = []
    for arg in args.children:
        if not arg.is_named:
            continue
        text = _read_text(arg, source)
        out.append(_unquote(text) if text[:1] in "\"'" else None)
    return out


def _callee(node, source: bytes) -> tuple[str | None, str | None]:
    """(function name, receiver text) of a call_expression, or (None, None)."""
    fn = node.children[0] if node.children else None
    if fn is None:
        return None, None
    if fn.type == "identifier":
        return _read_text(fn, source), None
    if fn.type == "member_expression" and fn.children:
        return _read_text(fn.children[-1], source), _read_text(fn.children[0], source)
    return None, None


def _handle_call(ex: _Extraction, node, source: bytes, owner: str, line: int) -> None:
    name, receiver = _callee(node, source)
    if not name:
        return
    lowered = name.lower()
    if lowered == "createobject":
        args = _string_args(node, source)
        if len(args) >= 2 and args[0] and args[0].lower() == "component":
            ex.ref_component(owner, "instantiates", args[1], line)
    elif lowered == "getinstance":
        args = _string_args(node, source)
        if args and args[0]:
            ex.ref_wirebox(owner, "uses", args[0], line)
    elif receiver is None or receiver.lower() in ("this", "variables"):
        ex.pending_calls.append((owner, name, line))


def _handle_new(ex: _Extraction, node, source: bytes, owner: str, line: int) -> None:
    target = next((c for c in node.children if c.type in ("member_expression", "identifier", "string")), None)
    if target is not None:
        ex.ref_component(owner, "instantiates", _unquote(_read_text(target, source)), line)


def _walk_tags(ex: _Extraction, node, source: bytes, fn_nid: str | None, line_offset: int) -> None:
    kind = node.type
    line = node.start_point[0] + 1 + line_offset
    owner = fn_nid or ex.owner_nid
    if kind == "cf_component_open_tag":
        attrs = _tag_attributes(node, source)
        ex.ref_component(ex.owner_nid, "extends", attrs.get("extends"), line)
        for iface in (attrs.get("implements") or "").split(","):
            ex.ref_component(ex.owner_nid, "implements", iface, line)
    elif kind == "cf_function_tag":
        attrs = _tag_attributes(node, source)
        if attrs.get("name"):
            fn_nid = ex.add_function(attrs["name"], line)
    elif kind in _START_TAG_TYPES:
        match = _TAG_NAME_RE.match(_read_text(node, source)[:40])
        tag = match.group(1).lower() if match else ""
        attrs = _tag_attributes(node, source)
        if tag == "invoke":
            ex.ref_component(owner, "calls", attrs.get("component"), line)
        elif tag == "include":
            ex.ref_template(owner, attrs.get("template"), line)
        elif tag == "module":
            ex.ref_template(owner, attrs.get("template"), line)
        elif tag == "object":
            if attrs.get("type", "component").lower() == "component":
                ex.ref_component(owner, "instantiates", attrs.get("component") or attrs.get("name"), line)
        elif tag == "property":
            if attrs.get("inject"):
                ex.ref_wirebox(ex.owner_nid, "depends_on", attrs["inject"], line)
    elif kind == "cf_script_content":
        script = _read_text(node, source).encode("utf-8")
        parser, _ = _parser("cfscript")
        if parser is not None:
            _walk_script(ex, parser.parse(script).root_node, script, fn_nid, line - 1)
        return
    elif kind == "call_expression":
        _handle_call(ex, node, source, owner, line)
    elif kind == "new_expression":
        _handle_new(ex, node, source, owner, line)
    for child in node.children:
        _walk_tags(ex, child, source, fn_nid, line_offset)


# -- script grammar ------------------------------------------------------------
def _component_attributes(node, source: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    for child in node.children:
        if child.type != "component_attribute":
            continue
        key = next((c for c in child.children if c.type == "identifier"), None)
        value = next((c for c in child.children if c.type in ("string", "identifier") and c is not key), None)
        if key is not None and value is not None:
            found[_read_text(key, source).lower()] = _unquote(_read_text(value, source))
    return found


def _walk_script(ex: _Extraction, node, source: bytes, fn_nid: str | None, line_offset: int) -> None:
    kind = node.type
    line = node.start_point[0] + 1 + line_offset
    owner = fn_nid or ex.owner_nid
    if kind == "component":
        attrs = _component_attributes(node, source)
        ex.ref_component(ex.owner_nid, "extends", attrs.get("extends"), line)
        for iface in (attrs.get("implements") or "").split(","):
            ex.ref_component(ex.owner_nid, "implements", iface, line)
    elif kind == "property_declaration":
        attrs = _component_attributes(node, source)
        if attrs.get("inject"):
            ex.ref_wirebox(ex.owner_nid, "depends_on", attrs["inject"], line)
    elif kind == "function_declaration":
        name = node.child_by_field_name("name")
        if name is not None:
            fn_nid = ex.add_function(_read_text(name, source), line)
    elif kind == "include_statement":
        target = next((c for c in node.children if c.type == "string"), None)
        if target is not None:
            ex.ref_template(owner, _unquote(_read_text(target, source)), line)
    elif kind == "call_expression":
        _handle_call(ex, node, source, owner, line)
    elif kind == "new_expression":
        _handle_new(ex, node, source, owner, line)
    for child in node.children:
        _walk_script(ex, child, source, fn_nid, line_offset)


# -- entry point ---------------------------------------------------------------
def _parser(kind: str):
    """The cached tree-sitter parser for ``kind`` ('cfml' or 'cfscript').

    Uses the shared thread-local cache so the grammar is built once per worker,
    not once per file. Returns ``(parser, error)``.
    """
    return _get_parser("tree_sitter_cfml", "language_cfml" if kind == "cfml" else "language_cfscript")


def extract_cfml(path: Path, content: str | bytes | None = None) -> dict:
    """Extract components, functions and references from a ``.cfc`` / ``.cfm`` file."""
    try:
        import tree_sitter_cfml  # noqa: F401
    except ImportError as e:
        import importlib.util
        # An installed-but-broken grammar (e.g. a C extension built for another
        # Python ABI) raises ImportError too; report the real cause, not "not installed".
        if importlib.util.find_spec("tree_sitter_cfml") is None:
            return {"nodes": [], "edges": [],
                    "error": "tree_sitter_cfml not installed. Run: pip install tree-sitter-cfml"}
        return {"nodes": [], "edges": [],
                "error": f"tree_sitter_cfml is installed but failed to load: {e}"}

    try:
        source = (
            content.encode("utf-8") if isinstance(content, str)
            else content if content is not None
            else _read_source_bytes(path)
        )
        parser, parser_error = _parser("cfml")
        if parser is None:
            return {"nodes": [], "edges": [], "error": parser_error or "tree_sitter_cfml failed to load"}
        root = parser.parse(source).root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    ex = _Extraction(path, source)
    try:
        if any(child.type == "component_file" for child in root.children):
            script_parser, _ = _parser("cfscript")
            if script_parser is not None:
                _walk_script(ex, script_parser.parse(source).root_node, source, None, 0)
        else:
            _walk_tags(ex, root, source, None, 0)
    except RecursionError:
        # A pathologically nested file keeps whatever was found before the limit.
        pass
    return ex.finish()
