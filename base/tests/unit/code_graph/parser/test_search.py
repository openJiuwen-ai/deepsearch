"""Tests for viewer-syntax search_nodes / search_edges."""

import pytest

from openjiuwen_search_base.codegraph import SearchResult, parse_search_query, search_edges, search_nodes, search_regex

NODES: list[dict] = [
    {
        "id": "a.py::BaseNode@L1",
        "type": "ClassNode",
        "name": "BaseNode",
        "node_type": "class",
        "path": "a.py",
        "signature": "class BaseNode",
        "owner": None,
        "tags": ["cat:core", "type:class", "lang:python"],
    },
    {
        "id": "a.py::FileNode@L10",
        "type": "ClassNode",
        "name": "FileNode",
        "node_type": "class",
        "path": "a.py",
        "signature": "class FileNode(BaseNode)",
        "owner": None,
        "tags": ["cat:core", "type:class", "lang:python"],
    },
    {
        "id": "a.py::parse_file@L20",
        "type": "FunctionNode",
        "name": "parse_file",
        "node_type": "function",
        "path": "a.py",
        "signature": "async def parse_file(path: Path) -> FileNode",
        "owner": None,
        "tags": ["cat:core", "type:function", "lang:python"],
    },
    {
        "id": "a.py::FileNode.children@L30",
        "type": "PropertyNode",
        "name": "children",
        "node_type": "property",
        "path": "a.py",
        "signature": "children: list[BaseNode]",
        "owner": "FileNode",
        "tags": ["cat:core", "type:property", "lang:python"],
    },
    {
        "id": "b.py::helper@L1",
        "type": "FunctionNode",
        "name": "helper",
        "node_type": "function",
        "path": "b.py",
        "signature": "def helper() -> None",
        "owner": None,
        "tags": ["cat:core", "type:function", "lang:python", "dir:b"],
    },
]

EDGES: list[dict] = [
    {
        "source": "a.py::FileNode@L10",
        "target": "a.py::BaseNode@L1",
        "relation": "INHERITS",
        "confidence": 1.0,
        "resolved_by": "inheritance",
    },
    {
        "source": "a.py::parse_file@L20",
        "target": "a.py::FileNode@L10",
        "relation": "CALLS",
        "confidence": 0.9,
        "resolved_by": "import_call",
    },
    {
        "source": "b.py::helper@L1",
        "target": "a.py::parse_file@L20",
        "relation": "CALLS",
        "confidence": 0.8,
        "resolved_by": "global",
    },
    {
        "source": "a.py::FileNode@L10",
        "target": "a.py::FileNode.children@L30",
        "relation": "CONTAINS",
        "confidence": 1.0,
        "resolved_by": "structure",
    },
    {
        "source": "a.py::parse_file@L20",
        "target": "mod::os",
        "relation": "IMPORTS",
        "confidence": 1.0,
        "resolved_by": "import_pass",
    },
]


def _assert_empty(result: SearchResult) -> None:
    assert result.matches == []
    assert result.total == 0
    assert result.tag_counts == {}
    assert result.tag_combo_counts == {}


class TestParseSearchQuery:
    def test_empty_returns_none(self):
        assert parse_search_query("") is None
        assert parse_search_query("   ") is None

    def test_free_text_only(self):
        parsed = parse_search_query("BaseNode")
        assert parsed is not None
        assert parsed.text == "basenode"
        assert parsed.predicates == ()

    def test_predicates_and_text(self):
        parsed = parse_search_query("{type:function} parse")
        assert parsed is not None
        assert parsed.text == "parse"
        assert len(parsed.predicates) == 1
        assert parsed.predicates[0][0] == "type"


class TestSearchNodes:
    def test_free_text_name_case_insensitive(self):
        result = search_nodes(NODES, "basenode")
        names = [n["name"] for n in result.matches]
        assert names[0] == "BaseNode"
        assert "FileNode" in names

    def test_free_text_name_exactish_via_signature_prefix(self):
        result = search_nodes(NODES, "class BaseNode")
        assert [n["name"] for n in result.matches] == ["BaseNode"]

    def test_free_text_signature(self):
        result = search_nodes(NODES, "async def parse_file")
        assert [n["name"] for n in result.matches] == ["parse_file"]

    def test_type_alias_matches_node_type(self):
        result = search_nodes(NODES, "{type:function}")
        assert {n["name"] for n in result.matches} == {"parse_file", "helper"}

    def test_type_alias_matches_type_field(self):
        result = search_nodes(NODES, "{type:FunctionNode}")
        assert {n["name"] for n in result.matches} == {"parse_file", "helper"}

    def test_glob_owner(self):
        result = search_nodes(NODES, "{owner:*Node}")
        assert [n["name"] for n in result.matches] == ["children"]

    def test_combined_type_and_text(self):
        result = search_nodes(NODES, "{type:function} parse")
        assert [n["name"] for n in result.matches] == ["parse_file"]

    def test_edge_only_query_returns_empty(self):
        _assert_empty(search_nodes(NODES, "{relation:CALLS}"))

    def test_empty_query_returns_empty(self):
        _assert_empty(search_nodes(NODES, ""))
        _assert_empty(search_nodes(NODES, "  "))

    def test_limit_truncates_matches_not_total(self):
        result = search_nodes(NODES, "{type:function}", limit=1)
        assert result.total == 2
        assert len(result.matches) == 1
        assert result.matches[0]["name"] == "helper"
        assert result.tag_counts["type:function"] == 2

    def test_limit_negative_returns_all(self):
        result = search_nodes(NODES, "{type:function}", limit=-1)
        assert result.total == 2
        assert len(result.matches) == 2

    def test_sorts_by_name_within_type(self):
        result = search_nodes(NODES, "{type:class}")
        assert [n["name"] for n in result.matches] == ["BaseNode", "FileNode"]

    def test_tag_counts_and_combos(self):
        result = search_nodes(NODES, "{type:function}")
        assert result.tag_counts["type:function"] == 2
        assert result.tag_counts["lang:python"] == 2
        assert result.tag_counts["dir:b"] == 1
        # Full tag-sets only; longer combos rank first (top 10).
        assert list(result.tag_combo_counts) == [
            "cat:core|dir:b|lang:python|type:function",
            "cat:core|lang:python|type:function",
        ]
        assert result.tag_combo_counts["cat:core|dir:b|lang:python|type:function"] == 1
        assert result.tag_combo_counts["cat:core|lang:python|type:function"] == 1


class TestSearchEdges:
    def test_relation_exact(self):
        result = search_edges(EDGES, "{relation:CALLS}")
        assert result.total == 2
        assert all(e["relation"] == "CALLS" for e in result.matches)

    def test_relation_glob(self):
        result = search_edges(EDGES, "{relation:IMPORT*}")
        assert result.total == 1
        assert result.matches[0]["relation"] == "IMPORTS"

    def test_free_text_calls(self):
        result = search_edges(EDGES, "CALLS")
        assert result.total == 2
        assert all(e["relation"] == "CALLS" for e in result.matches)

    def test_node_only_query_returns_empty(self):
        _assert_empty(search_edges(EDGES, "{type:function}"))

    def test_empty_query_returns_empty(self):
        _assert_empty(search_edges(EDGES, ""))
        _assert_empty(search_edges(EDGES, "  "))

    def test_sorts_by_confidence_desc(self):
        result = search_edges(EDGES, "{relation:CALLS}")
        confidences = [e["confidence"] for e in result.matches]
        assert confidences == [0.9, 0.8]

    def test_limit_truncates_matches_not_total(self):
        result = search_edges(EDGES, "{relation:CALLS}", limit=1)
        assert result.total == 2
        assert len(result.matches) == 1
        assert result.matches[0]["source"] == "a.py::parse_file@L20"
        assert result.tag_counts == {}

    def test_limit_negative_returns_all(self):
        result = search_edges(EDGES, "{relation:CALLS}", limit=-1)
        assert result.total == 2
        assert len(result.matches) == 2

    def test_endpoint_tag_stats(self):
        result = search_edges(EDGES, "{relation:CALLS}", nodes=NODES)
        assert result.tag_counts["type:function"] == 3
        assert result.tag_counts["type:class"] == 1
        # Longer helper combo ranks above the 3-tag function/class sets.
        assert list(result.tag_combo_counts)[0] == "cat:core|dir:b|lang:python|type:function"
        assert result.tag_combo_counts["cat:core|lang:python|type:function"] == 2
        assert result.tag_combo_counts["cat:core|lang:python|type:class"] == 1

    def test_source_glob_and_relation(self):
        result = search_edges(EDGES, "{source:*FileNode*} {relation:CONTAINS}")
        assert result.total == 1
        assert result.matches[0]["relation"] == "CONTAINS"

    def test_resolved_by_glob(self):
        result = search_edges(EDGES, "{resolved_by:import*}")
        assert {e["relation"] for e in result.matches} == {"CALLS", "IMPORTS"}


class TestSearchRegex:
    def test_nodes_by_name_pattern(self):
        result = search_regex(r"^parse_", target="nodes", nodes=NODES)
        assert [n["name"] for n in result.matches] == ["parse_file"]

    def test_nodes_ignore_case(self):
        result = search_regex(r"basenode", target="nodes", nodes=NODES, ignore_case=True)
        assert any(n["name"] == "BaseNode" for n in result.matches)

    def test_edges_by_relation(self):
        result = search_regex(r"^CALL", target="edges", edges=EDGES, nodes=NODES)
        assert result.total == 2
        assert all(e["relation"] == "CALLS" for e in result.matches)
        assert result.tag_counts

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            search_regex(r"[", target="nodes", nodes=NODES)

    def test_empty_pattern_returns_empty(self):
        _assert_empty(search_regex("  ", target="nodes", nodes=NODES))

    def test_bad_target_raises(self):
        with pytest.raises(ValueError, match="target must be"):
            search_regex(r"x", target="both", nodes=NODES)  # type: ignore[arg-type]
