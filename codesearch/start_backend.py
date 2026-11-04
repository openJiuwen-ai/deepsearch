# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务启动薄入口：`python start_backend.py`。

服务实现位于 `openjiuwen_codesearch/server/`（随 wheel 分发），
因此 whl 安装后也可直接用 `codesearch-server` 命令启动，无需源码树。
"""

from openjiuwen_codesearch.server.main import main

if __name__ == "__main__":
    main()
