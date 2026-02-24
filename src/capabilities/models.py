from typing import Any, Literal

from pydantic import BaseModel, Field


class CapabilityHit(BaseModel):
    id: str
    kind: Literal["workflow", "runtime_tool", "execution_pattern"]
    summary: str
    when_to_use: str
    required_args: list[str] = Field(default_factory=list)
    example: str


class CapabilityDoc(BaseModel):
    id: str
    kind: str
    description: str
    arg_schema: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    expected_output_shape: dict[str, Any] = Field(default_factory=dict)
