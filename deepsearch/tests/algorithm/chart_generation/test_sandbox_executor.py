# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""沙箱逃逸回归用例：读写白名单、子模块黑名单、进程启动入口。"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.chart_generation.sandbox.sandbox_executor import (
    AsyncCodeExecutor,
)

# 用测试文件所在目录作为"仓库外"路径：它既不在工作目录，也不在系统临时目录
OUTSIDE_DIR = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def executor(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("sandbox_workdir")
    return AsyncCodeExecutor(working_dir=str(workdir), exec_timeout=120)


def _run(executor, code):
    return asyncio.run(executor.execute(code))


def _lit(value) -> str:
    return json.dumps(str(value))


def test_os_open_cannot_write_outside_workdir(executor):
    target = OUTSIDE_DIR / "_sandbox_denied_write.txt"
    code = (
        "import os\n"
        f"fd = os.open({_lit(target)}, os.O_WRONLY | os.O_CREAT)\n"
        "os.write(fd, b'x')\n"
        "os.close(fd)\n"
        "print('wrote')\n"
    )
    result = _run(executor, code)

    assert result["error"] is True
    assert "PermissionError" in result["stderr"]
    assert not target.exists()


def test_os_open_cannot_read_outside_allowlist(executor):
    target = Path(__file__).resolve()
    code = (
        "import os\n"
        f"fd = os.open({_lit(target)}, os.O_RDONLY)\n"
        "print(len(os.read(fd, 16)))\n"
        "os.close(fd)\n"
    )
    result = _run(executor, code)

    assert result["error"] is True
    assert "PermissionError" in result["stderr"]


def test_pathlib_write_cannot_escape_workdir(executor):
    target = OUTSIDE_DIR / "_sandbox_denied_pathlib.txt"
    code = f"from pathlib import Path\nPath({_lit(target)}).write_text('x')\nprint('wrote')\n"
    result = _run(executor, code)

    assert result["error"] is True
    assert "PermissionError" in result["stderr"]
    assert not target.exists()


def test_os_remove_cannot_touch_files_outside_workdir(executor):
    target = OUTSIDE_DIR / "_sandbox_denied_remove.txt"
    target.write_text("keep me\n", encoding="utf-8")
    code = f"import os\nos.remove({_lit(target)})\nprint('removed')\n"
    try:
        result = _run(executor, code)
        assert result["error"] is True
        assert "PermissionError" in result["stderr"]
        assert target.exists()
    finally:
        target.unlink(missing_ok=True)


def test_dotted_blacklist_blocks_asyncio_subprocess(executor):
    """限制名单里的子模块条目必须生效（此前只比较点号前的包名）。"""
    code = "import asyncio.subprocess\nprint('imported')\n"
    result = _run(executor, code)

    assert result["error"] is True
    assert "ImportError" in result["stderr"]


@pytest.mark.skipif(sys.platform != "win32", reason="os.startfile 仅 Windows 存在")
def test_os_startfile_is_blocked(executor):
    code = "import os\nos.startfile('calc.exe')\nprint('started')\n"
    result = _run(executor, code)

    assert result["error"] is True
    assert "PermissionError" in result["stderr"]


def test_legit_write_and_read_inside_workdir_still_work(executor):
    code = (
        "from pathlib import Path\n"
        "import os\n"
        "Path('legit.txt').write_text('ok', encoding='utf-8')\n"
        "fd = os.open('legit2.txt', os.O_WRONLY | os.O_CREAT)\n"
        "os.write(fd, b'ok')\n"
        "os.close(fd)\n"
        "print(Path('legit.txt').read_text(encoding='utf-8'))\n"
    )
    result = _run(executor, code)

    assert result["error"] is False, result["stderr"]
    assert "ok" in result["stdout"]
