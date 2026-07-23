"""Tests for doc_selection_debug packing, extraction and Excel export.

Covers:
- Reporter._write_doc_selection_debug: packs 7 types of intermediate data
- OutlineToExcelExporter._extract_doc_selection_debug: flattens to 4 lists
- End-to-end: pack → extract → verify data consistency
- Edge cases: empty debug, background-knowledge fallback
"""

from openjiuwen_deepsearch.algorithm.report.report import Reporter, DocSelectionContext
from openjiuwen_deepsearch.utils.debug_utils.outline_visualization import (
    OutlineToExcelExporter,
)


# ---------- Helpers ----------

def _doc(idx, title=None, url=None):
    return {
        "title": title or f"doc-{idx}",
        "url": url or f"https://example.com/{idx}",
        "original_content": f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
    }


def _rationale(rid, desc, rtype="factual", priority="primary"):
    return {"id": rid, "description": desc, "type": rtype, "priority": priority}


def _coverage_result(docs, matrix, reliability=None, noise=None):
    return {
        "filtered_docs": docs,
        "coverage_matrix": matrix,
        "reliability_scores": reliability or {},
        "noise_scores": noise or {},
    }


def _make_full_debug_data():
    """Build a realistic doc_selection_debug dict for extraction tests."""
    docs = [_doc(0, "出口数据"), _doc(1, "目的国分析"), _doc(2, "无关内容")]
    rationales = [
        _rationale("r1", "出口数据", "quantitative", "primary"),
        _rationale("r2", "目的国", "factual", "supplementary"),
    ]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.9},
        "doc_2": {"r1": 0.05, "r2": 0.05},
    }
    coverage = _coverage_result(
        docs, matrix,
        reliability={"doc_0": 0.8, "doc_1": 0.7, "doc_2": 0.3},
        noise={"doc_0": 0.0, "doc_1": 0.1, "doc_2": 0.8},
    )
    selected_docs = [docs[0], docs[1]]
    selected_marginal_values = [0.85, 0.72]
    verify_result = {
        "uncovered_rationales": [],
        "weak_rationales": [{"id": "r2", "description": "目的国"}],
        "coverage_rate": 0.5,
        "limitations": [],
    }
    return {
        "rationales": rationales,
        "coverage_result": coverage,
        "doc_infos": docs,
        "selected_docs": selected_docs,
        "selected_marginal_values": selected_marginal_values,
        "verify_result": verify_result,
    }


# ---------- _write_doc_selection_debug ----------

class TestWriteDocSelectionDebug:
    """Test Reporter._write_doc_selection_debug packing logic."""

    def test_packs_all_7_keys(self):
        """Verify all 7 data keys are present in the packed dict."""
        docs = [_doc(0), _doc(1)]
        rationales = [_rationale("r1", "desc1")]
        matrix = {"doc_0": {"r1": 0.8}, "doc_1": {"r1": 0.2}}
        coverage = _coverage_result(docs, matrix, reliability={"doc_0": 0.9}, noise={"doc_0": 0.1})
        selected = [docs[0]]
        marginals = [0.8]
        verify = {"uncovered_rationales": [], "weak_rationales": [], "coverage_rate": 1.0, "limitations": []}

        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext(rationales, coverage, docs, selected, marginals, verify),
        )

        debug = current_inputs["doc_selection_debug"]
        assert set(debug.keys()) == {
            "rationales", "ngram_filter", "coverage_matrix",
            "reliability_scores", "noise_scores", "doc_info_map",
            "selected_docs", "verify_result",
        }

    def test_ngram_filter_counts(self):
        """Verify ngram_filter before/after counts are correct."""
        all_docs = [_doc(i) for i in range(10)]
        filtered = [_doc(i) for i in range(5)]
        coverage = _coverage_result(filtered, {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext([], coverage, all_docs, [], [], {}),
        )

        assert current_inputs["doc_selection_debug"]["ngram_filter"]["before"] == 10
        assert current_inputs["doc_selection_debug"]["ngram_filter"]["after"] == 5

    def test_selected_docs_summary(self):
        """Verify selected_docs summary contains title/url/marginal_value."""
        docs = [_doc(0, "Title A", "https://a.com"), _doc(1, "Title B", "https://b.com")]
        marginals = [0.9, 0.7]
        coverage = _coverage_result(docs, {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext([], coverage, docs, docs, marginals, {}),
        )

        selected = current_inputs["doc_selection_debug"]["selected_docs"]
        assert len(selected) == 2
        assert selected[0]["doc_key"] == "doc_0"
        assert selected[0]["title"] == "Title A"
        assert selected[0]["url"] == "https://a.com"
        assert selected[0]["marginal_value"] == 0.9
        assert selected[1]["title"] == "Title B"
        assert selected[1]["doc_key"] == "doc_1"

    def test_verify_result_preserved(self):
        """Verify verify_result is passed through correctly."""
        verify = {
            "uncovered_rationales": [{"id": "r1", "description": "missing"}],
            "weak_rationales": [],
            "coverage_rate": 0.0,
            "limitations": ["missing data"],
        }
        coverage = _coverage_result([], {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext([], coverage, [], [], [], verify),
        )

        result = current_inputs["doc_selection_debug"]["verify_result"]
        assert len(result["uncovered_rationales"]) == 1
        assert result["coverage_rate"] == 0.0

    def test_empty_verify_result_defaults_to_empty_dict(self):
        """Verify None verify_result becomes empty dict."""
        coverage = _coverage_result([], {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext([], coverage, [], [], [], None),
        )

        assert current_inputs["doc_selection_debug"]["verify_result"] == {}


# ---------- _extract_doc_selection_debug ----------

class TestExtractDocSelectionDebug:
    """Test OutlineToExcelExporter._extract_doc_selection_debug extraction logic."""

    def _make_outline_data(self):
        """Create an empty outline_data dict matching extract_all_data."""
        return {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'coverage_matrix': [],
            'doc_selection': [], 'coverage_verify': [],
        }

    def test_extracts_rationales_rows(self):
        """Verify rationales are extracted to the rationales list."""
        section = {
            "id": "1", "title": "Test Section",
            "doc_selection_debug": {
                "rationales": [
                    {"id": "r1", "description": "出口数据", "type": "quantitative", "priority": "primary"},
                    {"id": "r2", "description": "目的国", "type": "factual", "priority": "supplementary"},
                ],
                "ngram_filter": {"before": 10, "after": 8},
                "coverage_matrix": {}, "reliability_scores": {}, "noise_scores": {},
                "selected_docs": [], "verify_result": {},
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationales']) == 2
        row = outline_data['rationales'][0]
        assert row['section_id'] == '1'
        assert row['section_title'] == 'Test Section'
        assert row['rationale_id'] == 'r1'
        assert row['rationale_description'] == '出口数据'
        assert row['rationale_type'] == 'quantitative'
        assert row['rationale_priority'] == 'primary'

    def test_extracts_coverage_matrix_long_format(self):
        """Verify coverage_matrix is extracted as doc×rationale long format."""
        section = {
            "id": "2", "title": "Cov Section",
            "doc_selection_debug": {
                "rationales": [],
                "ngram_filter": {"before": 100, "after": 80},
                "coverage_matrix": {
                    "doc_0": {"r1": 0.9, "r2": 0.1},
                    "doc_1": {"r1": 0.3, "r2": 0.7},
                },
                "reliability_scores": {"doc_0": 0.8, "doc_1": 0.6},
                "noise_scores": {"doc_0": 0.0, "doc_1": 0.2},
                "doc_info_map": {
                    "doc_0": {"title": "Doc A", "url": "https://a.com"},
                    "doc_1": {"title": "Doc B", "url": "https://b.com"},
                },
                "selected_docs": [], "verify_result": {},
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # 2 docs × 2 rationales = 4 rows
        assert len(outline_data['coverage_matrix']) == 4
        row0 = outline_data['coverage_matrix'][0]
        assert row0['section_id'] == '2'
        assert row0['ngram_before'] == 100
        assert row0['ngram_after'] == 80
        assert row0['doc_key'] == 'doc_0'
        assert row0['doc_title'] == 'Doc A'
        assert row0['doc_url'] == 'https://a.com'
        # rationale_ids are sorted, so r1 comes before r2
        assert row0['rationale_id'] == 'r1'
        assert row0['coverage'] == 0.9
        assert row0['reliability'] == 0.8
        assert row0['noise'] == 0.0

    def test_extracts_doc_selection_rows(self):
        """Verify selected_docs are extracted with rank."""
        section = {
            "id": "3", "title": "Sel Section",
            "doc_selection_debug": {
                "rationales": [], "ngram_filter": {"before": 0, "after": 0},
                "coverage_matrix": {}, "reliability_scores": {}, "noise_scores": {},
                "selected_docs": [
                    {"doc_key": "doc_0", "title": "Doc A", "url": "https://a.com", "marginal_value": 0.9},
                    {"doc_key": "doc_1", "title": "Doc B", "url": "https://b.com", "marginal_value": 0.7},
                ],
                "verify_result": {},
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['doc_selection']) == 2
        assert outline_data['doc_selection'][0]['rank'] == 1
        assert outline_data['doc_selection'][0]['doc_key'] == 'doc_0'
        assert outline_data['doc_selection'][0]['doc_title'] == 'Doc A'
        assert outline_data['doc_selection'][1]['rank'] == 2
        assert outline_data['doc_selection'][1]['doc_title'] == 'Doc B'

    def test_extracts_coverage_verify_uncovered_and_weak(self):
        """Verify uncovered and weak rationales are extracted to coverage_verify."""
        section = {
            "id": "4", "title": "Verify Section",
            "doc_selection_debug": {
                "rationales": [], "ngram_filter": {"before": 0, "after": 0},
                "coverage_matrix": {}, "reliability_scores": {}, "noise_scores": {},
                "selected_docs": [],
                "verify_result": {
                    "uncovered_rationales": [{"id": "r1", "description": "missing data"}],
                    "weak_rationales": [{"id": "r2", "description": "partial data"}],
                    "coverage_rate": 0.0,
                    "limitations": [],
                },
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['coverage_verify']) == 2
        uncovered_row = outline_data['coverage_verify'][0]
        assert 'uncovered' in uncovered_row['status']
        assert uncovered_row['rationale_id'] == 'r1'
        weak_row = outline_data['coverage_verify'][1]
        assert 'weak' in weak_row['status']
        assert weak_row['rationale_id'] == 'r2'

    def test_coverage_verify_all_covered_summary(self):
        """Verify a summary row is added when all rationales are covered."""
        section = {
            "id": "5", "title": "All Covered",
            "doc_selection_debug": {
                "rationales": [], "ngram_filter": {"before": 0, "after": 0},
                "coverage_matrix": {}, "reliability_scores": {}, "noise_scores": {},
                "selected_docs": [],
                "verify_result": {
                    "uncovered_rationales": [],
                    "weak_rationales": [],
                    "coverage_rate": 1.0,
                    "limitations": [],
                },
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['coverage_verify']) == 1
        assert 'covered' in outline_data['coverage_verify'][0]['status']

    def test_empty_debug_skipped(self):
        """Verify empty/falsy doc_selection_debug is skipped."""
        section = {"id": "6", "title": "Empty", "doc_selection_debug": {}}
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationales']) == 0
        assert len(outline_data['coverage_matrix']) == 0
        assert len(outline_data['doc_selection']) == 0
        assert len(outline_data['coverage_verify']) == 0

    def test_none_debug_skipped(self):
        """Verify None doc_selection_debug is skipped."""
        section = {"id": "7", "title": "None", "doc_selection_debug": None}
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationales']) == 0


# ---------- End-to-end: pack → extract ----------

class TestEndToEnd:
    """End-to-end: _write_doc_selection_debug packs data, _extract_doc_selection_debug extracts it."""

    def test_pack_then_extract_consistency(self):
        """Pack data with _write_doc_selection_debug, then extract and verify consistency."""
        data = _make_full_debug_data()

        # Step 1: Pack
        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext(
                data["rationales"], data["coverage_result"],
                data["doc_infos"], data["selected_docs"],
                data["selected_marginal_values"], data["verify_result"],
            ),
        )
        debug = current_inputs["doc_selection_debug"]

        # Step 2: Simulate section after _update_state
        section = {
            "id": "1",
            "title": "Test Section",
            "doc_selection_debug": debug,
        }

        # Step 3: Extract
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'coverage_matrix': [],
            'doc_selection': [], 'coverage_verify': [],
        }
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # Verify rationales
        assert len(outline_data['rationales']) == 2
        assert outline_data['rationales'][0]['rationale_id'] == 'r1'

        # Verify coverage_matrix (3 docs × 2 rationales = 6 rows)
        assert len(outline_data['coverage_matrix']) == 6
        # Check ngram counts
        assert outline_data['coverage_matrix'][0]['ngram_before'] == 3
        assert outline_data['coverage_matrix'][0]['ngram_after'] == 3
        # Check doc_title/doc_url from doc_info_map
        assert outline_data['coverage_matrix'][0]['doc_title'] == '出口数据'

        # Verify doc_selection (2 selected docs)
        assert len(outline_data['doc_selection']) == 2
        assert outline_data['doc_selection'][0]['doc_key'] == 'doc_0'
        assert outline_data['doc_selection'][0]['doc_title'] == '出口数据'
        assert outline_data['doc_selection'][0]['marginal_value'] == 0.85

        # Verify coverage_verify (1 weak, 0 uncovered → 1 row for weak)
        assert len(outline_data['coverage_verify']) == 1
        assert 'weak' in outline_data['coverage_verify'][0]['status']

    def test_pack_then_extract_with_uncovered(self):
        """End-to-end with uncovered rationales."""
        docs = [_doc(0, "Doc A")]
        rationales = [_rationale("r1", "need"), _rationale("r2", "missing")]
        matrix = {"doc_0": {"r1": 0.8, "r2": 0.1}}
        coverage = _coverage_result(docs, matrix)
        verify = {
            "uncovered_rationales": [{"id": "r2", "description": "missing"}],
            "weak_rationales": [],
            "coverage_rate": 0.5,
            "limitations": ["missing data for r2"],
        }

        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            DocSelectionContext(rationales, coverage, docs, docs, [0.8], verify),
        )

        section = {"id": "1", "title": "Test", "doc_selection_debug": current_inputs["doc_selection_debug"]}
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'coverage_matrix': [],
            'doc_selection': [], 'coverage_verify': [],
        }
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # 2 rationales × 1 doc = 2 coverage_matrix rows
        assert len(outline_data['coverage_matrix']) == 2
        # 1 uncovered → 1 verify row
        assert len(outline_data['coverage_verify']) == 1
        assert 'uncovered' in outline_data['coverage_verify'][0]['status']
        assert outline_data['coverage_verify'][0]['rationale_id'] == 'r2'


# ---------- Section model field ----------

class TestSectionModelField:
    """Verify Section model has doc_selection_debug field with correct default."""

    def test_section_has_doc_selection_debug_field(self):
        from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Section
        section = Section(id="1", title="Test", description="test desc")
        assert hasattr(section, "doc_selection_debug")
        assert section.doc_selection_debug is None  # default is None

    def test_section_context_has_doc_selection_debug_field(self):
        from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.section_context import (
            SectionContext,
        )
        ctx = SectionContext()
        assert hasattr(ctx, "doc_selection_debug")
        assert ctx.doc_selection_debug == {}  # default is empty dict


# ---------- OutlineToExcelExporter integration ----------

class TestOutlineToExcelExporterIntegration:
    """Test full OutlineToExcelExporter with doc_selection_debug in outline data."""

    def test_create_dataframes_includes_new_sheets(self):
        """Verify create_dataframes produces 4 new DataFrames when data exists."""
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [
                {"section_id": "1", "section_title": "S1", "rationale_id": "r1",
                 "rationale_description": "desc", "rationale_type": "factual", "rationale_priority": "primary"},
            ],
            'coverage_matrix': [
                {"section_id": "1", "section_title": "S1", "ngram_before": 10, "ngram_after": 8,
                 "doc_key": "doc_0", "doc_title": "Doc A", "doc_url": "https://a.com",
                 "rationale_id": "r1", "coverage": 0.9, "reliability": 0.8, "noise": 0.0},
            ],
            'doc_selection': [
                {"section_id": "1", "section_title": "S1", "rank": 1,
                 "doc_key": "doc_0", "doc_title": "Doc A", "doc_url": "https://a.com", "marginal_value": 0.9},
            ],
            'coverage_verify': [
                {"section_id": "1", "section_title": "S1", "rationale_id": "r1",
                 "rationale_description": "desc", "status": "✓ covered", "coverage_rate": 1.0},
            ],
        }
        dfs = OutlineToExcelExporter.create_dataframes(outline_data)
        assert 'rationales' in dfs
        assert 'coverage_matrix' in dfs
        assert 'doc_selection' in dfs
        assert 'coverage_verify' in dfs
        assert len(dfs['rationales']) == 1
        assert len(dfs['coverage_matrix']) == 1
        assert len(dfs['doc_selection']) == 1
        assert len(dfs['coverage_verify']) == 1

    def test_create_dataframes_skips_empty_sheets(self):
        """Verify empty data lists don't produce DataFrames."""
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'coverage_matrix': [],
            'doc_selection': [], 'coverage_verify': [],
        }
        dfs = OutlineToExcelExporter.create_dataframes(outline_data)
        assert 'rationales' not in dfs
        assert 'coverage_matrix' not in dfs
        assert 'doc_selection' not in dfs
        assert 'coverage_verify' not in dfs
