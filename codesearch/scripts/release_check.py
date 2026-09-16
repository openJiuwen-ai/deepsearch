# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""发布前校验，供发布流程或 CI 调用：

1. 本包与同系列产品的 version 一致（同步发布要求）；
2. 发布依赖不得含 git 直引（包索引会拒收整个 distribution）；
3. base 版本满足本包的版本约束；
4. 若 CI 会改写版本号，改写后的版本仍须满足 base 约束。

用法：
    python scripts/release_check.py                    # 校验仓库当前状态
    python scripts/release_check.py --release-version X  # 校验 CI 将要发布的版本
"""

import argparse
import logging
import re
import sys
from pathlib import Path
import tomllib

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent  # codesearch/
REPO = HERE.parent


def load_version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def pin_accepts(pin: str, version: str) -> bool | None:
    """pin 是否接受该版本。packaging 不可用时返回 None（跳过该项校验）。"""
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version
    except ImportError:
        return None
    spec = Requirement(pin).specifier
    # 预发布版需显式放行才可能被接受，这里按“最宽松”判断：宽松都不过就一定不行
    return spec.contains(Version(version), prereleases=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-version",
        help="CI 将写入 pyproject 的版本号（流水线变量值）；给出后按它复核依赖约束",
    )
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    cs = tomllib.loads((HERE / "pyproject.toml").read_text())
    cs_ver = cs["project"]["version"]
    ds_ver = load_version(REPO / "deepsearch" / "pyproject.toml")
    base_ver = load_version(REPO / "base" / "pyproject.toml")
    target = args.release_version

    # 1) 产品版本同步
    if target:
        if cs_ver != target:
            errors.append(
                f"codesearch version={cs_ver} 与 --release-version={target} 不一致"
                "（请确认出包脚本已 sed version）"
            )
        if ds_ver != target:
            warnings.append(
                f"仓内 deepsearch={ds_ver} ≠ 发布号 {target}；"
                "由 deepsearch 出包任务单独改写，不阻断本任务"
            )
        if base_ver != target:
            warnings.append(
                f"仓内 base={base_ver} ≠ 发布号 {target}；"
                "由 build_search_base 单独改写，不阻断本任务"
            )
    elif cs_ver != ds_ver:
        errors.append(f"版本不同步：codesearch={cs_ver} deepsearch={ds_ver}（要求一致）")

    # 2) 发布依赖禁止 git 直引
    all_deps = list(cs["project"].get("dependencies", []))
    for extra_deps in cs["project"].get("optional-dependencies", {}).values():
        all_deps.extend(extra_deps)
    direct = [d for d in all_deps if "@ git+" in d or re.search(r"@\s*https?://", d)]
    if direct:
        errors.append(
            "发布依赖含 git/URL 直引（包索引会拒收整个 distribution），"
            f"发布版本需改用索引中的版本：{direct}"
        )

    # 3) base pin 可满足（仅通配 pin；CI 精确 pin 交给第 4 步）
    pin = next((d for d in cs["project"]["dependencies"] if "openjiuwen-search-base" in d), "")
    m = re.search(r"==(\d+)\.(\d+)\.\*", pin)
    if m and not base_ver.startswith(f"{m.group(1)}.{m.group(2)}."):
        errors.append(f"base 版本 {base_ver} 不满足 codesearch 的 pin {pin}")

    # 4) 以 CI 实际发布的版本复核：base 与本包会被改成同一个版本，
    if target:
        accepted = pin_accepts(pin, target)
        if accepted is False:
            errors.append(
                f"发布版本 {target} 不被 base pin 接受：{pin}\n"
                f"      CI 会把 base 与 codesearch 都发布为 {target}，"
                f"但本包的 wheel 仍要求 {pin}，安装时无法解析依赖。\n"
                f"      修法：出包脚本在改写 version 的同时，"
                f"把该 pin 一并改写为 =={target}"
            )
        elif accepted is None:
            warnings.append("未安装 packaging，跳过发布版本与 pin 的匹配校验")
        if re.search(r"(a|b|rc|dev)\d*$", target):
            warnings.append(
                f"发布版本 {target} 是 PEP 440 预发布版（规范化后带 a/b/rc/dev）。"
                "默认情况下 `pip install <包名>` **不会**选中预发布版，"
                "使用者需加 --pre，uv 需加 --prerelease=allow。若这不是本意，"
                "请改用正式版本号。"
            )

    if errors:
        logger.error("release_check FAILED:")
        for e in errors:
            logger.error("  ✗ %s", e)
        return 1
    logger.info(
        "release_check OK: codesearch=%s deepsearch=%s base=%s",
        cs_ver,
        ds_ver,
        base_ver,
    )
    if target:
        logger.info("  发布版本 %s 与 base pin %s 相容", target, pin)
    for w in warnings:
        logger.info("  ⚠ %s", w)
    logger.info("  提醒（未自动校验）：base 需与本包共同发布；LICENSE 与三方声明需齐备。")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
