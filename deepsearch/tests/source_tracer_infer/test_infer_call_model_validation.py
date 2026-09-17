# -*- coding: UTF-8 -*-

"""
infer_call_model 模块的单元测试。
重点覆盖新增的 is_list_of 校验函数，防止 shallow type_check 问题复发。
"""

import pytest

from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import (
    is_list_of,
    type_check,
    is_equal_length,
    is_valid_supplement_triples,
)
from openjiuwen_deepsearch.common.exception import CustomValueException


class TestIsListOfValid:
    """合法输入应通过校验。"""

    def test_list_of_int_basic(self):
        """标准 list[int] 通过。"""
        is_list_of([0, 1, 2], int)

    def test_list_of_int_single(self):
        """单元素 list[int] 通过。"""
        is_list_of([42], int)

    def test_list_of_int_empty(self):
        """空 list[int] 通过（允许无引用场景返回 []）。"""
        is_list_of([], int)

    def test_list_of_str_basic(self):
        """标准 list[str] 通过。"""
        is_list_of(["a", "b", "c"], str)

    def test_list_of_str_empty(self):
        """空 list[str] 通过。"""
        is_list_of([], str)


class TestIsListOfInvalidType:
    """顶层容器不是 list → 拒绝。"""

    def test_string_rejected(self):
        """LLM 返回 "0,1" 而非 [0,1] → CustomValueException。"""
        with pytest.raises(CustomValueException):
            is_list_of("0,1", int)

    def test_none_rejected(self):
        """None → CustomValueException。"""
        with pytest.raises(CustomValueException):
            is_list_of(None, int)

    def test_dict_rejected(self):
        """{} → CustomValueException。"""
        with pytest.raises(CustomValueException):
            is_list_of({"id": 0}, int)

    def test_int_rejected(self):
        """裸 int → CustomValueException。"""
        with pytest.raises(CustomValueException):
            is_list_of(0, int)


class TestIsListOfElementType:
    """容器是 list 但元素类型不对 → 拒绝。"""

    def test_nested_list_rejected(self):
        """LLM 返回 [[0,1]] 嵌套 list → 拒绝（shallow type_check 会漏掉）。"""
        with pytest.raises(CustomValueException):
            is_list_of([[0, 1]], int)

    def test_string_elements_rejected_for_int(self):
        """LLM 返回 ["0", "1"] 字符串元素 → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of(["0", "1"], int)

    def test_dict_elements_rejected(self):
        """LLM 返回 [{"id": 0}] dict 元素 → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of([{"id": 0}], int)

    def test_float_elements_rejected_for_int(self):
        """LLM 返回 [0.0, 1.0] 浮点 → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of([0.0, 1.0], int)

    def test_int_elements_rejected_for_str(self):
        """list[str] 检查时遇到 int 元素 → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of(["ok", 123, "done"], str)


class TestIsListOfBoolExclusion:
    """Python bool 是 int 子类，必须显式排除。"""

    def test_true_rejected_for_int(self):
        """True 不能作为合法 int。"""
        with pytest.raises(CustomValueException):
            is_list_of([True], int)

    def test_false_rejected_for_int(self):
        """False 不能作为合法 int。"""
        with pytest.raises(CustomValueException):
            is_list_of([False], int)

    def test_mixed_int_and_bool_rejected(self):
        """混合 [0, True, 1] → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of([0, True, 1], int)


class TestIsListOfDefaultParam:
    """expected_type 参数默认值为 int。"""

    def test_default_is_int(self):
        """不传 expected_type，默认按 int 校验。"""
        is_list_of([1, 2, 3])  # should pass

    def test_default_int_rejects_str(self):
        """不传 expected_type 时 str 元素被拒绝。"""
        with pytest.raises(CustomValueException):
            is_list_of(["1", "2"])


class TestIsValidSupplementTriplesValid:
    """补边三元组合法输入应通过校验。"""

    def test_single_triple_basic(self):
        """标准 [[0], "related", 5] 三元组包裹在列表中通过。"""
        is_valid_supplement_triples([[[0], "related", 5]])

    def test_multi_triple_basic(self):
        """多条三元组通过。"""
        is_valid_supplement_triples([
            [[0], "related", 5],
            [[1, 2], "infers", 7],
        ])

    def test_empty_list(self):
        """空 list 通过（允许无补边场景）。"""
        is_valid_supplement_triples([])

    def test_multi_head_ids(self):
        """头实体包含多个节点 id 通过。"""
        is_valid_supplement_triples([[[0, 1, 2], "related", 5]])

    def test_default_target_is_3(self):
        """不传 target，默认三元组长度为 3。"""
        is_valid_supplement_triples([[[0], "related", 5]])


class TestIsValidSupplementTriplesContainer:
    """顶层容器非法 → 拒绝。"""

    def test_not_a_list_rejected(self):
        """顶层不是 list → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples("[[0], 'related', 5]")

    def test_none_rejected(self):
        """None → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples(None)

    def test_dict_rejected(self):
        """dict → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples({"head": [0], "relation": "related", "tail": 5})


class TestIsValidSupplementTriplesTriple:
    """三元组结构非法 → 拒绝（对应 supplement_graph new_tuple[0][0] 崩溃点）。"""

    def test_triple_not_a_list_rejected(self):
        """三元组不是 list（如 str）→ 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([["0,related,5"]])

    def test_wrong_length_rejected(self):
        """三元组长度不是 3 → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[0], "related"]])

    def test_bare_head_int_rejected(self):
        """头实体是裸 int 而非 list → 拒绝（消费端 new_tuple[0][0] 会 TypeError）。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[1, "related", 5]])

    def test_bare_head_str_rejected(self):
        """头实体是裸 str（如 "0,2"）→ 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([["0,2", "related", 5]])

    def test_empty_head_rejected(self):
        """头实体是空 list → 拒绝（消费端 new_tuple[0][0] 会 IndexError）。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[], "related", 5]])


class TestIsValidSupplementTriplesElements:
    """三元组元素类型非法 → 拒绝。"""

    def test_head_str_ids_rejected(self):
        """头实体节点 id 是 str → 拒绝（set 成员判断需要可哈希 int）。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[["0"], "related", 5]])

    def test_head_bool_rejected(self):
        """头实体节点 id 是 bool → 拒绝（bool 是 int 子类，需显式排除）。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[True], "related", 5]])

    def test_tail_str_rejected(self):
        """尾实体是 str 节点 id → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[0], "related", "5"]])

    def test_tail_list_rejected(self):
        """尾实体是 list → 拒绝（unhashable，set 成员判断会 TypeError）。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[0], "related", [5]]])

    def test_tail_bool_rejected(self):
        """尾实体是 bool → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[0], "related", True]])

    def test_relation_int_rejected(self):
        """关系不是 str → 拒绝。"""
        with pytest.raises(CustomValueException):
            is_valid_supplement_triples([[[0], 1, 5]])
