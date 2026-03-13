"""Tests for JSON Schema to Pydantic model conversion."""

from __future__ import annotations

import pytest
from forge_gateway.schema import json_schema_to_model
from pydantic import BaseModel


class TestJsonSchemaToModel:
    """Tests for json_schema_to_model conversion."""

    def test_basic_string_field(self) -> None:
        schema = {
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        model = json_schema_to_model(schema)
        assert issubclass(model, BaseModel)

        instance = model(name="Alice")
        assert instance.name == "Alice"

    def test_multiple_types(self) -> None:
        schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "score": {"type": "number"},
                "active": {"type": "boolean"},
            },
            "required": ["name", "age", "score", "active"],
        }
        model = json_schema_to_model(schema)
        instance = model(name="Bob", age=25, score=9.5, active=True)

        assert instance.name == "Bob"
        assert instance.age == 25
        assert instance.score == 9.5
        assert instance.active is True

    def test_optional_fields_default_to_none(self) -> None:
        schema = {
            "properties": {
                "name": {"type": "string"},
                "nickname": {"type": "string"},
            },
            "required": ["name"],
        }
        model = json_schema_to_model(schema)
        instance = model(name="Alice")

        assert instance.name == "Alice"
        assert instance.nickname is None

    def test_all_fields_optional(self) -> None:
        schema = {
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
        }
        model = json_schema_to_model(schema)
        instance = model()

        assert instance.x is None
        assert instance.y is None

    def test_custom_model_name(self) -> None:
        schema = {
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
        model = json_schema_to_model(schema, model_name="CustomOutput")

        assert model.__name__ == "CustomOutput"

    def test_default_model_name(self) -> None:
        schema = {
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
        model = json_schema_to_model(schema)

        assert model.__name__ == "DynamicOutput"

    def test_empty_properties_raises(self) -> None:
        with pytest.raises(ValueError, match="properties"):
            json_schema_to_model({"properties": {}})

    def test_missing_properties_raises(self) -> None:
        with pytest.raises(ValueError, match="properties"):
            json_schema_to_model({"type": "object"})

    def test_unknown_type_defaults_to_any(self) -> None:
        schema = {
            "properties": {"data": {"type": "unknown_type"}},
            "required": ["data"],
        }
        model = json_schema_to_model(schema)
        # Should accept any value without error.
        instance = model(data="anything")
        assert instance.data == "anything"

    def test_field_with_no_type_defaults_to_string(self) -> None:
        schema = {
            "properties": {"value": {}},
            "required": ["value"],
        }
        model = json_schema_to_model(schema)
        instance = model(value="hello")
        assert instance.value == "hello"

    def test_array_and_object_types(self) -> None:
        schema = {
            "properties": {
                "tags": {"type": "array"},
                "metadata": {"type": "object"},
            },
            "required": ["tags", "metadata"],
        }
        model = json_schema_to_model(schema)
        instance = model(tags=["a", "b"], metadata={"key": "val"})

        assert instance.tags == ["a", "b"]
        assert instance.metadata == {"key": "val"}

    def test_generated_model_is_valid_pydantic(self) -> None:
        schema = {
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name", "count"],
        }
        model = json_schema_to_model(schema)

        # Should be serializable.
        instance = model(name="test", count=5)
        dumped = instance.model_dump()
        assert dumped == {"name": "test", "count": 5}

        # Should produce JSON schema.
        json_schema = model.model_json_schema()
        assert "properties" in json_schema
