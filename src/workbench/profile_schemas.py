from typing import Literal

from pydantic import Field

from workbench.schemas import Input


class ProfileOptions(Input):
    knowledge_collaboration_version: Literal["roles_v1", "task_board_v2"] = "roles_v1"
    policy_answer_mode: Literal["excerpts", "contract", "reviewed", "reviewed_repair"] = "excerpts"
    policy_answer_max_tokens: int = Field(default=1024, ge=256, le=2048)
    knowledge_agent_mode: Literal["single", "collaborative"] = "single"
    model_alias: Literal["rules", "server_default"] = "rules"
    prompt_template: Literal["intent_v1"] = "intent_v1"
    max_output_tokens: Literal[128, 256] = 256
    top_k: int = Field(default=3, ge=1, le=3)
    tone: Literal["plain", "friendly"] = "plain"
    return_flow_version: Literal["observed_v2", "guided_v3"] = "guided_v3"
    return_task_clarification: bool = False
    return_reference_check: bool = False
    return_source_reading: bool = False
    return_decisions: Literal["rules", "model"] = "rules"
    return_retrieval_mode: Literal["baseline", "policy_scoped", "clause_bound"] = "baseline"


class ActivateProfile(Input):
    expected_revision: int = Field(ge=0)
