from unittest.mock import MagicMock

from openjiuwen_deepsearch.algorithm.user_feedback_processor.history import (
    build_current_outline_update,
)


def test_build_current_outline_update_appends_incremental_plan_to_matched_section():
    matched_section = MagicMock()
    matched_section.id = "1"
    matched_section.plans = []
    other_section = MagicMock()
    other_section.id = "2"
    other_section.plans = []
    current_outline = MagicMock(sections=[matched_section, other_section])
    incremental_plan = MagicMock()

    updated_outline = build_current_outline_update(
        current_outline=current_outline,
        action_result={
            "matched_section_id": "1",
            "incremental_plan": incremental_plan,
        },
    )

    assert updated_outline is current_outline
    assert matched_section.plans == [incremental_plan]
    assert other_section.plans == []


def test_build_current_outline_update_returns_none_without_incremental_plan():
    current_outline = MagicMock(sections=[])

    updated_outline = build_current_outline_update(
        current_outline=current_outline,
        action_result={"matched_section_id": "1"},
    )

    assert updated_outline is None
