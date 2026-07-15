"""Test the standalone report-style export bundle."""

import base64

import pytest

from openjiuwen_deepsearch.algorithm.report_style.export import report_bundle


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO"
    "+/p9sAAAAASUVORK5CYII="
)


def test_build_report_bundle_keeps_existing_export_layout(tmp_path, caplog):
    """Keep rewritten links and assets compatible with the current export bundle.

    Args:
        tmp_path: pytest 提供的临时目录。
        caplog: pytest 日志捕获夹具。
    """
    with caplog.at_level("INFO", logger=report_bundle.__name__):
        bundle = report_bundle.build_report_bundle(
            {
                "response_content": "正文[依据](#inference:7)\n\n(#insertChart:chart_1)",
                "infer_messages": [
                    {
                        "id": "7",
                        "html_base64": base64.b64encode("<p>依据</p>".encode()).decode(),
                    }
                ],
                "chart_messages": [
                    {"chart_id": "chart_1", "chart_title": "销量", "base64": PNG_B64}
                ],
            },
            tmp_path,
        )

    assert bundle.markdown_path.read_text(encoding="utf-8") == (
        "正文[依据](infer/inference_7.html)\n\n![销量](charts/chart_1.png)"
    )
    assert (bundle.root_dir / "infer/inference_7.html").read_text(encoding="utf-8") == "<p>依据</p>"
    assert (bundle.root_dir / "charts/chart_1.png").exists()
    assert not (bundle.root_dir / "assets").exists()
    assert str(tmp_path) not in caplog.text
    assert "Built report bundle infer_assets=1 chart_assets=1" in caplog.text


@pytest.mark.parametrize(
    "final_result",
    [
        {"response_content": ""},
        {
            "response_content": "正文",
            "chart_messages": [{"chart_id": "../unsafe", "base64": PNG_B64}],
        },
        {
            "response_content": "正文",
            "infer_messages": [{"id": "1", "html_base64": "invalid-base64"}],
        },
    ],
)
def test_build_report_bundle_rejects_invalid_input(tmp_path, final_result):
    """Reject empty reports and unsafe resource fields.

    Args:
        tmp_path: pytest 提供的临时目录。
        final_result: 需要验证的报告最终结果。
    """
    with pytest.raises(report_bundle.ReportStyleValidationError):
        report_bundle.build_report_bundle(final_result, tmp_path)
