"""Typed MCBEWS/1 wire DTOs.

The models intentionally keep the compact wire aliases (``v``, ``cid``,
``sid`` and ``u``) at the boundary while exposing semantic Python names to
Host adapters.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mcbe_ws_sdk.errors import ProtocolError

CapabilityRequestSchemaVersion = Literal[2]
SessionSchemaVersion = Literal[1]
TextResponseFramingVersion = Literal[1]
SessionAction = Literal[
    "new",
    "switch",
    "list",
    "status",
    "clear",
    "save",
    "restore",
    "saved",
    "delete",
    "compress",
]
TextResponseRole = Literal["user", "assistant", "approval"]
ApprovalDecisionKind = Literal["approve", "deny"]


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


class UiChatChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    msg_id: str
    chunk_index: int
    total_chunks: int
    content: str


class UiChatMessage(BaseModel):
    """A complete UI chat message with its originating conversation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    msg_id: str
    player_name: str
    message: str
    conversation_id: str = Field(default="default", alias="cid")

    @field_validator("msg_id", "player_name", "message", "conversation_id")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class SessionRequest(BaseModel):
    """Validated session operation request from the trusted ToolPlayer."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: SessionSchemaVersion = Field(default=1, alias="v")
    request_id: str
    action: SessionAction
    player_name: str
    conversation_id: str = Field(default="default", alias="cid")
    saved_session_id: str | None = Field(default=None, alias="sid")

    @field_validator("request_id", "player_name", "conversation_id")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_action_parameters(self) -> SessionRequest:
        if self.action == "switch" and self.conversation_id == "default":
            raise ValueError("switch requires cid")
        if self.action in {"restore", "delete"} and not self.saved_session_id:
            raise ValueError(f"{self.action} requires sid")
        return self


class SessionError(BaseModel):
    """Stable, bounded session error object."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str

    @field_validator("code", "message")
    @classmethod
    def _require_bounded_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        if len(value) > 256:
            raise ValueError("value exceeds 256 characters")
        return value


class SessionResponse(BaseModel):
    """Atomic session response correlated to one :class:`SessionRequest`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: SessionSchemaVersion = Field(default=1, alias="v")
    request_id: str
    action: SessionAction
    ok: bool
    data: dict[str, Any] | None = None
    error: SessionError | None = None

    @field_validator("request_id")
    @classmethod
    def _require_request_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request_id must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_result(self) -> SessionResponse:
        if self.ok and self.error is not None:
            raise ValueError("successful session response cannot contain error")
        if not self.ok and self.error is None:
            raise ValueError("failed session response must contain error")
        return self

    @classmethod
    def failure(
        cls,
        *,
        request_id: str,
        action: SessionAction,
        code: str,
        message: str,
    ) -> SessionResponse:
        return cls(
            request_id=request_id,
            action=action,
            ok=False,
            error=SessionError(code=code, message=message),
        )


class ApprovalDecision(BaseModel):
    """Typed approval decision carried by the ToolPlayer chat channel.

    ``legacy=True`` is only used by the decoder for an id-only decision.  Such
    a decision deliberately has no owner and must be resolved by a Host pending
    approval store; it is never treated as coming from the transport sender.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: SessionSchemaVersion = Field(default=1, alias="v")
    approval_id: str
    player_name: str | None = None
    conversation_id: str | None = Field(default=None, alias="cid")
    decision: ApprovalDecisionKind = "approve"
    legacy: bool = False

    @field_validator("approval_id")
    @classmethod
    def _require_approval_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approval_id must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_owner(self) -> ApprovalDecision:
        if self.legacy:
            if self.player_name is not None or self.conversation_id is not None:
                raise ValueError("legacy approval cannot claim an owner")
            return self
        if not self.player_name or not self.player_name.strip():
            raise ValueError("approval decision requires player_name")
        if not self.conversation_id or not self.conversation_id.strip():
            raise ValueError("approval decision requires cid")
        return self

    @property
    def approved(self) -> bool:
        """Whether this decision approves the pending operation."""

        return self.decision == "approve"


class TokenUsage(BaseModel):
    """Compact token usage included only in a final text response frame."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input_tokens: int = Field(alias="i", ge=0)
    output_tokens: int = Field(alias="o", ge=0)


class TextResponseChunk(BaseModel):
    """One validated ``mcbews:text_resp`` frame."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    response_id: str = Field(alias="id")
    index: int = Field(alias="i", ge=1)
    total: int = Field(alias="n", ge=1)
    player_name: str = Field(alias="p")
    role: TextResponseRole = Field(alias="r")
    content: str = Field(alias="c")
    conversation_id: str | None = Field(default=None, alias="cid")
    title: str | None = Field(default=None, alias="t")
    usage: TokenUsage | None = Field(default=None, alias="u")

    @field_validator("response_id", "player_name")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_frame(self) -> TextResponseChunk:
        if self.index > self.total:
            raise ValueError("text response index exceeds total")
        if self.usage is not None and self.index != self.total:
            raise ValueError("usage is only allowed on the final text response frame")
        if self.conversation_id is not None and not self.conversation_id.strip():
            raise ValueError("cid must not be empty")
        return self


class TextResponseMessage(BaseModel):
    """A complete, ordered text response after bounded reassembly."""

    model_config = ConfigDict(extra="forbid")

    response_id: str
    player_name: str
    role: TextResponseRole
    content: str
    conversation_id: str | None = None
    title: str | None = None
    usage: TokenUsage | None = None


def protocol_error_message(error: Exception) -> ProtocolError:
    """Convert a Pydantic validation error to the SDK boundary error type."""

    return ProtocolError(str(error))


__all__ = [
    "AddonBridgeChunk",
    "AddonBridgeRequest",
    "AddonBridgeResponse",
    "ApprovalDecision",
    "ApprovalDecisionKind",
    "CapabilityRequestSchemaVersion",
    "SessionAction",
    "SessionError",
    "SessionRequest",
    "SessionResponse",
    "SessionSchemaVersion",
    "TextResponseChunk",
    "TextResponseFramingVersion",
    "TextResponseMessage",
    "TextResponseRole",
    "TokenUsage",
    "UiChatChunk",
    "UiChatMessage",
]
