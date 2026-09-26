"""Strict, runtime-validated models for contract boundaries."""

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, NonNegativeInt, StrictBool, StrictStr


class ContractModel(BaseModel):
    """Base for data crossing component or persistence boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True)


class StepResult(ContractModel):
    """Validated result of one game command."""

    text: StrictStr
    score: int
    moves: NonNegativeInt
    done: StrictBool


class StepResultPayload(TypedDict):
    """Dictionary shape required by the public adapter contract."""

    text: str
    score: int
    moves: int
    done: bool
