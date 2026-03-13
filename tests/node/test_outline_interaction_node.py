# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
    OutlineInteractionNode,
    DependencyOutlineInteractionNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import OutlineInteraction
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.common_utils.stream_utils import StreamEvent

logger = logging.getLogger(__name__)


# ============ 测试 Fixtures ============


@pytest.fixture
def outline_interaction_node():
    """创建 OutlineInteractionNode 实例"""
    return OutlineInteractionNode()


@pytest.fixture
def mock_session():
    """创建 Mock Session"""
    session = MagicMock(spec=Session)
    session.get_global_state = MagicMock(return_value=None)
    session.update_global_state = MagicMock()
    session.interact = AsyncMock()
    session.write_custom_stream = AsyncMock()
    return session


@pytest.fixture
def mock_context():
    """创建 Mock ModelContext"""
    return MagicMock(spec=ModelContext)


@pytest.fixture
def default_config():
    """默认配置"""
    return {
        "feedback_mode": "cmd",
        "outline_interaction_enabled": True,
        "outline_interaction_max_rounds": 5,
    }


# ============ 辅助函数 ============


def setup_session_state(
    session, config=None, history_outlines=None, current_outline=None, outline_interactions=None
):
    """设置 Session 状态"""

    def get_state_side_effect(key):
        if key == "config.workflow_feedback_mode":
            return config.get("feedback_mode", "cmd") if config else "cmd"
        elif key == "config.outline_interaction_enabled":
            return config.get("outline_interaction_enabled", True) if config else True
        elif key == "config.outline_interaction_max_rounds":
            return config.get("outline_interaction_max_rounds", 5) if config else 5
        elif key == "search_context.history_outlines":
            return history_outlines if history_outlines else []
        elif key == "search_context.current_outline":
            return current_outline
        elif key == "search_context.outline_interactions":
            return outline_interactions if outline_interactions else []
        return None

    session.get_global_state.side_effect = get_state_side_effect


# ============ 核心流程测试 ============


class TestOutlineInteractionNodeCoreFlow:
    """核心流程测试"""

    @pytest.mark.asyncio
    async def test_interaction_disabled_skip_to_editor_team(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """交互功能禁用时，应直接跳转到 EDITOR_TEAM"""
        # Arrange
        config = {
            "feedback_mode": "cmd",
            "outline_interaction_enabled": False,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config)

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.EDITOR_TEAM.value
        # 不应该调用任何用户交互方法
        mock_session.interact.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_rounds_reached_skip_to_editor_team(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """达到最大轮数时，通知用户并跳转到 EDITOR_TEAM"""
        # Arrange
        config = {
            "feedback_mode": "cmd",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 3,
        }
        # 设置已有 3 轮交互历史
        outline_interactions = [
            OutlineInteraction(feedback="comment1", interaction_mode="revise_comment"),
            OutlineInteraction(feedback="comment2", interaction_mode="revise_comment"),
            OutlineInteraction(feedback="comment3", interaction_mode="revise_comment"),
        ]
        setup_session_state(
            mock_session, config=config, outline_interactions=outline_interactions
        )

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.EDITOR_TEAM.value
        # 应该通知用户达到最大轮数
        mock_session.write_custom_stream.assert_called()
        call_args = mock_session.write_custom_stream.call_args[0][0]
        assert "Maximum interaction rounds reached" in call_args["content"]

    @pytest.mark.asyncio
    async def test_user_accepts_outline(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """用户接受大纲：action='accepted' → 跳转 EDITOR_TEAM"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config)

        # Mock 用户输入
        user_input = json.dumps({"interrupt_feedback": "accepted", "feedback": ""})
        mock_session.interact.return_value = user_input

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.EDITOR_TEAM.value
        # 用户接受时，不应该保存交互记录
        calls = mock_session.update_global_state.call_args_list
        outline_interactions_updated = any(
            "search_context.outline_interactions" in str(call) for call in calls
        )
        assert not outline_interactions_updated, "接受大纲时不应保存交互记录"

    @pytest.mark.asyncio
    async def test_user_revise_with_comments(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """用户评论修改：action='revise_comment' → 跳转 OUTLINE"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        current_outline = {"title": "测试大纲", "sections": []}
        setup_session_state(
            mock_session,
            config=config,
            current_outline=current_outline,
        )

        # Mock 用户输入
        user_input = json.dumps(
            {"interrupt_feedback": "revise_comment", "feedback": "请增加更多细节"}
        )
        mock_session.interact.return_value = user_input

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.OUTLINE.value
        # 检查状态更新
        calls = mock_session.update_global_state.call_args_list
        interaction_update_found = False

        for call in calls:
            state_dict = call[0][0]
            if "search_context.outline_interactions" in state_dict:
                interactions = state_dict["search_context.outline_interactions"]
                assert len(interactions) == 1
                assert interactions[0].feedback == "请增加更多细节"
                assert interactions[0].interaction_mode == "revise_comment"
                assert interactions[0].outline_before == current_outline
                interaction_update_found = True

        assert interaction_update_found, "应该保存交互记录"

    @pytest.mark.asyncio
    async def test_user_revise_outline_directly(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """用户直接修改大纲：action='revise_outline' → 跳转 OUTLINE"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        current_outline = {"title": "旧大纲", "sections": []}
        setup_session_state(
            mock_session,
            config=config,
            current_outline=current_outline,
        )

        # Mock 用户输入 - 用户直接提供新大纲
        user_input = json.dumps(
            {
                "interrupt_feedback": "revise_outline",
                "feedback": "1. 新章节1\n2. 新章节2",
            }
        )
        mock_session.interact.return_value = user_input

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.OUTLINE.value
        # 检查状态更新
        calls = mock_session.update_global_state.call_args_list
        interaction_update_found = False

        for call in calls:
            state_dict = call[0][0]
            if "search_context.outline_interactions" in state_dict:
                interactions = state_dict["search_context.outline_interactions"]
                assert len(interactions) == 1
                assert interactions[0].feedback == "1. 新章节1\n2. 新章节2"
                assert interactions[0].interaction_mode == "revise_outline"
                assert interactions[0].outline_before == current_outline
                interaction_update_found = True

        assert interaction_update_found, "应该保存交互记录"

    @pytest.mark.asyncio
    async def test_no_user_input_goto_end(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """无用户输入时，跳转到 END"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config, history_outlines=[])

        # Mock 空用户输入
        mock_session.interact.return_value = ""

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.END.value

    @pytest.mark.asyncio
    async def test_unknown_action_goto_editor_team(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """未知 action 时，跳转到 END_NODE"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config, history_outlines=[])

        # Mock 未知 action
        user_input = json.dumps(
            {"interrupt_feedback": "unknown_action", "feedback": "一些反馈"}
        )
        mock_session.interact.return_value = user_input

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.END.value


# ============ 异常处理测试 ============


class TestOutlineInteractionNodeExceptionHandling:
    """异常处理测试"""

    @pytest.mark.asyncio
    async def test_json_parse_error_handling(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """JSON 解析错误时，记录异常信息并返回空字典"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config, history_outlines=[])

        # Mock 无效 JSON 输入
        mock_session.interact.return_value = "这不是有效的JSON"

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.END.value
        # 应该记录异常信息
        calls = mock_session.update_global_state.call_args_list
        exception_logged = any(
            "search_context.final_result.exception_info" in str(call) for call in calls
        )
        assert exception_logged, "应该记录异常信息"

# ============ 辅助方法测试 ============


class TestOutlineInteractionNodeHelperMethods:
    """辅助方法测试"""

    def test_save_history_with_comments(self, outline_interaction_node, mock_session):
        """保存交互记录，包含 feedback 和 outline_before"""
        # Arrange
        current_outline = {"title": "当前大纲", "sections": [{"title": "章节1"}]}
        outline_interactions = []

        def get_state_side_effect(key):
            if key == "search_context.current_outline":
                return current_outline
            elif key == "search_context.outline_interactions":
                return outline_interactions
            return None

        mock_session.get_global_state.side_effect = get_state_side_effect

        # Act
        outline_interaction_node._save_history(mock_session, "这是反馈", "revise_comment")

        # Assert
        mock_session.update_global_state.assert_called_once()
        updated_state = mock_session.update_global_state.call_args[0][0]

        assert "search_context.history_outlines" not in updated_state
        assert len(updated_state["search_context.outline_interactions"]) == 1
        interaction = updated_state["search_context.outline_interactions"][0]
        assert interaction.feedback == "这是反馈"
        assert interaction.interaction_mode == "revise_comment"
        assert interaction.outline_before == current_outline

    def test_save_history_without_comments(
        self, outline_interaction_node, mock_session
    ):
        """保存交互记录：revise_outline 时也保存 feedback"""
        # Arrange
        current_outline = {"title": "当前大纲", "sections": []}
        outline_interactions = []

        def get_state_side_effect(key):
            if key == "search_context.current_outline":
                return current_outline
            elif key == "search_context.outline_interactions":
                return outline_interactions
            return None

        mock_session.get_global_state.side_effect = get_state_side_effect

        # Act
        outline_interaction_node._save_history(mock_session, "新大纲JSON", "revise_outline")

        # Assert
        updated_state = mock_session.update_global_state.call_args[0][0]
        assert "search_context.history_outlines" not in updated_state
        assert len(updated_state["search_context.outline_interactions"]) == 1
        interaction = updated_state["search_context.outline_interactions"][0]
        assert interaction.feedback == "新大纲JSON"
        assert interaction.interaction_mode == "revise_outline"
        assert interaction.outline_before == current_outline


    @pytest.mark.asyncio
    async def test_notify_user_waiting_input(
        self, outline_interaction_node, mock_session
    ):
        """通知用户：发送 WAITING_USER_INPUT 事件"""
        # Act
        await outline_interaction_node._notify_user(
            mock_session, "1", StreamEvent.WAITING_USER_INPUT
        )

        # Assert
        mock_session.write_custom_stream.assert_called_once()
        payload = mock_session.write_custom_stream.call_args[0][0]

        assert payload["agent"] == NodeId.OUTLINE_INTERACTION.value
        assert payload["content"] == "1"
        assert payload["event"] == StreamEvent.WAITING_USER_INPUT.value
        assert payload["message_type"] == "interrupt"

    @pytest.mark.asyncio
    async def test_notify_user_input_ended(
        self, outline_interaction_node, mock_session
    ):
        """通知用户：发送 USER_INPUT_ENDED 事件"""
        # Act
        await outline_interaction_node._notify_user(
            mock_session, "操作完成", StreamEvent.USER_INPUT_ENDED
        )

        # Assert
        mock_session.write_custom_stream.assert_called_once()
        payload = mock_session.write_custom_stream.call_args[0][0]

        assert payload["event"] == StreamEvent.USER_INPUT_ENDED.value
        assert payload["content"] == "操作完成"

    @pytest.mark.asyncio
    async def test_get_user_input_web_mode(
        self, outline_interaction_node, mock_session
    ):
        """获取输入：web 模式调用 session.interact"""
        # Arrange
        mock_session.interact.return_value = '{"interrupt_feedback": "accepted"}'

        # Act
        result = await outline_interaction_node._get_user_input("web", "1", mock_session)

        # Assert
        mock_session.interact.assert_called_once()
        assert result == {"interrupt_feedback": "accepted"}

    @pytest.mark.asyncio
    async def test_get_user_input_cmd_mode(
        self, outline_interaction_node, mock_session
    ):
        """获取输入：cmd 模式调用 input()"""
        # Arrange
        with patch("builtins.input", return_value='{"interrupt_feedback": "accepted"}'):
            # Act
            result = await outline_interaction_node._get_user_input("cmd", "1", mock_session)

        # Assert
        assert result == {"interrupt_feedback": "accepted"}


# ============ 边界条件测试 ============

    @pytest.mark.asyncio
    async def test_max_rounds_boundary(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """边界条件：current_round == max_rounds - 1 时仍可交互"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 3,
        }
        # 设置已有 2 轮交互历史（max_rounds - 1）
        outline_interactions = [
            OutlineInteraction(feedback="comment1", interaction_mode="revise_comment"),
            OutlineInteraction(feedback="comment2", interaction_mode="revise_comment"),
        ]
        setup_session_state(
            mock_session, config=config, outline_interactions=outline_interactions
        )

        # Mock 用户接受
        mock_session.interact.return_value = '{"interrupt_feedback": "accepted"}'

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert - 应该正常交互并跳转到 EDITOR_TEAM
        assert result["next_node"] == NodeId.EDITOR_TEAM.value
        mock_session.interact.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_outline_interactions(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """空交互历史列表时 current_round 为 0"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config)
        mock_session.interact.return_value = '{"interrupt_feedback": "accepted"}'

        # Act
        result = outline_interaction_node._pre_handle(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["current_round"] == 0


# ============ DependencyOutlineInteractionNode 测试 ============


class TestDependencyOutlineInteractionNode:
    """依赖大纲交互节点测试"""

    @pytest.fixture
    def dependency_node(self):
        """创建 DependencyOutlineInteractionNode 实例"""
        return DependencyOutlineInteractionNode()

    @pytest.mark.asyncio
    async def test_inherits_from_outline_interaction_node(self, dependency_node):
        """应该继承自 OutlineInteractionNode"""
        assert isinstance(dependency_node, OutlineInteractionNode)

    @pytest.mark.asyncio
    async def test_accepted_redirects_to_dependency_reasoning_team(
        self, dependency_node, mock_session, mock_context
    ):
        """接受大纲时，跳转到 DEPENDENCY_REASONING_TEAM 而非 EDITOR_TEAM"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config, history_outlines=[])
        mock_session.interact.return_value = '{"interrupt_feedback": "accepted"}'

        # Act
        result = await dependency_node._do_invoke({}, mock_session, mock_context)

        # Assert
        assert result["next_node"] == NodeId.DEPENDENCY_REASONING_TEAM.value

    @pytest.mark.asyncio
    async def test_interaction_disabled_redirects_to_dependency_reasoning_team(
        self, dependency_node, mock_session, mock_context
    ):
        """交互禁用时，跳转到 DEPENDENCY_REASONING_TEAM"""
        # Arrange
        config = {
            "feedback_mode": "cmd",
            "outline_interaction_enabled": False,
            "outline_interaction_max_rounds": 5,
        }
        setup_session_state(mock_session, config=config)

        # Act
        result = await dependency_node._do_invoke({}, mock_session, mock_context)

        # Assert
        assert result["next_node"] == NodeId.DEPENDENCY_REASONING_TEAM.value

    @pytest.mark.asyncio
    async def test_revise_comment_still_goes_to_outline(
        self, dependency_node, mock_session, mock_context
    ):
        """修改评论时，仍然跳转到 OUTLINE"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        current_outline = {"title": "测试大纲"}
        setup_session_state(
            mock_session,
            config=config,
            history_outlines=[],
            current_outline=current_outline,
        )
        mock_session.interact.return_value = (
            '{"interrupt_feedback": "revise_comment", "feedback": "修改意见"}'
        )

        # Act
        result = await dependency_node._do_invoke({}, mock_session, mock_context)

        # Assert - 修改时仍然跳转到 OUTLINE
        assert result["next_node"] == NodeId.OUTLINE.value


# ============ 集成测试 ============


class TestOutlineInteractionNodeIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_flow_accepted(
        self, outline_interaction_node, mock_session, mock_context
    ):
        """完整流程测试：用户接受大纲"""
        # Arrange
        config = {
            "feedback_mode": "web",
            "outline_interaction_enabled": True,
            "outline_interaction_max_rounds": 5,
        }
        current_outline = {
            "title": "测试报告大纲",
            "sections": [
                {"title": "背景介绍", "description": "介绍背景"},
                {"title": "分析内容", "description": "详细分析"},
            ],
        }
        setup_session_state(
            mock_session,
            config=config,
            history_outlines=[],
            current_outline=current_outline,
        )
        mock_session.interact.return_value = '{"interrupt_feedback": "accepted"}'

        # Act
        result = await outline_interaction_node._do_invoke(
            {}, mock_session, mock_context
        )

        # Assert
        assert result["next_node"] == NodeId.EDITOR_TEAM.value

        # 验证通知调用
        assert mock_session.write_custom_stream.call_count >= 1

        # 验证 interact 被调用
        mock_session.interact.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_rounds_history_accumulation(
        self, outline_interaction_node, mock_session, mock_context, caplog
    ):
        """多轮交互历史累积测试"""
        with caplog.at_level(logging.INFO):
            # 模拟第 2 轮交互
            config = {
                "feedback_mode": "web",
                "outline_interaction_enabled": True,
                "outline_interaction_max_rounds": 5,
            }
            # 已有 1 轮交互历史
            outline_interactions = [
                OutlineInteraction(feedback="第一轮反馈", interaction_mode="revise_comment")
            ]
            current_outline = {"title": "大纲v2"}
            setup_session_state(
                mock_session,
                config=config,
                current_outline=current_outline,
                outline_interactions=outline_interactions,
            )
            mock_session.interact.return_value = (
                '{"interrupt_feedback": "revise_comment", "feedback": "继续修改"}'
            )

            # Act
            result = await outline_interaction_node._do_invoke(
                {}, mock_session, mock_context
            )

            # Assert
            assert result["next_node"] == NodeId.OUTLINE.value

            # 验证交互记录被正确保存
            calls = mock_session.update_global_state.call_args_list
            interactions_updated = False
            for call in calls:
                state = call[0][0]
                if "search_context.outline_interactions" in state:
                    interactions = state["search_context.outline_interactions"]
                    assert len(interactions) == 2
                    assert interactions[1].feedback == "继续修改"
                    assert interactions[1].interaction_mode == "revise_comment"
                    assert interactions[1].outline_before == current_outline
                    interactions_updated = True
                # 大纲交互阶段不再保存到 history_outlines
                if "search_context.history_outlines" in state:
                    assert False, "大纲交互阶段不应保存到 history_outlines"
            assert interactions_updated