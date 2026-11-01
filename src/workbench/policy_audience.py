"""Auditable, explicit policy audience; never infer missing business facts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyAudience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant: str | None = Field(default=None, min_length=1, max_length=80)
    region: str | None = Field(default=None, min_length=1, max_length=80)
    channel: str | None = Field(default=None, min_length=1, max_length=80)
    product: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("merchant", "region", "channel", "product")
    @classmethod
    def normalize(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Audience must be a nonblank explicit value")
        return value


def audience_filter(versions, audience, *, explicit_version=False):
    """Return eligible versions and unresolved dimensions before any retrieval."""
    supplied = audience.model_dump(exclude_none=True) if audience else {}
    dimensions = ("merchant", "region", "channel", "product")
    # Unknown metadata cannot establish applicability for supplied dimensions.
    candidates = [
        (document, version)
        for document, version in versions
        if all(
            (version.parse_metadata or {}).get("audience", {}).get(k) == v
            for k, v in supplied.items()
        )
    ]
    missing = []
    if not explicit_version:
        for key in dimensions:
            if key not in supplied and any(
                (v.parse_metadata or {}).get("audience", {}).get(key) for _, v in candidates
            ):
                missing.append(key)
    return candidates, missing
