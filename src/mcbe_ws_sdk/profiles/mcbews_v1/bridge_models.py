"""Capability bridge DTOs independent of business extensions."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CapabilityRequestSchemaVersion = Literal[2]


class AddonBridgeRequest(BaseModel):
    """Capability request sent to the reference Addon."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: CapabilityRequestSchemaVersion = Field(default=2, alias="v")
    request_id: str
    capability: str
    payload: dict[str, Any]

    @field_validator("request_id", "capability")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class AddonBridgeChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


class AddonBridgeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str
    payload: dict[str, Any]
