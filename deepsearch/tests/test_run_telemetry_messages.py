# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Serialization contract for telemetry message sampling."""

from openjiuwen_deepsearch.utils.run_telemetry import messages_payload_for_telemetry


def test_long_conversation_keeps_first_two_and_last_message():
    messages = [{"role": "user", "content": f"message-{index}"} for index in range(4)]

    payload = messages_payload_for_telemetry(messages)

    assert payload["count"] == 4
    assert [entry["content"] for entry in payload["message_contents"]] == [
        "message-0",
        "message-1",
        "message-3",
    ]
    assert [entry["index"] for entry in payload["message_contents"]] == [1, 2, 4]
