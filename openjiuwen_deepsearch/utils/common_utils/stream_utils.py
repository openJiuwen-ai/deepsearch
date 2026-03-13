# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum
import time


def get_current_time():
    '''获取当前UTC时间'''

    time_ms = int(round(time.time() * 1000))

    return time_ms


class MessageType(enum.Enum):
    MESSAGE_CHUNK = "message_chunk"
    INTERRUPT = "interrupt"


class StreamEvent(enum.Enum):
    START = "start"
    DONE = "done"
    MESSAGE = "message"
    SUMMARY_RESPONSE = "summary_response"
    WAITING_USER_INPUT = "waiting_user_input"
    USER_INPUT_ENDED = "user_input_ended"
    ERROR = "error"


async def custom_stream_output(session, stream_id, stream_content, agent_name, stream_meta: dict | None = None):
    async def _write_event(event: StreamEvent, content: str):
        payload = {
            "message_id": stream_id,
            "agent": agent_name,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": event.value,
            "created_time": get_current_time()
        }

        if stream_meta:
            payload.update(dict(stream_meta))
        await session.write_custom_stream(payload)

    await _write_event(StreamEvent.START, "")
    await _write_event(StreamEvent.MESSAGE, stream_content)
    await _write_event(StreamEvent.DONE, "")
