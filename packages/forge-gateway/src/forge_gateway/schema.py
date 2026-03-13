"""JSON Schema to Pydantic model conversion utilities.

Converts JSON Schema dicts (as received from HTTP requests) into dynamic
Pydantic BaseModel classes that the agent can use for structured output.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, create_model

# Mapping from JSON Schema type strings to Python types.
_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def json_schema_to_model(
    schema: dict[str, Any],
    model_name: str = "DynamicOutput",
) -> type[BaseModel]:
    """Convert a JSON Schema dict into a dynamic Pydantic BaseModel class.

    Supports flat schemas with typed properties and required/optional fields.
    Nested object schemas are mapped to ``dict[str, Any]``; for full recursive
    support, callers should define explicit Pydantic models instead.

    Args:
        schema: A JSON Schema dict with ``properties`` and optional ``required``.
        model_name: Name for the generated model class.

    Returns:
        A dynamically created Pydantic BaseModel subclass.

    Raises:
        ValueError: If the schema has no ``properties`` key.

    Examples:
        >>> schema = {
        ...     "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        ...     "required": ["name"],
        ... }
        >>> Model = json_schema_to_model(schema)
        >>> instance = Model(name="Alice", age=30)
        >>> instance.name
        'Alice'
    """
    properties = schema.get("properties")
    if not properties:
        raise ValueError("Schema must contain a 'properties' key with at least one field.")

    required_fields: set[str] = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        python_type = _resolve_type(field_schema)
        if field_name in required_fields:
            # Required field: type with ... (no default).
            field_definitions[field_name] = (python_type, ...)
        else:
            # Optional field: type with None default.
            field_definitions[field_name] = (python_type | None, None)

    return create_model(model_name, **field_definitions)


def _resolve_type(field_schema: dict[str, Any]) -> type:
    """Resolve a JSON Schema field definition to a Python type.

    Args:
        field_schema: A single property's schema (e.g. ``{"type": "string"}``).

    Returns:
        The corresponding Python type.
    """
    json_type = field_schema.get("type", "string")
    return _JSON_SCHEMA_TYPE_MAP.get(json_type, Any)
