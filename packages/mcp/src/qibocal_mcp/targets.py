"""Qibocal-compatible wire types without importing the calibration stack.

Tool arguments are declared with a plain top-level JSON type: some clients
turn a top-level anyOf into a text field, and then send JSON as text. Such
text is parsed as well.
"""

import json
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, Field, WithJsonSchema

QubitId = Annotated[int | str, Field(union_mode="left_to_right")]


def parse_json(value: Any) -> Any:
    """Decode JSON received as text; leave anything else to validation."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def target_list(value: Any) -> Any:
    """Accept JSON text, and a single target in place of a list."""
    value = parse_json(value)
    return value if isinstance(value, list) else [value]


def homogeneous(targets: list) -> list:
    """Qibocal takes either qubits or multi-qubit targets, not both at once."""
    if len({isinstance(target, list) for target in targets}) > 1:
        raise ValueError("Targets must be all qubit IDs, or all lists of qubit IDs")
    return targets


# Qubit IDs, or lists of IDs for multi-qubit protocols, e.g. [0, 1] or [[0, 1]].
Targets = Annotated[
    list[QubitId | list[QubitId]],
    BeforeValidator(target_list),
    AfterValidator(homogeneous),
]
# Optional JSON object, declared as an object so it is never sent as text.
Overrides = Annotated[
    dict | None,
    BeforeValidator(parse_json),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]
