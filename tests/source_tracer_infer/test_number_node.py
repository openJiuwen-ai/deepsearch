import pytest
from unittest.mock import patch
from openjiuwen_deepsearch.algorithm.source_tracer_infer.number_node import NumberNode
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import NumberNodeParam


class TestNumberNode:
    """Test cases for NumberNode core functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.number_node = NumberNode()

    # Test static methods
    def test_wr_ratio_identical_strings(self):
        """Test _wr_ratio with identical strings."""
        result = NumberNode._wr_ratio("test", "test")
        assert result == 100.0

    def test_wr_ratio_different_strings(self):
        """Test _wr_ratio with different strings."""
        result = NumberNode._wr_ratio("test", "different")
        assert 0 <= result <= 100

    def test_wr_ratio_empty_strings(self):
        """Test _wr_ratio with empty strings."""
        result = NumberNode._wr_ratio("", "")
        assert result == 100.0

    def test_partial_ratio_exact_match(self):
        """Test _partial_ratio with exact match."""
        result = NumberNode._partial_ratio("test", "this is a test string")
        assert result == 100.0

    def test_partial_ratio_partial_match(self):
        """Test _partial_ratio with partial match."""
        result = NumberNode._partial_ratio("test", "this is a tes string")
        assert 0 <= result <= 100

    def test_partial_ratio_empty_strings(self):
        """Test _partial_ratio with empty strings."""
        result = NumberNode._partial_ratio("", "test")
        assert result == 0.0

    def test_token_set_ratio_identical_strings(self):
        """Test _token_set_ratio with identical strings."""
        result = NumberNode._token_set_ratio("test string", "test string")
        assert result == 100.0

    def test_token_set_ratio_different_strings(self):
        """Test _token_set_ratio with different strings."""
        result = NumberNode._token_set_ratio("test string", "different text")
        assert 0 <= result <= 100

    def test_token_set_ratio_empty_strings(self):
        """Test _token_set_ratio with empty strings."""
        result = NumberNode._token_set_ratio("", "test")
        assert result == 0.0

    def test_extract_best_match_single_choice(self):
        """Test _extract_best_match with single choice."""
        choices = ["test string", "different string"]
        result = NumberNode._extract_best_match("test", choices, limit=1)
        assert len(result) == 1
        assert isinstance(result[0], tuple)
        assert len(result[0]) == 2
        assert result[0][0] in choices
        assert 0 <= result[0][1] <= 100

    def test_extract_best_match_multiple_choices(self):
        """Test _extract_best_match with multiple choices."""
        choices = ["test string", "different string", "another test"]
        result = NumberNode._extract_best_match("test", choices, limit=2)
        assert len(result) == 2
        assert all(isinstance(item, tuple) for item in result)

    def test_extract_best_match_empty_choices(self):
        """Test _extract_best_match with empty choices."""
        result = NumberNode._extract_best_match("test", [], limit=1)
        assert result == []

    # Test instance methods
    def test_replace_index_with_url(self):
        """Test replace_index_with_url method."""
        search_records = [
            {"title": "Test Title", "url": "https://example.com"},
            {"title": "Another Title", "url": "https://test.com"},
        ]
        title, url = self.number_node.replace_index_with_url(0, search_records)
        assert title == "Test Title"
        assert url == "https://example.com"

    def test_number_citation_node_new_node(self):
        """Test number_citation_node with new node."""
        number_node_param = NumberNodeParam()

        number_node_param, node_id = self.number_node.number_citation_node(
            1,
            number_node_param,
            "Test Title",
            "https://example.com",
        )

        new_citation_ids = number_node_param.citation_ids
        new_node_map = number_node_param.node_map
        assert node_id == 0
        assert 0 in new_citation_ids
        assert new_node_map[0]["label"] == "《Test Title》"
        assert new_node_map[0]["url"] == "https://example.com"

    def test_number_citation_node_existing_node(self):
        """Test number_citation_node with existing node."""
        node_set = set()
        node_map = {0: {"label": "《Test Title》", "url": "https://example.com"}}
        node_index = 1
        citation_ids = {0}
        number_node_param = NumberNodeParam(node_set=node_set, node_map=node_map, 
                                            node_index=node_index, citation_ids=citation_ids)

        number_node_param, node_id = self.number_node.number_citation_node(
            1,
            number_node_param,
            "Test Title",
            "https://example.com",
        )

        new_node_index = number_node_param.node_index
        assert node_id == 0
        assert new_node_index == 1  # Should not increment

    def test_number_conclusion_node_new_node(self):
        """Test number_conclusion_node with new node."""
        conclusion = "test conclusion"
        number_node_param = NumberNodeParam()

        number_node_param, node_id = self.number_node.number_conclusion_node(
            "test node", number_node_param, conclusion
        )

        assert node_id == 0
        assert number_node_param.node_map[0]["label"] == "test node"

    def test_number_conclusion_node_existing_node(self):
        """Test number_conclusion_node with existing node."""
        node_set = {"test node"}
        node_map = {0: {"label": "test node"}}
        node_index = 1
        conclusion_ids = set()
        conclusion = "test conclusion"
        number_node_param = NumberNodeParam(node_set=node_set, node_map=node_map, 
                                            node_index=node_index, conclusion_ids=conclusion_ids)
        
        number_node_param, node_id = self.number_node.number_conclusion_node(
            "test node", number_node_param, conclusion
        )

        assert node_id == 0
        assert number_node_param.node_index == 1  # Should not increment

    def test_number_conclusion_node_conclusion_match(self):
        """Test number_conclusion_node with conclusion match."""
        node_set = set()
        node_map = {}
        node_index = 0
        conclusion_ids = set()
        conclusion = "test conclusion"
        number_node_param = NumberNodeParam(node_set=node_set, node_map=node_map, 
                                            node_index=node_index, citation_ids=set(), 
                                            conclusion_ids=conclusion_ids)
        
        number_node_param, node_id = self.number_node.number_conclusion_node(
            conclusion, number_node_param, conclusion
        )

        assert node_id in number_node_param.conclusion_ids

    def test_number_node_basic(self):
        """Test number_node method with basic input."""
        structured_inference = [["test head", "relation", "test tail"]]
        conclusion = "test conclusion"
        search_records = []

        result = self.number_node.number_node(
            structured_inference, conclusion, search_records
        )

        assert len(result.structured_inference) == 1
        assert len(result.node_map) == 2  # head and tail nodes
        assert isinstance(result.citation_ids, list)
        assert isinstance(result.conclusion_ids, list)

    def test_number_node_with_citation(self):
        """Test number_node method with citation."""
        structured_inference = [[0, "relation", "test tail"]]
        conclusion = "test conclusion"
        search_records = [{"title": "Test Title", "url": "https://example.com"}]

        result = self.number_node.number_node(
            structured_inference, conclusion, search_records
        )

        assert len(result.structured_inference) == 1
        assert len(result.node_map) == 2  # citation and tail nodes
        assert len(result.citation_ids) == 1
        assert result.citation_ids[0] == 0

    def test_number_node_with_programmer_node(self):
        """Test number_node method with programmer node."""
        structured_inference = [[0, "relation", "test tail"]]
        conclusion = "test conclusion"
        search_records = [{"title": "ProgrammerNode", "url": ""}]

        result = self.number_node.number_node(
            structured_inference, conclusion, search_records
        )

        assert len(result.structured_inference) == 1
        assert len(result.node_map) == 2  # programmer and tail nodes
        assert len(result.citation_ids) == 1
        assert result.node_map[0]["is_program_info"] is True

    def test_number_node_multiple_heads(self):
        """Test number_node method with multiple heads."""
        structured_inference = [[["head1", "head2"], "relation", "tail"]]
        conclusion = "test conclusion"
        search_records = []

        result = self.number_node.number_node(
            structured_inference, conclusion, search_records
        )

        assert len(result.structured_inference) == 1
        assert len(result.node_map) == 3  # head1, head2, and tail nodes
        assert len(result.structured_inference[0][0]) == 2  # Two head IDs

    def test_number_node_error_handling(self):
        """Test number_node error handling."""
        structured_inference = [["test head", "relation", "test tail"]]
        conclusion = "test conclusion"
        search_records = []

        # Mock an error in one of the helper methods
        with patch.object(
            self.number_node,
            "number_conclusion_node",
            side_effect=Exception("Test error"),
        ):
            with pytest.raises(ValueError) as exc_info:
                self.number_node.number_node(
                    structured_inference, conclusion, search_records
                )

            assert "ERROR in NUMBER_NODE" in str(exc_info.value)

    def test_number_node_empty_structured_inference(self):
        """Test number_node with empty structured_inference."""
        structured_inference = []
        conclusion = "test conclusion"
        search_records = []

        result = self.number_node.number_node(
            structured_inference, conclusion, search_records
        )

        assert len(result.structured_inference) == 0
        assert len(result.node_map) == 0
        assert len(result.citation_ids) == 0
        assert len(result.conclusion_ids) == 0
