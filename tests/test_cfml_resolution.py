"""Cross-file resolution of CFML references.

The corpus under ``tests/fixtures/new_languages/cfml_resolution`` is three small
synthetic applications written for these tests. It is arranged so that the same
name (``lib.Toolkit``) means different files in different applications, one name
(``Twin``) is genuinely ambiguous, and one (``Missing``) does not exist.
"""
from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path

import pytest

from graphify.extract import extract
from graphify.extractors.base import _make_id
from graphify.extractors.cfml import extract_cfml

_needs_cfml = pytest.mark.skipif(
    _ilu.find_spec("tree_sitter_cfml") is None,
    reason="tree-sitter-cfml not installed (optional [cfml] extra)",
)

ROOT = Path(__file__).parent / "fixtures" / "new_languages" / "cfml_resolution"


def _component(rel: str) -> str:
    path = ROOT / rel
    return _make_id(str(path), path.stem)


def _file(graph: dict, rel: str) -> str:
    """Id of a file node as it appears in the graph (extract() re-keys file ids). Use a unique file name."""
    (found,) = [
        n["id"] for n in graph["nodes"]
        if n.get("label") == Path(rel).name and n.get("metadata", {}).get("kind") == "file"
    ]
    return found


def _stub(kind: str, raw: str) -> str:
    return _make_id("cfml", kind, raw)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    files = sorted(p for p in ROOT.rglob("*") if p.suffix in (".cfc", ".cfm"))
    return extract(files, cache_root=tmp_path_factory.mktemp("cfml-resolution"))


def _edge(graph: dict, relation: str, source: str, target: str) -> dict | None:
    for e in graph["edges"]:
        if e["relation"] == relation and e["source"] == source and e["target"] == target:
            return e
    return None


def _targets(graph: dict, source: str, relation: str) -> set[str]:
    return {e["target"] for e in graph["edges"] if e["source"] == source and e["relation"] == relation}


def _function(graph: dict, rel: str, label: str) -> str:
    owner = _component(rel)
    ids = [e["target"] for e in graph["edges"] if e["source"] == owner and e["relation"] == "method"]
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    (found,) = [i for i in ids if labels[i] == label]
    return found


# --- mappings read from Application files ------------------------------------

@_needs_cfml
def test_script_application_records_static_mappings_only():
    node = extract_cfml(ROOT / "webapp" / "Application.cfc")["nodes"][0]
    # the variable-backed and the `&`-joined values are resolved; the absolute
    # path and the computed value are ignored
    assert node["metadata"]["mappings"] == [
        {"name": "lib", "path": "../shared/lib"},
        {"name": "core", "path": "core"},
    ]


@_needs_cfml
def test_tag_application_records_mappings_from_cfset():
    node = extract_cfml(ROOT / "tagapp" / "Application.cfc")["nodes"][0]
    assert node["metadata"]["mappings"] == [{"name": "ext", "path": "../shared/lib"}]


@_needs_cfml
def test_only_application_files_record_mappings():
    node = extract_cfml(ROOT / "webapp" / "handlers" / "Main.cfc")["nodes"][0]
    assert "mappings" not in node["metadata"]


# --- components ---------------------------------------------------------------

@_needs_cfml
def test_dotted_name_resolves_through_a_declared_mapping(graph):
    edge = _edge(graph, "extends", _component("webapp/handlers/Main.cfc"), _component("shared/lib/Toolkit.cfc"))
    assert edge and edge["confidence"] == "INFERRED"


@_needs_cfml
def test_the_same_mapping_name_means_different_files_in_different_applications(graph):
    assert _edge(graph, "extends", _component("vendorapp/pages/Page.cfc"), _component("vendorapp/vendor/lib/Toolkit.cfc"))
    assert not _edge(graph, "extends", _component("vendorapp/pages/Page.cfc"), _component("shared/lib/Toolkit.cfc"))


@_needs_cfml
def test_a_nested_application_mapping_shadows_the_outer_one(graph):
    # webapp/admin has its own Application.cfc: its /lib wins over webapp's /lib
    panel = _component("webapp/admin/Panel.cfc")
    assert _edge(graph, "extends", panel, _component("vendorapp/vendor/lib/Toolkit.cfc"))
    assert not _edge(graph, "extends", panel, _component("shared/lib/Toolkit.cfc"))


@_needs_cfml
def test_a_mapping_whose_value_is_joined_from_another_variable(graph):
    run = _function(graph, "webapp/handlers/Main.cfc", ".run()")
    assert _edge(graph, "instantiates", run, _component("webapp/core/Engine.cfc"))
    assert _edge(graph, "instantiates", run, _component("shared/lib/Formatter.cfc"))


@_needs_cfml
def test_tag_application_mapping_resolves_a_reference(graph):
    go = _function(graph, "tagapp/Consumer.cfc", ".go()")
    assert _edge(graph, "instantiates", go, _component("shared/lib/Formatter.cfc"))


@_needs_cfml
def test_name_resolves_against_the_enclosing_application_directory(graph):
    run = _function(graph, "webapp/handlers/Main.cfc", ".run()")
    assert _edge(graph, "instantiates", run, _component("webapp/services/Ledger.cfc"))


@_needs_cfml
def test_unique_path_suffix_resolves_across_applications(graph):
    render = _function(graph, "vendorapp/pages/Page.cfc", ".render()")
    assert _edge(graph, "instantiates", render, _component("webapp/services/Ledger.cfc"))


@_needs_cfml
def test_ambiguous_name_stays_unresolved(graph):
    run = _function(graph, "webapp/handlers/Main.cfc", ".run()")
    assert _edge(graph, "instantiates", run, _stub("component", "Twin"))
    assert not _edge(graph, "instantiates", run, _component("webapp/a/Twin.cfc"))
    assert not _edge(graph, "instantiates", run, _component("webapp/b/Twin.cfc"))


@_needs_cfml
def test_missing_name_stays_unresolved(graph):
    run = _function(graph, "webapp/handlers/Main.cfc", ".run()")
    assert _edge(graph, "instantiates", run, _stub("component", "Missing"))


# --- templates ----------------------------------------------------------------

@_needs_cfml
def test_webroot_relative_include_resolves_against_the_application_directory(graph):
    run = _function(graph, "webapp/handlers/Main.cfc", ".run()")
    assert _edge(graph, "includes", run, _file(graph, "webapp/views/header.cfm"))


# --- WireBox ------------------------------------------------------------------

@_needs_cfml
def test_wirebox_name_resolves_within_the_application(graph):
    edge = _edge(graph, "depends_on", _component("webapp/handlers/Main.cfc"), _component("webapp/models/Cart.cfc"))
    assert edge and edge["confidence"] == "INFERRED"


@_needs_cfml
def test_wirebox_module_restricts_the_match_to_that_module(graph):
    assert _edge(graph, "depends_on", _component("webapp/handlers/Main.cfc"),
                 _component("webapp/modules/billing/models/Invoice.cfc"))


@_needs_cfml
def test_wirebox_unknown_module_member_and_dsl_ids_stay_unresolved(graph):
    main = _component("webapp/handlers/Main.cfc")
    targets = _targets(graph, main, "depends_on")
    assert _stub("wirebox", "ghost@billing") in targets
    assert _stub("wirebox", "logbox:root") in targets


# --- housekeeping ----------------------------------------------------------------

@_needs_cfml
def test_resolved_stubs_are_removed_and_unresolved_ones_kept(graph):
    ids = {n["id"] for n in graph["nodes"]}
    assert _stub("component", "lib.Toolkit") not in ids
    assert _stub("component", "core.Engine") not in ids
    assert _stub("component", "Missing") in ids
    assert _stub("component", "Twin") in ids


@_needs_cfml
def test_every_edge_endpoint_exists_after_resolution(graph):
    ids = {n["id"] for n in graph["nodes"]}
    dangling = [e for e in graph["edges"] if e["target"] not in ids and e["relation"] != "contains"]
    cfml = [e for e in dangling if str(e.get("source_file", "")).endswith((".cfc", ".cfm"))]
    assert cfml == []


@_needs_cfml
def test_resolution_keeps_the_edge_context(graph):
    edge = _edge(graph, "extends", _component("webapp/handlers/Main.cfc"), _component("shared/lib/Toolkit.cfc"))
    assert edge and edge["context"] == "cfml_component:lib.Toolkit"


@_needs_cfml
def test_resolution_is_deterministic(tmp_path):
    files = sorted(p for p in ROOT.rglob("*") if p.suffix in (".cfc", ".cfm"))
    first = extract(files, cache_root=tmp_path / "a")
    second = extract(files, cache_root=tmp_path / "b")
    key = lambda g: (sorted((e["source"], e["target"], e["relation"]) for e in g["edges"]), sorted(n["id"] for n in g["nodes"]))  # noqa: E731
    assert key(first) == key(second)
