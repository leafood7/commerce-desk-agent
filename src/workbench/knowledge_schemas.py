from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from workbench.schemas import Input

PolicyScope = Literal["unknown", "general", "goods_coupon", "freight_coupon", "returns", "shipping"]


class VersionOptions(Input):
    policy_scope: PolicyScope = "unknown"
    chunk_strategy: Literal["structural", "fixed"] = "structural"
    chunk_chars: int = Field(default=600, ge=160, le=1000)
    chunk_overlap: int = Field(default=80, ge=0, le=200)
    effective_from: date | None = None
    effective_until: date | None = None

    @model_validator(mode="after")
    def valid_ranges(self):
        if self.chunk_overlap >= self.chunk_chars:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until < self.effective_from
        ):
            raise ValueError("Policy end date precedes start date")
        return self


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=1200)
    policy_scope: PolicyScope | None = None
    strategy: Literal["vector", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    top_k: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.query.strip():
            raise ValueError("Query must not be blank")
        return self


class PublishInput(Input):
    acknowledge_warnings: bool = False
