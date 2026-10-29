from typing import Literal

from pydantic import Field

from workbench.schemas import Input


class ProfileOptions(Input):
    model_alias: Literal["rules", "server_default"] = "rules"
    prompt_template: Literal["intent_v1"] = "intent_v1"
    max_output_tokens: Literal[128, 256] = 256
    top_k: int = Field(default=3, ge=1, le=3)
    tone: Literal["plain", "friendly"] = "plain"


class ActivateProfile(Input):
    expected_revision: int = Field(ge=0)
