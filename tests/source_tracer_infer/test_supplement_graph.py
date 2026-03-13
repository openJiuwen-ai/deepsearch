import pytest
import asyncio
from unittest.mock import patch, AsyncMock
import networkx as nx

from openjiuwen_deepsearch.algorithm.source_tracer_infer.supplement_graph import (
    SupplementGraph,
)
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import GraphInfo


class TestSupplementGraph:
    """Test cases for SupplementGraph core functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.supplement_graph = SupplementGraph("test_model")

    def test_init(self):
        """Test SupplementGraph initialization."""
        assert self.supplement_graph.model_name == "test_model"

    def test_generate_graph_basic(self):
        """Test generate_graph with basic input."""
        structured_inference = [([0], "relation", 1), ([1], "relation2", 2)]

        graph, new_structured_inference = self.supplement_graph.generate_graph(
            structured_inference
        )

        assert isinstance(graph, nx.DiGraph)
        assert len(graph.edges()) == 2
        assert len(new_structured_inference) == 2
        assert graph.has_edge(0, 1)
        assert graph.has_edge(1, 2)

    def test_generate_graph_remove_self_loop(self):
        """Test generate_graph removes self loops."""
        structured_inference = [
            ([0, 1], "relation", 1)  # 1 is in head_ids, creating self loop
        ]

        graph, new_structured_inference = self.supplement_graph.generate_graph(
            structured_inference
        )

        assert len(graph.edges()) == 1
        assert graph.has_edge(0, 1)
        assert not graph.has_edge(1, 1)  # Self loop should be removed
        assert new_structured_inference[0][0] == [
            0
        ]  # 1 should be removed from head_ids

    def test_generate_graph_remove_all_self_loop(self):
        """Test generate_graph removes tuple when all heads are self loops."""
        structured_inference = [
            ([1], "relation", 1)  # Only self loop
        ]

        graph, new_structured_inference = self.supplement_graph.generate_graph(
            structured_inference
        )

        assert len(graph.edges()) == 0
        assert len(new_structured_inference) == 0  # Should be removed entirely

    def test_filter_conclusion_node_valid(self):
        """Test filter_conclusion_node with valid conclusion nodes."""
        graph = nx.DiGraph()
        graph.add_node(0)
        graph.add_node(1)
        graph.add_edge(0, 1)

        conclusion_ids = [1]  # Node 1 has no outgoing edges

        result = self.supplement_graph.filter_conclusion_node(graph, conclusion_ids)

        assert result == [1]

    def test_filter_conclusion_node_invalid(self):
        """Test filter_conclusion_node with invalid conclusion nodes."""
        graph = nx.DiGraph()
        graph.add_node(0)
        graph.add_node(1)
        graph.add_edge(0, 1)
        graph.add_edge(1, 2)  # Node 1 has outgoing edge

        conclusion_ids = [1]

        result = self.supplement_graph.filter_conclusion_node(graph, conclusion_ids)

        assert result == []

    def test_filter_conclusion_node_mixed(self):
        """Test filter_conclusion_node with mixed conclusion nodes."""
        graph = nx.DiGraph()
        graph.add_node(0)
        graph.add_node(1)
        graph.add_node(2)
        graph.add_edge(0, 1)
        graph.add_edge(1, 2)  # Node 1 has outgoing edge

        conclusion_ids = [1, 2]  # Node 2 has no outgoing edges

        result = self.supplement_graph.filter_conclusion_node(graph, conclusion_ids)

        assert result == [2]

    def test_remove_no_indegree_conclusion_node_basic(self):
        """Test remove_no_indegree_conclusion_node with basic case."""
        structured_inference = [([0], "relation", 1), ([1], "relation2", 2)]
        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}, 2: {"label": "node2"}}
        citation_ids = [0]
        conclusion_ids = [2]

        result = self.supplement_graph.remove_no_indegree_conclusion_node(
            structured_inference, node_map, citation_ids, conclusion_ids
        )

        new_structured_inference, new_node_map, new_conclusion_ids = result
        assert len(new_structured_inference) == 2
        assert len(new_node_map) == 3
        assert new_conclusion_ids == [2]

    def test_remove_no_indegree_conclusion_node_remove_no_indegree(self):
        """Test remove_no_indegree_conclusion_node removes nodes with no indegree."""
        structured_inference = [
            ([1], "relation", 2)  # Node 1 has no indegree and is not citation
        ]
        node_map = {1: {"label": "node1"}, 2: {"label": "node2"}}
        citation_ids = [0]  # Node 1 is not in citation_ids
        conclusion_ids = [2]

        result = self.supplement_graph.remove_no_indegree_conclusion_node(
            structured_inference, node_map, citation_ids, conclusion_ids
        )

        new_structured_inference, new_node_map, new_conclusion_ids = result
        assert len(new_structured_inference) == 0  # Should be removed
        assert 1 not in new_node_map  # Node 1 should be removed

    @pytest.mark.asyncio
    async def test_supplement_graph_basic(self):
        """Test supplement_graph with basic disconnected graph."""
        graph = nx.DiGraph()
        graph.add_node(0)
        graph.add_node(1)
        # Two disconnected components

        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}}
        conclusion_ids = [1]
        citation_ids = []

        with patch(
            "openjiuwen_deepsearch.algorithm.source_tracer_infer.supplement_graph.call_model",
            new_callable=AsyncMock,
        ) as mock_call_model:
            mock_call_model.return_value = [([0], "relation", 1)]

            result = await self.supplement_graph.supplement_graph(
                graph, node_map, conclusion_ids, citation_ids
            )

            assert len(result) == 1
            assert result[0] == ([0], "relation", 1)

    @pytest.mark.asyncio
    async def test_supplement_graph_filter_same_component(self):
        """Test supplement_graph filters tuples from same component."""
        graph = nx.DiGraph()
        graph.add_edge(0, 1)
        graph.add_edge(2, 3)

        node_map = {
            0: {"label": "node0"},
            1: {"label": "node1"},
            2: {"label": "node2"},
            3: {"label": "node3"},
        }
        conclusion_ids = [1]
        citation_ids = []

        with patch(
            "openjiuwen_deepsearch.algorithm.source_tracer_infer.supplement_graph.call_model",
            new_callable=AsyncMock,
        ) as mock_call_model:
            mock_call_model.return_value = [([0], "relation", 1), ([2], "relation", 3)]

            result = await self.supplement_graph.supplement_graph(
                graph, node_map, conclusion_ids, citation_ids
            )

            # Should filter out tuples from same component
            assert len(result) == 0

    @pytest.mark.asyncio
    async def test_supplement_graph_empty_result(self):
        """Test supplement_graph with empty LLM result."""
        graph = nx.DiGraph()
        graph.add_node(0)
        graph.add_node(1)

        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}}
        conclusion_ids = [1]
        citation_ids = []

        with patch(
            "openjiuwen_deepsearch.algorithm.source_tracer_infer.supplement_graph.call_model",
            new_callable=AsyncMock,
        ) as mock_call_model:
            mock_call_model.return_value = []

            result = await self.supplement_graph.supplement_graph(
                graph, node_map, conclusion_ids, citation_ids
            )

            assert result == []

    def test_remove_disconnected_subgraph_basic(self):
        """Test remove_disconnected_subgraph with basic case."""
        graph = nx.DiGraph()
        graph.add_edge(0, 1)
        graph.add_edge(2, 3)  # Separate component

        conclusion_ids = [1]  # Only in first component

        result = self.supplement_graph.remove_disconnected_subgraph(
            graph, conclusion_ids
        )

        assert set(result) == {2, 3}  # Should remove nodes from second component

    def test_remove_disconnected_subgraph_all_components(self):
        """Test remove_disconnected_subgraph when all components have conclusion."""
        graph = nx.DiGraph()
        graph.add_edge(0, 1)
        graph.add_edge(2, 3)

        conclusion_ids = [1, 3]  # Both components have conclusions

        result = self.supplement_graph.remove_disconnected_subgraph(
            graph, conclusion_ids
        )

        assert result == []  # Nothing should be removed

    def test_remove_disconnected_subgraph_empty_graph(self):
        """Test remove_disconnected_subgraph with empty graph."""
        graph = nx.DiGraph()
        conclusion_ids = []

        result = self.supplement_graph.remove_disconnected_subgraph(
            graph, conclusion_ids
        )

        assert result == []

    def test_update_graph_info_with_removeNodes_basic(self):
        """Test update_graph_info_with_remove_nodes with basic case."""
        structured_inference = [
            ([0], "relation", 3),
            ([1], "relation2", 2),
        ]
        node_map = {
            0: {"label": "node0"},
            1: {"label": "node1"},
            2: {"label": "node2"},
            3: {"label": "node3"},
        }
        remove_nodes = [1, 2]

        result = self.supplement_graph.update_graph_info_with_remove_nodes(
            structured_inference, node_map, remove_nodes
        )

        new_structured_inference, new_node_map = result
        assert len(new_structured_inference) == 1  # Only first tuple should remain
        assert new_structured_inference[0] == ([0], "relation", 3)
        assert len(new_node_map) == 2  # Only nodes 0 and 3 should remain
        assert 1 not in new_node_map
        assert 2 not in new_node_map

    def test_update_graph_info_with_remove_nodes_empty(self):
        """Test update_graph_info_with_removeNodes with empty remove_nodes."""
        structured_inference = [([0], "relation", 1), ([1], "relation2", 2)]
        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}, 2: {"label": "node2"}}
        remove_nodes = []

        result = self.supplement_graph.update_graph_info_with_remove_nodes(
            structured_inference, node_map, remove_nodes
        )

        new_structured_inference, new_node_map = result
        assert len(new_structured_inference) == 2
        assert len(new_node_map) == 3

    def test_cut_branch_basic(self):
        """Test cut_branch with basic case."""
        new_structured_inference = [([0], "relation", 1), ([1], "relation2", 2)]
        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}, 2: {"label": "node2"}}
        citation_ids = [0]
        conclusion_ids = [2]

        result = self.supplement_graph.cut_branch(
            new_structured_inference, node_map, citation_ids, conclusion_ids
        )

        assert len(result) == 4
        new_structured_inference, new_node_map, new_citation_ids, new_conclusion_ids = (
            result
        )
        assert len(new_structured_inference) == 2  # All nodes should be saved
        assert len(new_node_map) == 3
        assert new_citation_ids == [0]
        assert new_conclusion_ids == [2]

    def test_cut_branch_remove_redundant(self):
        """Test cut_branch removes redundant branches."""
        new_structured_inference = [
            ([0], "relation", 1),
            ([2], "relation2", 3),  # Branch not connected to conclusion
        ]
        node_map = {
            0: {"label": "node0"},
            1: {"label": "node1"},
            2: {"label": "node2"},
            3: {"label": "node3"},
        }
        citation_ids = [0, 2]
        conclusion_ids = [1]

        result = self.supplement_graph.cut_branch(
            new_structured_inference, node_map, citation_ids, conclusion_ids
        )

        new_structured_inference, new_node_map, new_citation_ids, new_conclusion_ids = (
            result
        )
        assert len(new_structured_inference) == 1  # Only first tuple should remain
        assert new_structured_inference[0] == [[0], "relation", 1]
        assert len(new_node_map) == 2  # Only nodes 0 and 1 should remain
        assert new_citation_ids == [0]

    def test_del_redundant_node_basic(self):
        """Test _del_redundant_node with basic case."""
        structured_inference = [([0], "relation", 1), ([1], "relation2", 2)]
        node_map = {0: {"label": "node0"}, 1: {"label": "node1"}, 2: {"label": "node2"}}
        citation_ids = [0]
        save_node_set = {0, 1, 2}

        result = self.supplement_graph._del_redundant_node(
            structured_inference, node_map, citation_ids, save_node_set
        )

        new_structured_inference, new_node_map, new_citation_ids = result
        assert len(new_structured_inference) == 2
        assert len(new_node_map) == 3
        assert new_citation_ids == [0]

    def test_del_redundant_node_remove_redundant(self):
        """Test _del_redundant_node removes redundant nodes."""
        structured_inference = [
            ([0], "relation", 1),
            ([2], "relation2", 3),  # Node 2 and 3 are not in save_node_set
        ]
        node_map = {
            0: {"label": "node0"},
            1: {"label": "node1"},
            2: {"label": "node2"},
            3: {"label": "node3"},
        }
        citation_ids = [0, 2]
        save_node_set = {0, 1}

        result = self.supplement_graph._del_redundant_node(
            structured_inference, node_map, citation_ids, save_node_set
        )

        new_structured_inference, new_node_map, new_citation_ids = result
        assert len(new_structured_inference) == 1  # Only first tuple should remain
        assert len(new_node_map) == 2  # Only nodes 0 and 1 should remain
        assert new_citation_ids == [0]

    @pytest.mark.asyncio
    async def test_run_connected_graph(self):
        """Test run with connected graph."""
        graph_info = GraphInfo(
            [[[0], "relation", 1]],  # structured_inference
            {0: {"label": "node0"}, 1: {"label": "node1"}},  # node_map
            [0],  # citation_ids
            [1],  # conclusion_ids
        )

        result = await self.supplement_graph.run(graph_info)

        assert len(result) == 4
        new_structured_inference, node_map, citation_ids, conclusion_ids = result
        assert len(new_structured_inference) == 1
        assert len(node_map) == 2
        assert len(citation_ids) == 1
        assert len(conclusion_ids) == 1

    @pytest.mark.asyncio
    async def test_run_invalid_conclusion_count(self):
        """Test run with invalid conclusion count."""
        graph_info = GraphInfo(
            [[[0], "relation", 1]],
            {0: {"label": "node0"}, 1: {"label": "node1"}},
            [0],
            [],  # No conclusion nodes
        )

        with pytest.raises(ValueError) as exc_info:
            await self.supplement_graph.run(graph_info)

        assert "conclusion nodes not equal to 1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_error_handling(self):
        """Test run error handling."""
        graph_info = GraphInfo(
            [[0, "relation", 1]],
            {0: {"label": "node0"}, 1: {"label": "node1"}},
            [0],
            [1],
        )

        with patch.object(
            self.supplement_graph, "generate_graph", side_effect=Exception("Test error")
        ):
            with pytest.raises(Exception) as exc_info:
                await self.supplement_graph.run(graph_info)

            assert "Test error" in str(exc_info.value)
