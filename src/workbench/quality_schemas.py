from typing import Literal

from pydantic import Field, StrictInt, StrictStr, field_validator

from workbench.schemas import Input


class QualityLookup(Input):
    order_number: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    item_id: StrictStr = Field(min_length=1, max_length=36)
    quantity: StrictInt = Field(ge=1, le=10000)


class QualityCreate(QualityLookup):
    reason: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Reason must be nonblank")
        return value.strip()


class QualityChange(Input):
    expected_revision: StrictInt = Field(ge=1)
    action: Literal["start", "complete", "void"]
    verdict: Literal["passed", "failed", "inconclusive"] | None = None
    finding: Literal["defect_confirmed", "no_defect", "unclear"] | None = None
    note: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Note must be nonblank")
        return value.strip()
