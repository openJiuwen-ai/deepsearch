# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""发布前校验（base/README "发布前 TODO" 的机器化，供 checklist/CI 调用）：

1. codesearch 与 deepsearch 的 version 一致（leader 裁决：版本号同步）；
2. 发布依赖不得含 git 直引（PyPI 会拒收整包）；
3. base 版本满足 codesearch 的 pin。

用法：python scripts/release_check.py   （在 codesearch/ 目录下）
退出码非 0 即不放行。
"""

import re
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # codesearch/
REPO = HERE.parent


def load_version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def main() -> int:
    errors: list[str] = []

    cs = tomllib.loads((HERE / "pyproject.toml").read_text())
    cs_ver = cs["project"]["version"]
    ds_ver = load_version(REPO / "deepsearch" / "pyproject.toml")
    base_ver = load_version(REPO / "base" / "pyproject.toml")

    # 1) 产品版本同步
    if cs_ver != ds_ver:
        errors.append(f"版本不同步：codesearch={cs_ver} deepsearch={ds_ver}（裁决要求一致）")

    # 2) 发布依赖禁止 git 直引
    all_deps = list(cs["project"].get("dependencies", []))
    for extra_deps in cs["project"].get("optional-dependencies", {}).values():
        all_deps.extend(extra_deps)
    direct = [d for d in all_deps if "@ git+" in d or re.search(r"@\s*https?://", d)]
    if direct:
        errors.append(
            "发布依赖含 git/URL 直引（PyPI 拒收整包），发布版需切换为索引版本"
            f"（如 openjiuwen==0.1.10.post3，已验证兼容）：{direct}"
        )

    # 3) base pin 可满足
    pin = next((d for d in cs["project"]["dependencies"] if "openjiuwen-search-base" in d), "")
    m = re.search(r"==(\d+)\.(\d+)\.\*", pin)
    if m and not base_ver.startswith(f"{m.group(1)}.{m.group(2)}."):
        errors.append(f"base 版本 {base_ver} 不满足 codesearch 的 pin {pin}")

    if errors:
        print("release_check FAILED:")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print(f"release_check OK: codesearch={cs_ver} deepsearch={ds_ver} base={base_ver}")
    print("  提醒（未自动校验）：base 需与 codesearch 共同发布；LICENSE/三方声明齐备。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
