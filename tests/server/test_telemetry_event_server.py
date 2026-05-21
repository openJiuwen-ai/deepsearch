"""Tests for telemetry JSONL ingest validation and read filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.telemetry_event_server as tes


@pytest.fixture
def telemetry_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(tes, "server_state", tes.ServerState(jsonl_path=str(log), event_path="/events", public_base="http://127.0.0.1:9"))
    monkeypatch.setattr(tes, "app", tes._make_app())
    return TestClient(tes.app)


def test_post_rejects_empty_body_when_jsonl(telemetry_client: TestClient) -> None:
    r = telemetry_client.post("/events", content=b"")
    assert r.status_code == 422


def test_default_public_base_uses_loopback_for_wildcard_bind_host() -> None:
    assert tes._default_public_base_from_bind("0.0.0.0", 8089) == "http://127.0.0.1:8089"
    assert tes._default_public_base_from_bind("::", 8089) == "http://[::1]:8089"


def test_default_public_base_uses_loopback_for_blank_bind_host() -> None:
    assert tes._default_public_base_from_bind("", 8089) == "http://127.0.0.1:8089"
    assert tes._default_public_base_from_bind("   ", 8089) == "http://127.0.0.1:8089"


def test_default_public_base_preserves_explicit_bind_host() -> None:
    assert tes._default_public_base_from_bind("127.0.0.1", 8089) == "http://127.0.0.1:8089"


def test_default_public_base_brackets_explicit_ipv6_bind_host() -> None:
    assert tes._default_public_base_from_bind("::1", 8089) == "http://[::1]:8089"
    assert tes._default_public_base_from_bind("2001:db8::1", 8089) == "http://[2001:db8::1]:8089"
    assert tes._default_public_base_from_bind("[2001:db8::1]", 8089) == "http://[2001:db8::1]:8089"


def test_post_rejects_correlation_only_fragment(telemetry_client: TestClient) -> None:
    r = telemetry_client.post("/events", json={"conversation_id": "c1", "space_id": "s1"})
    assert r.status_code == 422


def test_post_accepts_emit_shape(telemetry_client: TestClient) -> None:
    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "seq": 1,
        "ts": "2026-01-01T00:00:00+00:00",
        "event": "run_started",
        "payload": {"conversation_id": "cid", "query_preview": "hi"},
    }
    r = telemetry_client.post("/events", json=body)
    assert r.status_code == 204
    log_path = Path(tes.server_state.jsonl_path or "")
    text = log_path.read_text(encoding="utf-8").strip()
    assert json.loads(text)["event"] == "run_started"


def test_read_skips_legacy_invalid_lines(tmp_path: Path) -> None:
    log = tmp_path / "t.jsonl"
    log.write_text(
        "{}\n"
        '{"conversation_id": "x"}\n'
        '{"event": "ok", "payload": {}}\n',
        encoding="utf-8",
    )
    rows = tes._read_all_parsed(str(log))
    assert len(rows) == 1
    assert rows[0]["event"] == "ok"
    assert rows[0]["payload"] == {}


def test_recent_and_range_match_emit_envelope(telemetry_client: TestClient) -> None:
    """Same shape as :func:`run_telemetry._build_envelope` + finalized payload."""
    rid = "run-integration-1"
    for seq, ev in [(1, "run_started"), (2, "messages_updated"), (3, "run_completed")]:
        body = {
            "schema_version": 1,
            "run_id": rid,
            "seq": seq,
            "ts": "2026-05-14T12:00:00+00:00",
            "event": ev,
            "payload": {
                "source": "test",
                "action_execution": {"action_id": None},
                "conversation_id": "cid-1",
            },
        }
        assert telemetry_client.post("/events", json=body).status_code == 204

    recent = telemetry_client.get("/telemetry/recent", params={"n": 10, "run_id": rid})
    assert recent.status_code == 200
    data = recent.json()
    assert data["count"] == 3
    assert [x["event"] for x in data["items"]] == ["run_started", "messages_updated", "run_completed"]
    assert all(x.get("run_id") == rid for x in data["items"])

    r_range = telemetry_client.get(
        "/telemetry/range",
        params={"run_id": rid, "start_seq": 2, "end_seq": 3},
    )
    assert r_range.status_code == 200
    rg = r_range.json()
    assert rg["count"] == 2
    assert [x["seq"] for x in rg["items"]] == [2, 3]
