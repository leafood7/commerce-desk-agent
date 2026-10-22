"""Invariants for the model catalog that get_model's dispatch depends on."""

import typing
from collections import defaultdict

import pytest

from core.llm import _MODEL_TABLE
from schema.models import AllModelEnum, GoogleModelName, VertexAIModelName

MODEL_ENUMS = typing.get_args(AllModelEnum.__value__)


def test_model_enums_are_all_discovered():
    # 空元组会让后面的测试没有实际检查内容却仍然通过
    assert len(MODEL_ENUMS) >= 12


def test_model_values_are_unique_across_enums():
    # StrEnum 按值计算哈希，相同值的成员会在
    # _MODEL_TABLE 和 AVAILABLE_MODELS 中合并为一项，get_model 也会都走第一个分支
    owners = defaultdict(list)
    for enum in MODEL_ENUMS:
        for member in enum:
            owners[member.value].append(f"{enum.__name__}.{member.name}")

    duplicates = {value: names for value, names in owners.items() if len(names) > 1}
    assert not duplicates, f"model values must be unique across enums: {duplicates}"


@pytest.mark.parametrize("model", list(VertexAIModelName), ids=lambda m: m.value)
def test_vertexai_models_do_not_collide_with_google(model):
    # 每个 Vertex 模型都有对应的 Google 模型，值冲突会导致创建错误的客户端
    assert model not in GoogleModelName


def test_model_table_covers_every_model():
    # _MODEL_TABLE 与 AllModelEnum 联合类型分别维护
    assert set(_MODEL_TABLE) == {member for enum in MODEL_ENUMS for member in enum}
