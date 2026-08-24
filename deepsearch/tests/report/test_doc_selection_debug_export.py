"""Tests for doc_selection_debug packing, extraction and Excel export.

Covers:
- Reporter._write_doc_selection_debug: packs 7 types of intermediate data
- OutlineToExcelExporter._extract_doc_selection_debug: flattens to 2 lists (rationales + rationale_top_passages)
- End-to-end: pack -> extract -> verify data consistency
- Edge cases: empty debug, background-knowledge fallback
"""

from openjiuwen_deepsearch.algorithm.report.report import Reporter, PassageSelectionContext
from openjiuwen_deepsearch.utils.debug_utils.outline_visualization import (
    OutlineToExcelExporter,
)


# ---------- Helpers ----------

def _doc(idx, title=None, url=None):
    return {
        "doc_title": title or f"doc-{idx}",
        "doc_url": url or f"https://example.com/{idx}",
        "original_content": f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
    }


def _rationale(rid, desc, rtype="factual", priority="primary"):
    return {"id": rid, "description": desc, "type": rtype, "priority": priority}


def _coverage_result(docs, matrix):
    return {
        "filtered_passages": docs,
        "coverage_matrix": matrix,
    }


def _make_full_debug_data():
    """Build a realistic doc_selection_debug dict for extraction tests."""
    docs = [_doc(0, "出口数据"), _doc(1, "目的国分析"), _doc(2, "无关内容")]
    rationales = [
        _rationale("r1", "出口数据", "quantitative", "primary"),
        _rationale("r2", "目的国", "factual", "supplementary"),
    ]
    matrix = {
        "passage_0": {"r1": 0.9, "r2": 0.1},
        "passage_1": {"r1": 0.1, "r2": 0.9},
        "passage_2": {"r1": 0.05, "r2": 0.05},
    }
    coverage = _coverage_result(docs, matrix)
    selected_docs = [docs[0], docs[1]]
    return {
        "rationales": rationales,
        "coverage_result": coverage,
        "passages": docs,
        "selected_passages": selected_docs,
    }


# ---------- _write_doc_selection_debug ----------

class TestWriteDocSelectionDebug:
    """Test Reporter._write_doc_selection_debug packing logic."""

    def test_packs_all_7_keys(self):
        """Verify all data keys are present in the packed dict."""
        docs = [_doc(0), _doc(1)]
        rationales = [_rationale("r1", "desc1")]
        matrix = {"passage_0": {"r1": 0.8}, "passage_1": {"r1": 0.2}}
        coverage = _coverage_result(docs, matrix)
        selected = [docs[0]]

        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext(rationales, coverage, docs, selected),
        )

        debug = current_inputs["doc_selection_debug"]
        assert set(debug.keys()) == {
            "rationales", "doc_filter", "coverage_matrix",
            "dimension_scores", "passage_info_map", "selected_passages",
        }

    def test_doc_filter_counts(self):
        """Verify doc_filter before/after counts are correct."""
        all_docs = [_doc(i) for i in range(10)]
        filtered = [_doc(i) for i in range(5)]
        coverage = _coverage_result(filtered, {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext([], coverage, all_docs, []),
        )

        assert current_inputs["doc_selection_debug"]["doc_filter"]["before"] == 10
        assert current_inputs["doc_selection_debug"]["doc_filter"]["after"] == 5

    def test_selected_docs_summary(self):
        """Verify selected_docs summary contains title/url."""
        docs = [_doc(0, "Title A", "https://a.com"), _doc(1, "Title B", "https://b.com")]
        coverage = _coverage_result(docs, {})
        current_inputs = {}

        Reporter._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext([], coverage, docs, docs),
        )

        selected = current_inputs["doc_selection_debug"]["selected_passages"]
        assert len(selected) == 2
        assert selected[0]["passage_key"] == "passage_0"
        assert selected[0]["doc_title"] == "Title A"
        assert selected[0]["doc_url"] == "https://a.com"
        assert selected[1]["doc_title"] == "Title B"
        assert selected[1]["passage_key"] == "passage_1"

# ---------- _extract_doc_selection_debug ----------

class TestExtractDocSelectionDebug:
    """Test OutlineToExcelExporter._extract_doc_selection_debug extraction logic."""

    def _make_outline_data(self):
        """Create an empty outline_data dict matching extract_all_data."""
        return {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'rationale_top_passages': [],
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
                "doc_filter": {"before": 10, "after": 8},
                "coverage_matrix": {},
                "selected_passages": [],
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

    def test_extracts_rationale_top_passages(self):
        """Verify top passages are extracted per rationale, sorted by coverage score."""
        section = {
            "id": "2", "title": "TopPassages Section",
            "doc_selection_debug": {
                "rationales": [
                    {"id": "r1", "description": "出口数据", "type": "quantitative", "priority": "primary"},
                    {"id": "r2", "description": "目的国", "type": "factual", "priority": "supplementary"},
                ],
                "doc_filter": {"before": 100, "after": 80},
                "coverage_matrix": {
                    "passage_0": {"r1": 0.9, "r2": 0.1},
                    "passage_1": {"r1": 0.3, "r2": 0.7},
                    "passage_2": {"r1": 0.05, "r2": 0.05},
                },
                "passage_info_map": {
                    "passage_0": {"doc_title": "Doc A", "doc_url": "https://a.com", "passage_text": "passage A"},
                    "passage_1": {"doc_title": "Doc B", "doc_url": "https://b.com", "passage_text": "passage B"},
                    "passage_2": {"doc_title": "Doc C", "doc_url": "https://c.com", "passage_text": "passage C"},
                },
                "selected_passages": [],
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # r1 has 3 docs with score > 0, r2 has 3 docs with score > 0 → 6 rows total
        assert len(outline_data['rationale_top_passages']) == 6

        # r1 rows: passage_0 (0.9), passage_1 (0.3), passage_2 (0.05) sorted descending
        r1_rows = [r for r in outline_data['rationale_top_passages'] if r['rationale_id'] == 'r1']
        assert len(r1_rows) == 3
        assert r1_rows[0]['rank'] == 1
        assert r1_rows[0]['passage_key'] == 'passage_0'
        assert r1_rows[0]['doc_title'] == 'Doc A'
        assert r1_rows[0]['coverage'] == 0.9
        assert r1_rows[1]['rank'] == 2
        assert r1_rows[1]['passage_key'] == 'passage_1'
        assert r1_rows[2]['rank'] == 3
        assert r1_rows[2]['passage_key'] == 'passage_2'

        # r2 rows: passage_1 (0.7), passage_0 (0.1), passage_2 (0.05) sorted descending
        r2_rows = [r for r in outline_data['rationale_top_passages'] if r['rationale_id'] == 'r2']
        assert len(r2_rows) == 3
        assert r2_rows[0]['passage_key'] == 'passage_1'
        assert r2_rows[0]['coverage'] == 0.7

    def test_top_passages_capped_at_15(self):
        """Verify only top 15 passages per rationale are kept."""
        rationales = [{"id": "r1", "description": "desc", "type": "factual", "priority": "primary"}]
        coverage_matrix = {f"passage_{i}": {"r1": 0.01 * (20 - i)} for i in range(20)}
        doc_info_map = {f"passage_{i}": {"doc_title": f"Doc {i}", "doc_url": "", "passage_text": ""} for i in range(20)}
        section = {
            "id": "3", "title": "Cap Section",
            "doc_selection_debug": {
                "rationales": rationales,
                "doc_filter": {"before": 20, "after": 20},
                "coverage_matrix": coverage_matrix,
                "passage_info_map": doc_info_map,
                "selected_passages": [],
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationale_top_passages']) == 15
        # Highest score is passage_0 (0.01*20=0.2)
        assert outline_data['rationale_top_passages'][0]['passage_key'] == 'passage_0'

    def test_top_passages_skips_zero_scores(self):
        """Verify docs with zero coverage score are not included."""
        rationales = [{"id": "r1", "description": "desc", "type": "factual", "priority": "primary"}]
        coverage_matrix = {
            "passage_0": {"r1": 0.8},
            "passage_1": {"r1": 0.0},
        }
        doc_info_map = {
            "passage_0": {"doc_title": "Doc A", "doc_url": "", "passage_text": ""},
            "passage_1": {"doc_title": "Doc B", "doc_url": "", "passage_text": ""},
        }
        section = {
            "id": "4", "title": "Zero Section",
            "doc_selection_debug": {
                "rationales": rationales,
                "doc_filter": {"before": 2, "after": 2},
                "coverage_matrix": coverage_matrix,
                "passage_info_map": doc_info_map,
                "selected_passages": [],
            }
        }
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # Only passage_0 has score > 0
        assert len(outline_data['rationale_top_passages']) == 1
        assert outline_data['rationale_top_passages'][0]['passage_key'] == 'passage_0'

    def test_empty_debug_skipped(self):
        """Verify empty/falsy doc_selection_debug is skipped."""
        section = {"id": "6", "title": "Empty", "doc_selection_debug": {}}
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationales']) == 0
        assert len(outline_data['rationale_top_passages']) == 0

    def test_none_debug_skipped(self):
        """Verify None doc_selection_debug is skipped."""
        section = {"id": "7", "title": "None", "doc_selection_debug": None}
        outline_data = self._make_outline_data()
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        assert len(outline_data['rationales']) == 0


# ---------- End-to-end: pack -> extract ----------

class TestEndToEnd:
    """End-to-end: _write_doc_selection_debug packs data, _extract_doc_selection_debug extracts it."""

    def test_pack_then_extract_consistency(self):
        """Pack data with _write_doc_selection_debug, then extract and verify consistency."""
        data = _make_full_debug_data()

        # Step 1: Pack
        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext(
                data["rationales"], data["coverage_result"],
                data["passages"], data["selected_passages"],
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
            'rationales': [], 'rationale_top_passages': [],
        }
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # Verify rationales
        assert len(outline_data['rationales']) == 2
        assert outline_data['rationales'][0]['rationale_id'] == 'r1'

        # Verify rationale_top_passages
        # r1: passage_0 (0.9), passage_1 (0.1), passage_2 (0.05) → 3 rows
        # r2: passage_1 (0.9), passage_0 (0.1), passage_2 (0.05) → 3 rows
        assert len(outline_data['rationale_top_passages']) == 6
        r1_rows = [r for r in outline_data['rationale_top_passages'] if r['rationale_id'] == 'r1']
        assert len(r1_rows) == 3
        assert r1_rows[0]['passage_key'] == 'passage_0'
        assert r1_rows[0]['coverage'] == 0.9
        # Check doc_title from passage_info_map
        assert r1_rows[0]['doc_title'] == '出口数据'

    def test_pack_then_extract_with_uncovered(self):
        """End-to-end with uncovered rationales."""
        docs = [_doc(0, "Doc A")]
        rationales = [_rationale("r1", "need"), _rationale("r2", "missing")]
        matrix = {"passage_0": {"r1": 0.8, "r2": 0.1}}
        coverage = _coverage_result(docs, matrix)

        current_inputs = {}
        Reporter._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext(rationales, coverage, docs, docs),
        )

        section = {"id": "1", "title": "Test", "doc_selection_debug": current_inputs["doc_selection_debug"]}
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'rationale_top_passages': [],
        }
        OutlineToExcelExporter._extract_doc_selection_debug(section, outline_data)

        # 2 rationales, each with 1 doc having score > 0 → 2 top_passages rows
        assert len(outline_data['rationale_top_passages']) == 2
        r1_rows = [r for r in outline_data['rationale_top_passages'] if r['rationale_id'] == 'r1']
        assert len(r1_rows) == 1
        assert r1_rows[0]['coverage'] == 0.8


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


# ---------- export_outline_without_plans ----------

class TestExportOutlineWithoutPlans:
    """Verify export_outline_without_plans excludes doc_selection_debug from LLM inputs."""

    def test_excludes_doc_selection_debug_from_sections(self):
        """Verify doc_selection_debug is excluded alongside plans."""
        from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
            Outline, Section, Plan, Step,
        )
        step = Step(id="s1", type="info_collecting", title="Step", description="desc")
        plan = Plan(id="p1", language="zh-CN", title="Plan", thought="t",
                    steps=[step], is_research_completed=True)
        section = Section(
            id="1", title="Test", description="desc",
            plans=[plan],
            doc_selection_debug={"rationales": []},
        )
        outline = Outline(
            id="o1", language="zh-CN", thought="t", title="Report",
            sections=[section],
        )
        result = Reporter.export_outline_without_plans(outline)
        assert isinstance(result, Outline)
        result_section = result.sections[0]
        assert result_section.plans == []
        assert result_section.doc_selection_debug is None

    def test_excludes_doc_selection_debug_from_dict(self):
        """Verify exclusion works when outline is a dict."""
        outline_dict = {
            "id": "o1", "language": "zh-CN", "thought": "t", "title": "Report",
            "sections": [{
                "id": "1", "title": "Test", "description": "desc",
                "plans": [{"id": "p1", "language": "zh-CN", "title": "P",
                           "thought": "", "steps": [], "is_research_completed": False}],
                "doc_selection_debug": {"rationales": [{"id": "r1"}]},
            }],
        }
        result = Reporter.export_outline_without_plans(outline_dict)
        assert isinstance(result, dict)
        result_section = result["sections"][0]
        assert "plans" not in result_section or result_section["plans"] == []
        assert "doc_selection_debug" not in result_section or result_section["doc_selection_debug"] is None


# ---------- OutlineToExcelExporter integration ----------

class TestOutlineToExcelExporterIntegration:
    """Test full OutlineToExcelExporter with doc_selection_debug in outline data."""

    def test_create_dataframes_includes_new_sheets(self):
        """Verify create_dataframes produces rationales + rationale_top_passages DataFrames."""
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [
                {"section_id": "1", "section_title": "S1", "rationale_id": "r1",
                 "rationale_description": "desc", "rationale_type": "factual", "rationale_priority": "primary"},
            ],
            'rationale_top_passages': [
                {"section_id": "1", "section_title": "S1", "rationale_id": "r1",
                 "rationale_description": "desc", "rank": 1, "passage_key": "passage_0",
                 "doc_title": "Doc A", "doc_url": "https://a.com",
                 "passage_text": "passage", "coverage": 0.9,
                 "reliability": 0.8, "analysis": 0.7, "presentation": 0.6,
                 "data_density": 0.9, "total_score": 0.86},
            ],
            'fulltext_evidence': [],
            'passage_evidence': [],
        }
        dfs = OutlineToExcelExporter.create_dataframes(outline_data)
        assert 'rationales' in dfs
        assert 'rationale_top_passages' in dfs
        assert len(dfs['rationales']) == 1
        assert len(dfs['rationale_top_passages']) == 1

    def test_create_dataframes_skips_empty_sheets(self):
        """Verify empty data lists don't produce DataFrames."""
        outline_data = {
            'outlines': [], 'sections': [], 'plans': [], 'steps': [],
            'retrieval_query_docs': [], 'doc_infos': [], 'toc': [],
            'rationales': [], 'rationale_top_passages': [],
            'fulltext_evidence': [], 'passage_evidence': [],
        }
        dfs = OutlineToExcelExporter.create_dataframes(outline_data)
        assert 'rationales' not in dfs
        assert 'rationale_top_passages' not in dfs
