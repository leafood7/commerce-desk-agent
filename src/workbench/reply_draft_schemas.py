"""Reply draft request contracts."""

from typing import Literal

from pydantic import Field, StrictStr

from workbench.schemas import Input


class GenerateDraft(Input):
    return_request_id: StrictStr = Field(min_length=1, max_length=36)
    conversation_id: StrictStr | None = Field(default=None, max_length=36)
    mode: Literal["template", "llm"] = "template"


class EditDraft(Input):
    content: StrictStr = Field(min_length=1, max_length=2000)


class ConfirmDraft(Input):
    expected_facts_snapshot_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")