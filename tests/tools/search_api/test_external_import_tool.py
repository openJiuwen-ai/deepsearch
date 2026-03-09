import pytest
import tempfile
import os
from unittest import mock
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.external_tool.tool import \
    load_external_search_tools, CustomValueException, StatusCode

MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.external_tool.tool"


class TestLoadExternalSearchTools:
    """测试加载外部搜索工具函数"""

    def test_no_func_path_and_name(self):
        """测试没有提供函数路径和名称的情况"""
        # When
        engine_name, external_mapping = load_external_search_tools("", "")

        # Then
        assert engine_name == ""
        assert external_mapping == {}

    def test_invalid_func_path_type(self, caplog):
        """测试函数路径类型无效的情况"""
        # Given
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
            temp_file.write(b"def test_func(): pass")
            temp_path = temp_file.name

        try:
            # When - 模拟配置值不是字符串的情况
            with mock.patch(f'{MODULE_PATH}.importlib.util.spec_from_file_location',
                            return_value=None):
                engine_name, external_mapping = load_external_search_tools(temp_path, "test_func")

            # Then
            assert engine_name == ""
            assert external_mapping == {}
        finally:
            os.unlink(temp_path)

    def test_function_not_found_in_module(self, caplog):
        """测试模块中找不到指定函数的情况"""
        # Given
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
            temp_file.write(b"def different_func(): pass")
            temp_path = temp_file.name

        try:
            # When
            engine_name, external_mapping = load_external_search_tools(temp_path, "non_existent_func")

            # Then
            assert engine_name == ""
            assert external_mapping == {}
            assert "function: non_existent_func not found" in caplog.text
        finally:
            os.unlink(temp_path)

    def test_module_import_exception(self, caplog):
        """测试模块导入异常的情况"""
        # Given
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
            temp_file.write(b"def test_func(): pass")
            temp_path = temp_file.name

        try:
            # When - 模拟导入时抛出异常
            with mock.patch(f'{MODULE_PATH}.importlib.util.module_from_spec') as mock_module:
                mock_module.side_effect = Exception("Import error")
                engine_name, external_mapping = load_external_search_tools(temp_path, "test_func")

            # Then
            assert engine_name == ""
            assert external_mapping == {}
        finally:
            os.unlink(temp_path)

    def test_successful_load(self, caplog):
        """测试成功加载外部工具的情况"""
        # Given - 创建一个有效的Python模块文件
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w', encoding='utf-8') as temp_file:
            temp_file.write("""
def search_function():
    \"\"\"测试搜索函数\"\"\"
    return "search result"
""")
            temp_path = temp_file.name

        try:
            # When
            engine_name, external_mapping = load_external_search_tools(temp_path, "search_function")

            # Then
            assert engine_name == "external_search"
            assert "external_search" in external_mapping
            assert callable(external_mapping["external_search"])
            assert "Successfully loaded the external" in caplog.text

            # 验证函数可以正常调用
            result = external_mapping["external_search"]()
            assert result == "search result"
        finally:
            os.unlink(temp_path)

    def test_invalid_file_path(self, caplog):
        """测试无效文件路径的情况"""
        # When
        engine_name, external_mapping = load_external_search_tools("/invalid/path/nonexistent.py", "test_func")

        # Then
        assert engine_name == ""
        assert external_mapping == {}

    def test_mock_spec_and_module_creation(self):
        """使用mock测试spec和模块创建"""
        # Given
        mock_spec = mock.MagicMock()
        mock_module = mock.MagicMock()
        mock_loader = mock.MagicMock()

        mock_spec.loader = mock_loader
        mock_module.test_function = mock.MagicMock(return_value="mocked result")

        with mock.patch(f'{MODULE_PATH}.importlib.util.spec_from_file_location',
                        return_value=mock_spec):
            with mock.patch(f'{MODULE_PATH}.importlib.util.module_from_spec',
                            return_value=mock_module):
                with mock.patch.object(mock_spec, 'loader') as mock_loader:
                    # When
                    engine_name, external_mapping = load_external_search_tools(
                        "/mock/path.py", "test_function")

                # Then
                assert engine_name == "external_search"
                assert "external_search" in external_mapping
                mock_loader.exec_module.assert_called_once_with(mock_module)

    def test_custom_exception_handling(self, caplog):
        """测试自定义异常处理"""
        # Given
        with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as temp_file:
            temp_file.write(b"def test_func(): pass")
            temp_path = temp_file.name

        try:
            # When - 模拟抛出自定义异常
            with mock.patch(f'{MODULE_PATH}.importlib.util.spec_from_file_location',
                            side_effect=CustomValueException(
                                error_code=StatusCode.LOAD_EXTEND_TOOLS_FAILED.code,
                                message=StatusCode.LOAD_EXTEND_TOOLS_FAILED.errmsg)):
                engine_name, external_mapping = load_external_search_tools(temp_path, "test_func")

            # Then
            assert engine_name == ""
            assert external_mapping == {}
        finally:
            os.unlink(temp_path)


# 如果需要测试日志记录，添加以下fixture
@pytest.fixture
def caplog(caplog):
    """配置日志捕获"""
    caplog.set_level("INFO")
    return caplog
