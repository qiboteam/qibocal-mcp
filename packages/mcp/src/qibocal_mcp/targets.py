"""Qibocal-compatible wire types without importing the calibration stack."""

from typing import Annotated

from pydantic import BeforeValidator, Field, PlainSerializer

QubitId = Annotated[int | str, Field(union_mode="left_to_right")]
QubitPairId = Annotated[
    tuple[QubitId, QubitId],
    BeforeValidator(lambda p: tuple(p.split("-")) if isinstance(p, str) else p),
    PlainSerializer(lambda p: f"{p[0]}-{p[1]}"),
]
Targets = list[QubitId] | list[QubitPairId] | list[tuple[QubitId, ...]]
