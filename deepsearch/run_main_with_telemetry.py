#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Run :mod:`main` in-process with explicit :class:`~openjiuwen_deepsearch.utils.run_telemetry.RunTelemetryConfig`.

Telemetry options are parsed first (``parse_known_args``); everything else is forwarded to
:func:`main.main` — no ``--`` separator required.

Example
-------
.. code-block:: bash

   python run_main_with_telemetry.py \\
     --telemetry-url http://127.0.0.1:8089/events \\
     --telemetry-run-id my-experiment-1 \\
     --mode query --search_mode search --query "..." \\
     --llm_model_name ... --llm_api_key ...
"""

from __future__ import annotations

import argparse
import json
import sys

from openjiuwen_deepsearch.utils.run_telemetry import RunTelemetryConfig

import main as main_module


def _parse_argv(argv: list[str]) -> tuple[RunTelemetryConfig | None, list[str]]:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--telemetry-url", default=None)
    p.add_argument("--telemetry-run-id", default=None)
    p.add_argument("--telemetry-token", default=None)
    p.add_argument("--telemetry-headers-json", default=None)
    p.add_argument("--telemetry-timeout", type=float, default=2.0)
    args, rest = p.parse_known_args(argv)

    telemetry: RunTelemetryConfig | None = None
    url = (args.telemetry_url or "").strip()
    if url:
        extra: dict[str, str] = {}
        if args.telemetry_headers_json:
            raw = json.loads(args.telemetry_headers_json)
            if not isinstance(raw, dict):
                raise ValueError("--telemetry-headers-json must be a JSON object")
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str):
                    extra[k] = v
        telemetry = RunTelemetryConfig(
            url=url,
            run_id=(args.telemetry_run_id or "").strip() or None,
            token=(args.telemetry_token or "").strip() or None,
            extra_headers=extra,
            timeout_sec=float(args.telemetry_timeout),
        )

    return telemetry, rest


def main() -> int:
    telemetry, main_argv = _parse_argv(sys.argv[1:])
    # Always pass a list so :func:`main.main` does not fall back to ``sys.argv`` (which still
    # contains ``--telemetry-*`` flags).
    return main_module.main(argv=main_argv, telemetry=telemetry)


if __name__ == "__main__":
    main()
