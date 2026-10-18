"""Machine-readable input contracts for native psychology journalling."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.2.0"
REFLECTIONS = (
    "situation", "early_warning_condition", "trigger", "emotion_body_state",
    "automatic_thought", "impulse_temptation", "action_taken", "hypothetical_cost_if_repeated",
    "root_belief_or_fear", "future_regret_or_reward", "replacement_thought", "better_response",
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Provenance(Input):
    kind: Literal["user_report", "imported", "assistant_interpretation"]
    source: str | None = None
    recorded_by: str | None = None
    observed_at: str | None = Field(None, description="Actual ISO 8601 time with offset, if known")


class PatternData(Input):
    pattern_key: str | None = Field(None, description="Unique stable key, required on create; immutable")
    system_key: str | None = None
    label: str | None = Field(None, description="Required on create; a rename preserves history")
    description: str | None = None
    tags: list[str] | None = Field(None, description="User-confirmed labels; arrays replace atomically, null clears")
    practice_response: str | None = None
    status: Literal["active", "archived"] | None = None


class PatternEntryData(Input):
    entry_key: str | None = Field(None, description="Required on create; unique immutable occurrence key")
    account_id: int | None = Field(None, description="Required existing account on create; immutable")
    event_date: str | None = Field(None, description="Required YYYY-MM-DD local reported date; never creation date", json_schema_extra={"format": "date"})
    timezone: str | None = Field(None, description="Required IANA timezone")
    occurred_at: str | None = Field(None, description="Actual offset-aware event time if known; omit for date-only events", json_schema_extra={"format": "date-time"})
    pattern_id: int | None = None
    title: str | None = None
    session_id: int | None = None
    candidate_id: int | None = None
    trade_ids: list[int] | None = None
    position_id: int | None = None
    parent_journal_entry_id: int | None = None
    context_journal_entry_id: int | None = None
    symbol: str | None = None
    setup_description: str | None = None
    situation: str | None = None
    early_warning_condition: str | None = None
    trigger: str | None = None
    emotion_body_state: str | None = None
    automatic_thought: str | None = None
    impulse_temptation: str | None = None
    action_taken: str | None = None
    hypothetical_cost_if_repeated: str | None = None
    root_belief_or_fear: str | None = None
    future_regret_or_reward: str | None = None
    replacement_thought: str | None = None
    better_response: str | None = None
    observed_result: str | None = None
    next_session_intention: str | None = None
    interpretation: str | None = Field(None, description="Assistant interpretation kept separate from original user reflections")
    trigger_tags: list[str] | None = None
    emotion_tags: list[str] | None = None
    behaviour_tags: list[str] | None = None
    urge_response: Literal["resisted", "acted_on", "no_urge", "unknown"] | None = None
    plan_adherence: Literal["followed", "deviated", "not_applicable", "unknown"] | None = None
    rule_breach: Literal["yes", "no", "unknown"] | None = None
    rule_reference: str | None = None
    status: Literal["draft", "reviewed", "archived"] | None = None
    follow_up_status: Literal["pending", "reviewed", "not_required"] | None = None
    related_pattern_entry_ids: list[int] | None = None
    management_event_ids: list[int] | None = None
    evidence_ids: list[int] | None = None
    source: str | None = None
    recorded_by: str | None = None
    field_provenance: dict[str, Provenance] | None = None
    field_states: dict[str, Literal["provided", "unknown", "not_applicable"]] | None = Field(None, description="Per reflection field; arrays/objects replace atomically. Unknown and not applicable remain distinct.")
    template_id: int | None = None
    template_version: str | None = None
    baseline_version: str | None = None
    playbook_version: str | None = None
    uncalibrated_score_note: str | None = Field(None, description="Original optional score and meaning as text; no numeric scale or readiness mapping")
    proposed_rule: str | None = None
    proposed_rule_status: Literal["proposed", "adopted", "rejected"] | None = None
    adoption_decision: str | None = None
    adoption_reference: str | None = None
    adopted_at: str | None = None


class ParentUpdate(Input):
    status: Literal["draft", "reviewed", "archived"] | None = None
    follow_up_status: Literal["pending", "reviewed", "not_required"] | None = None
    next_session_intention: str | None = None


class FollowupData(Input):
    pattern_entry_id: int
    followup_key: str
    note: str
    observed_at: str | None = None
    response_practised: bool | None = None
    response_helpful: bool | None = None
    observed_result: str | None = None
    next_session_intention: str | None = None
    evidence_ids: list[int] | None = None
    source: str | None = None
    recorded_by: str | None = None
    field_provenance: dict[str, Provenance] | None = None
    corrects_followup_id: int | None = None
    parent_update: ParentUpdate | None = Field(None, description="Optional parent state/intention patch, atomic with follow-up; requires expected_revision")


class PatternFilters(Input):
    account_id: int | None = None
    session_id: int | None = None
    symbol: str | None = None
    pattern_id: int | None = None
    status: Literal["draft", "reviewed", "archived"] | None = None
    follow_up_status: Literal["pending", "reviewed", "not_required"] | None = None
    urge_response: Literal["resisted", "acted_on", "no_urge", "unknown"] | None = None
    plan_adherence: Literal["followed", "deviated", "not_applicable", "unknown"] | None = None
    rule_breach: Literal["yes", "no", "unknown"] | None = None
    trigger_tags: list[str] | None = None
    emotion_tags: list[str] | None = None
    behaviour_tags: list[str] | None = None


class MigrationSelection(Input):
    source_ids: list[int] | None = Field(None, description="Explicit legacy journal IDs; each must match namespace or supplied template binding")
    template_ids: list[int] | None = Field(None, description="Explicit database-specific pattern template bindings; no hard-coded IDs")
    namespace: bool = Field(False, description="Explicitly select entries with custom_fields.neo_pattern")
    timezone: str = Field(description="Confirmed timezone used only where legacy metadata lacks it")


class EvidenceData(Input):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra={"anyOf": [
        {"required": ["journal_entry_id"], "properties": {"journal_entry_id": {"type": "integer", "minimum": 1}}},
        {"required": ["pattern_entry_id"], "properties": {"pattern_entry_id": {"type": "integer", "minimum": 1}}},
    ]})
    journal_entry_id: int | None = None
    pattern_entry_id: int | None = None
    candidate_id: int | None = None
    trade_id: int | None = None
    filename: str
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    stage: Literal["pre", "management", "post"]
    captured_at: str = Field(json_schema_extra={"format": "date-time"})
    symbol: str
    timeframe: str
    source: str
    chart_context: str | None = None
