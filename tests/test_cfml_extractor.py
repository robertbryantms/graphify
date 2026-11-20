"""Extraction coverage for CFML (ColdFusion / Lucee) components and templates.

Every fixture under ``tests/fixtures/new_languages/cfml`` is synthetic and was
written for these tests.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

from graphify.detect import FileType, classify_file
from graphify.extract import extract
from graphify.extractors.base import _make_id
from graphify.extractors.cfml import extract_cfml

# tree-sitter-cfml is an optional extra, not installed by a default `uv sync`.
# The grammar tests skip when it is absent; the missing-parser test below
# simulates the absent grammar itself and always runs.
_needs_cfml = pytest.mark.skipif(
    _ilu.find_spec("tree_sitter_cfml") is None,
    reason="tree-sitter-cfml not installed (optional [cfml] extra)",
)

FIXTURES = Path(__file__).parent / "fixtures" / "new_languages" / "cfml"


def _component_id(path: Path) -> str:
    return _make_id(str(path), path.stem)


def _stub(kind: str, raw: str) -> str:
    """Id of an unresolved reference (component, template or wirebox)."""
    return _make_id("cfml", kind, raw)


def _labels(result: dict) -> dict[str, str]:
    return {n["id"]: n["label"] for n in result["nodes"]}


def _edges(result: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e["relation"] == relation]


def _function_id(result: dict, label: str) -> str:
    matches = [n["id"] for n in result["nodes"] if n["label"] == label]
    assert len(matches) == 1, (label, matches)
    return matches[0]


def _has_edge(result: dict, relation: str, src: str, tgt: str) -> dict | None:
    for e in result["edges"]:
        if e["relation"] == relation and e["source"] == src and e["target"] == tgt:
            return e
    return None


# --- structure ---------------------------------------------------------------

@_needs_cfml
def test_cfc_yields_file_and_component_nodes():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    labels = _labels(r)
    comp = _component_id(path)
    assert labels[_make_id(str(path))] == "Order.cfc"
    assert labels[comp] == "Order"
    assert _has_edge(r, "contains", _make_id(str(path)), comp)


@_needs_cfml
def test_tag_functions_become_methods_of_the_component():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    comp = _component_id(path)
    for name in ("total", "applyDiscount", "render"):
        assert _has_edge(r, "method", comp, _function_id(r, f".{name}()")), name


@_needs_cfml
def test_script_functions_become_methods_of_the_component():
    path = FIXTURES / "InventoryHandler.cfc"
    r = extract_cfml(path)
    comp = _component_id(path)
    for name in ("index", "refresh"):
        assert _has_edge(r, "method", comp, _function_id(r, f".{name}()")), name


@_needs_cfml
def test_template_functions_hang_off_the_file_node():
    path = FIXTURES / "report.cfm"
    r = extract_cfml(path)
    fn = _function_id(r, "formatRow()")
    assert _has_edge(r, "contains", _make_id(str(path)), fn)
    assert not [n for n in r["nodes"] if n["label"] == "report"], "a template is not a component"


# --- inheritance -------------------------------------------------------------

@_needs_cfml
def test_extends_resolves_to_a_sibling_component():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    edge = _has_edge(r, "extends", _component_id(path), _component_id(FIXTURES / "Base.cfc"))
    assert edge and edge["confidence"] == "INFERRED"


@_needs_cfml
def test_extends_matches_names_case_insensitively():
    path = FIXTURES / "CaseDemo.cfc"
    r = extract_cfml(path)
    assert _has_edge(r, "extends", _component_id(path), _component_id(FIXTURES / "Base.cfc"))


@_needs_cfml
@pytest.mark.parametrize("fixture, interfaces", [
    ("Order.cfc", ["IAuditable"]),
    ("InventoryHandler.cfc", ["IAuditable", "ILoggable"]),
])
def test_implements_keeps_unresolved_interfaces_as_references(fixture, interfaces):
    path = FIXTURES / fixture
    r = extract_cfml(path)
    for name in interfaces:
        edge = _has_edge(r, "implements", _component_id(path), _stub("component", name))
        assert edge and edge["confidence"] == "INFERRED", name
        assert _labels(r)[_stub("component", name)] == name


# --- references --------------------------------------------------------------

@_needs_cfml
def test_create_object_component_resolves_through_a_subdirectory():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    pricing = _component_id(FIXTURES / "helpers" / "Pricing.cfc")
    assert _has_edge(r, "instantiates", _function_id(r, ".total()"), pricing)


@_needs_cfml
def test_create_object_of_a_non_component_type_is_ignored():
    r = extract_cfml(FIXTURES / "Order.cfc")
    assert not [n for n in r["nodes"] if "java.util" in n["label"]]


@_needs_cfml
def test_new_expression_and_script_create_object_are_instantiations():
    path = FIXTURES / "InventoryHandler.cfc"
    r = extract_cfml(path)
    pricing = _component_id(FIXTURES / "helpers" / "Pricing.cfc")
    order = _component_id(FIXTURES / "Order.cfc")
    assert _has_edge(r, "instantiates", _function_id(r, ".index()"), pricing)
    assert _has_edge(r, "instantiates", _function_id(r, ".refresh()"), order)


@_needs_cfml
def test_cfinvoke_is_a_call_to_the_component():
    r = extract_cfml(FIXTURES / "Order.cfc")
    base = _component_id(FIXTURES / "Base.cfc")
    assert _has_edge(r, "calls", _function_id(r, ".total()"), base)


@_needs_cfml
def test_cfobject_without_a_matching_file_stays_a_named_reference():
    r = extract_cfml(FIXTURES / "Order.cfc")
    edge = _has_edge(r, "instantiates", _function_id(r, ".render()"), _stub("component", "Mailer"))
    assert edge and edge["confidence"] == "INFERRED"


@_needs_cfml
def test_relative_include_binds_to_the_included_file():
    footer = FIXTURES / "partials" / "footer.cfm"
    r = extract_cfml(FIXTURES / "Order.cfc")
    edge = _has_edge(r, "includes", _function_id(r, ".render()"), _make_id(str(footer)))
    assert edge and edge["confidence"] == "EXTRACTED"


@_needs_cfml
def test_webroot_relative_include_is_not_guessed():
    r = extract_cfml(FIXTURES / "Order.cfc")
    edge = _has_edge(r, "includes", _function_id(r, ".render()"), _stub("template", "/shared/menu.cfm"))
    assert edge and edge["confidence"] == "INFERRED"
    assert _labels(r)[_stub("template", "/shared/menu.cfm")] == "/shared/menu.cfm"


@_needs_cfml
def test_script_include_statement_is_an_include():
    footer = FIXTURES / "partials" / "footer.cfm"
    r = extract_cfml(FIXTURES / "InventoryHandler.cfc")
    assert _has_edge(r, "includes", _function_id(r, ".index()"), _make_id(str(footer)))


# --- ColdBox / WireBox wiring ------------------------------------------------

@_needs_cfml
def test_script_property_inject_is_a_dependency():
    path = FIXTURES / "InventoryHandler.cfc"
    r = extract_cfml(path)
    edge = _has_edge(r, "depends_on", _component_id(path), _stub("wirebox", "stockService@catalog"))
    assert edge and edge["confidence"] == "INFERRED"


@_needs_cfml
def test_tag_cfproperty_inject_is_a_dependency():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    assert _has_edge(r, "depends_on", _component_id(path), _stub("wirebox", "priceService@catalog"))


@_needs_cfml
def test_get_instance_is_a_use_of_the_named_bean():
    r = extract_cfml(FIXTURES / "InventoryHandler.cfc")
    edge = _has_edge(r, "uses", _function_id(r, ".index()"), _stub("wirebox", "stockService"))
    assert edge and edge["confidence"] == "INFERRED"


# --- calls within one file ---------------------------------------------------

@_needs_cfml
def test_unscoped_call_to_a_function_of_the_same_component():
    r = extract_cfml(FIXTURES / "Order.cfc")
    edge = _has_edge(r, "calls", _function_id(r, ".total()"), _function_id(r, ".applyDiscount()"))
    assert edge and edge["confidence"] == "EXTRACTED"


@_needs_cfml
def test_this_and_unscoped_calls_to_one_function_make_one_edge():
    r = extract_cfml(FIXTURES / "InventoryHandler.cfc")
    src, tgt = _function_id(r, ".index()"), _function_id(r, ".refresh()")
    assert len([e for e in r["edges"] if e["relation"] == "calls" and e["source"] == src and e["target"] == tgt]) == 1


# --- script islands inside templates -----------------------------------------

@_needs_cfml
def test_cfscript_block_in_a_template_is_extracted_with_file_line_numbers():
    path = FIXTURES / "report.cfm"
    r = extract_cfml(path)
    pricing = _component_id(FIXTURES / "helpers" / "Pricing.cfc")
    edge = _has_edge(r, "instantiates", _make_id(str(path)), pricing)
    assert edge and edge["source_location"] == "L9"
    call = _has_edge(r, "calls", _make_id(str(path)), _function_id(r, "formatRow()"))
    assert call and call["source_location"] == "L10"


@_needs_cfml
def test_new_in_a_cfset_tag_is_an_instantiation():
    path = FIXTURES / "report.cfm"
    r = extract_cfml(path)
    assert _has_edge(r, "instantiates", _make_id(str(path)), _component_id(FIXTURES / "Order.cfc"))


# --- node and edge conventions -----------------------------------------------

@_needs_cfml
def test_nodes_carry_a_language_and_kind():
    path = FIXTURES / "Order.cfc"
    r = extract_cfml(path)
    kinds = {n["id"]: n["metadata"] for n in r["nodes"]}
    assert kinds[_make_id(str(path))] == {"language": "cfml", "kind": "file"}
    assert kinds[_component_id(path)] == {"language": "cfml", "kind": "component"}
    assert kinds[_function_id(r, ".total()")] == {"language": "cfml", "kind": "function"}


@_needs_cfml
def test_functions_are_marked_callable():
    r = extract_cfml(FIXTURES / "Order.cfc")
    fn = next(n for n in r["nodes"] if n["label"] == ".total()")
    assert fn["_callable"] is True


@_needs_cfml
def test_unresolved_references_are_sourceless_stubs():
    r = extract_cfml(FIXTURES / "Order.cfc")
    stub = next(n for n in r["nodes"] if n["id"] == _stub("component", "IAuditable"))
    assert stub["metadata"] == {"language": "cfml", "kind": "reference"}
    assert "source_file" not in stub and stub["source_location"] is None
    # Every node that claims a source file really is defined in that file.
    assert all(n.get("source_file") == str(FIXTURES / "Order.cfc") for n in r["nodes"] if n["metadata"]["kind"] != "reference")


@_needs_cfml
def test_unresolved_edges_name_what_a_later_pass_must_find():
    r = extract_cfml(FIXTURES / "Order.cfc")
    by_target = {e["target"]: e.get("context") for e in r["edges"]}
    assert by_target[_stub("component", "IAuditable")] == "cfml_component:IAuditable"
    assert by_target[_stub("template", "/shared/menu.cfm")] == "cfml_template:/shared/menu.cfm"
    assert by_target[_stub("wirebox", "priceService@catalog")] == "cfml_wirebox:priceService@catalog"
    resolved = [e for e in r["edges"] if e["target"] == _component_id(FIXTURES / "Base.cfc") and e["relation"] == "extends"]
    assert resolved and "context" not in resolved[0]


@_needs_cfml
def test_stub_ids_cannot_collide_with_other_languages():
    r = extract_cfml(FIXTURES / "Order.cfc")
    assert _make_id("IAuditable") not in {n["id"] for n in r["nodes"]}


# --- robustness --------------------------------------------------------------

@_needs_cfml
@pytest.mark.parametrize("fixture", ["Broken.cfc", "Contract.cfc"])
def test_unparseable_or_unsupported_files_still_yield_the_file_node(fixture):
    path = FIXTURES / fixture
    r = extract_cfml(path)
    assert "error" not in r
    assert _make_id(str(path)) in {n["id"] for n in r["nodes"]}


@_needs_cfml
def test_extraction_is_deterministic():
    first = extract_cfml(FIXTURES / "InventoryHandler.cfc")
    second = extract_cfml(FIXTURES / "InventoryHandler.cfc")
    assert first == second


@_needs_cfml
def test_content_argument_overrides_the_file_read(tmp_path):
    source = tmp_path / "Widget.cfc"
    source.write_text("<cfcomponent></cfcomponent>", encoding="utf-8")
    r = extract_cfml(source, content='component extends="Missing" {\n function run() {} \n}\n')
    assert "run" in "".join(_labels(r).values())
    assert _has_edge(r, "extends", _component_id(source), _stub("component", "Missing"))


@_needs_cfml
def test_dynamic_names_are_not_turned_into_references(tmp_path):
    source = tmp_path / "Dynamic.cfc"
    source.write_text(
        '<cfcomponent><cffunction name="build">'
        '<cfset var x = createObject("component", variables.className)>'
        '<cfset var y = createObject("component", "#prefix#.Thing")>'
        '</cffunction></cfcomponent>',
        encoding="utf-8",
    )
    r = extract_cfml(source)
    assert not _edges(r, "instantiates")


# --- integration -------------------------------------------------------------

def test_cfc_and_cfm_are_code_files():
    assert classify_file(FIXTURES / "Base.cfc") == FileType.CODE
    assert classify_file(FIXTURES / "report.cfm") == FileType.CODE


@_needs_cfml
def test_extract_dispatches_cfml_and_links_components_across_files(tmp_path):
    files = sorted(p for p in FIXTURES.rglob("*") if p.suffix in (".cfc", ".cfm"))
    r = extract(files, cache_root=tmp_path)
    ids = {n["id"] for n in r["nodes"]}
    order, base = _component_id(FIXTURES / "Order.cfc"), _component_id(FIXTURES / "Base.cfc")
    assert order in ids and base in ids
    assert _has_edge(r, "extends", order, base)


def test_cfml_missing_parser_reports_install_hint(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Missing.cfc"
    source.write_text("<cfcomponent></cfcomponent>\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "tree_sitter_cfml", None)

    result = extract([source], cache_root=tmp_path)

    assert result["nodes"] == []
    assert 'pip install "graphifyy[cfml]"' in capsys.readouterr().err
