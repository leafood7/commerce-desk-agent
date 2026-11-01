from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from workbench.policy_audience import PolicyAudience
from workbench.schemas import Input

PolicyScope = Literal["unknown", "general", "goods_coupon", "freight_coupon", "returns", "shipping"]


class VersionOptions(Input):
    audience: PolicyAudience | None = None
    knowledge_kind: Literal["document", "faq", "product"] | None = None
    faq_sources: list[str] | None = Field(default=None, max_length=8)
    policy_scope: PolicyScope = "unknown"
    chunk_strategy: Literal["structural", "fixed", "parent_child"] = "structural"
    chunk_chars: int = Field(default=600, ge=160, le=1000)
    chunk_overlap: int = Field(default=80, ge=0, le=200)
    effective_from: date | None = None
    effective_until: date | None = None

    @model_validator(mode="after")
    def valid_ranges(self):
        if self.knowledge_kind == "faq" and (
            not self.faq_sources or len(set(self.faq_sources)) != len(self.faq_sources)
        ):
            raise ValueError("FAQ requires distinct reviewed source versions")
        if self.knowledge_kind != "faq" and self.faq_sources:
            raise ValueError("Source links are supported only for FAQ")
        if self.faq_sources and any(not 1 <= len(v) <= 36 for v in self.faq_sources):
            raise ValueError("Invalid FAQ source")
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
    audience: PolicyAudience | None = None
    query: str = Field(min_length=1, max_length=1200)
    policy_scope: PolicyScope | None = None
    strategy: Literal["vector", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    top_k: int = Field(default=3, ge=1, le=5)
    version_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.query.strip():
            raise ValueError("Query must not be blank")
        return self


class PublishInput(Input):
    acknowledge_warnings: bool = False


class ReadInput(Input):
    version_id: str = Field(min_length=1, max_length=36)
    parent_id: str = Field(pattern=r"^p[0-9]{1,4}$")
    offset: int = Field(default=0, ge=0, le=1000000)
    max_chars: int = Field(default=2000, ge=100, le=6000)
    audience: PolicyAudience | None = None


class ParsePatch(Input):
    block_index: int = Field(ge=0, le=100000)
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=20000)


class ReasonedInput(Input):
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("A meaningful review reason is required")
        return value.strip()


class ParseRevisionInput(ReasonedInput):
    expected_active_version_id: str = Field(min_length=1, max_length=36)
    patches: list[ParsePatch] = Field(min_length=1, max_length=12)


class ParseReviewInput(ReasonedInput):
    action: Literal["approve", "reject"]
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RollbackInput(ReasonedInput):
    version_id: str = Field(min_length=1, max_length=36)
    expected_active_version_id: str = Field(min_length=1, max_length=36)
