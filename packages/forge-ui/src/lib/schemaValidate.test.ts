import { describe, it, expect } from "vitest";
import { resolveSchemaPath, validateAgainstSchema } from "./schemaValidate";
import type { JsonSchemaLike } from "./schemaValidate";

describe("resolveSchemaPath", () => {
  it("resolves a simple nested path of plain object properties", () => {
    const root: JsonSchemaLike = {
      properties: {
        tools: {
          properties: {
            manual_tools: {
              items: {
                type: "object",
              },
            },
          },
        },
      },
    };
    const path = ["properties", "tools", "properties", "manual_tools", "items"];
    const result = resolveSchemaPath(root, path);
    expect(result).toEqual({ type: "object" });
  });

  it("resolves through a $ref mid-path", () => {
    const root: JsonSchemaLike = {
      properties: {
        tools: {
          properties: {
            manual_tools: {
              items: {
                $ref: "#/$defs/ManualTool",
              },
            },
          },
        },
      },
      $defs: {
        ManualTool: {
          type: "object",
          required: ["name"],
        },
      },
    };
    const path = ["properties", "tools", "properties", "manual_tools", "items"];
    const result = resolveSchemaPath(root, path);
    expect(result).toEqual({ type: "object", required: ["name"] });
  });

  it("returns undefined for a path segment that doesn't exist", () => {
    const root: JsonSchemaLike = {
      properties: {
        tools: {
          properties: {
            manual_tools: {
              items: {
                type: "object",
              },
            },
          },
        },
      },
    };
    const path = ["properties", "tools", "properties", "nonexistent", "items"];
    const result = resolveSchemaPath(root, path);
    expect(result).toBeUndefined();
  });

  it("returns undefined when a $ref can't be resolved (missing from $defs)", () => {
    const root: JsonSchemaLike = {
      properties: {
        tools: {
          properties: {
            manual_tools: {
              items: {
                $ref: "#/$defs/NonExistent",
              },
            },
          },
        },
      },
      $defs: {},
    };
    const path = ["properties", "tools", "properties", "manual_tools", "items"];
    const result = resolveSchemaPath(root, path);
    expect(result).toBeUndefined();
  });

  it("returns undefined for an empty path array", () => {
    const root: JsonSchemaLike = {
      properties: {
        tools: {
          type: "object",
        },
      },
    };
    const path: string[] = [];
    const result = resolveSchemaPath(root, path);
    expect(result).toBeUndefined();
  });
});

describe("validateAgainstSchema", () => {
  it("returns [] (valid) when schema is undefined -- fails open", () => {
    const errors = validateAgainstSchema(undefined, { foo: "bar" });
    expect(errors).toEqual([]);
  });

  it("type mismatch: schema { type: 'string' }, value 42 -- returns one error containing 'expected string'", () => {
    const schema: JsonSchemaLike = { type: "string" };
    const errors = validateAgainstSchema(schema, 42);
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/expected string/i);
  });

  it("type match: schema { type: 'string' }, value 'hello' -- returns []", () => {
    const schema: JsonSchemaLike = { type: "string" };
    const errors = validateAgainstSchema(schema, "hello");
    expect(errors).toEqual([]);
  });

  it("union type: schema { type: ['string', 'null'] } accepts both a string value and null, rejects a number", () => {
    const schema: JsonSchemaLike = { type: ["string", "null"] };
    
    // String value
    let errors = validateAgainstSchema(schema, "hello");
    expect(errors).toEqual([]);

    // null value
    errors = validateAgainstSchema(schema, null);
    expect(errors).toEqual([]);

    // Number value
    errors = validateAgainstSchema(schema, 42);
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/expected string \| null/i);
  });

  it("enum: schema { type: 'string', enum: ['GET', 'POST'] } -- 'GET' passes, 'DELETE' produces an error containing 'must be one of'", () => {
    const schema: JsonSchemaLike = { type: "string", enum: ["GET", "POST"] };
    
    // Valid value
    let errors = validateAgainstSchema(schema, "GET");
    expect(errors).toEqual([]);

    // Invalid value
    errors = validateAgainstSchema(schema, "DELETE");
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/must be one of/i);
  });

  it("required object fields: schema with required ['name', 'description'] against { name: 'x' } (missing description) -- returns exactly one error, and it contains both 'description' and 'is required'", () => {
    const schema: JsonSchemaLike = {
      type: "object",
      required: ["name", "description"],
      properties: {
        name: { type: "string" },
        description: { type: "string" },
      },
    };
    const errors = validateAgainstSchema(schema, { name: "x" });
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/description/);
    expect(errors[0]).toMatch(/is required/i);
  });

  it("required treats an empty string as missing: same schema as #11, against { name: 'x', description: '' } -- still reports description as required (empty string counts as absent)", () => {
    const schema: JsonSchemaLike = {
      type: "object",
      required: ["name", "description"],
      properties: {
        name: { type: "string" },
        description: { type: "string" },
      },
    };
    const errors = validateAgainstSchema(schema, { name: "x", description: "" });
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/description/);
    expect(errors[0]).toMatch(/is required/i);
  });

  it("nested property validation: schema with nested api object against { api: {} } -- returns an error whose path-prefix is 'api.method'", () => {
    const schema: JsonSchemaLike = {
      type: "object",
      properties: {
        api: {
          type: "object",
          required: ["method"],
          properties: {
            method: { type: "string" },
          },
        },
      },
    };
    const errors = validateAgainstSchema(schema, { api: {} });
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/api\.method/i);
  });

  it("array items: schema with array of objects with required name against [{ name: 'a' }, {}] -- returns exactly one error, and it references index 1 (contains '[1]')", () => {
    const schema: JsonSchemaLike = {
      type: "array",
      items: {
        type: "object",
        required: ["name"],
        properties: {
          name: { type: "string" },
        },
      },
    };
    const errors = validateAgainstSchema(schema, [{ name: "a" }, {}]);
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/\[1\]/);
  });

  it("$ref resolution during validation: schema { $ref: '#/$defs/Widget' } with root { $defs: { Widget: { type: 'object', required: ['id'] } } } against {} -- returns one error containing 'id' and 'is required'", () => {
    const schemaWithRef: JsonSchemaLike = { $ref: "#/$defs/Widget" };
    const rootWithDefs: JsonSchemaLike = {
      $defs: {
        Widget: {
          type: "object",
          required: ["id"],
        },
      },
    };
    const errors = validateAgainstSchema(schemaWithRef, {}, rootWithDefs);
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/id/);
    expect(errors[0]).toMatch(/is required/i);
  });

  it("unresolvable $ref during validation fails open: schema { $ref: '#/$defs/Missing' }, root { $defs: {} } -- returns [] (never throws, never blocks)", () => {
    const schemaWithRef: JsonSchemaLike = { $ref: "#/$defs/Missing" };
    const rootWithDefs: JsonSchemaLike = {
      $defs: {},
    };
    const errors = validateAgainstSchema(schemaWithRef, {}, rootWithDefs);
    expect(errors).toEqual([]);
  });

  it("anyOf: schema { anyOf: [{ type: 'string' }, { type: 'number' }] } -- a string value and a number value both pass ([] errors each), a boolean value fails with an error containing 'does not match any allowed schema'", () => {
    const schema: JsonSchemaLike = {
      anyOf: [{ type: "string" }, { type: "number" }],
    };

    // String value
    let errors = validateAgainstSchema(schema, "hello");
    expect(errors).toEqual([]);

    // Number value
    errors = validateAgainstSchema(schema, 42);
    expect(errors).toEqual([]);

    // Boolean value
    errors = validateAgainstSchema(schema, true);
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/does not match any allowed schema/i);
  });

  it("realistic composite case: valid ManualTool value returns []", () => {
    const schema: JsonSchemaLike = {
      type: "object",
      required: ["name", "description", "api"],
      properties: {
        name: { type: "string" },
        description: { type: "string" },
        api: {
          type: "object",
          required: ["method"],
          properties: {
            method: { type: "string", enum: ["GET", "POST", "PUT", "PATCH", "DELETE"] },
          },
        },
      },
    };
    const errors = validateAgainstSchema(schema, {
      name: "get_weather",
      description: "Gets weather",
      api: { method: "GET" },
    });
    expect(errors).toEqual([]);
  });

  it("realistic composite case: invalid value with out-of-enum method returns exactly one error containing 'must be one of'", () => {
    const schema: JsonSchemaLike = {
      type: "object",
      required: ["name", "description", "api"],
      properties: {
        name: { type: "string" },
        description: { type: "string" },
        api: {
          type: "object",
          required: ["method"],
          properties: {
            method: { type: "string", enum: ["GET", "POST", "PUT", "PATCH", "DELETE"] },
          },
        },
      },
    };
    const errors = validateAgainstSchema(schema, {
      name: "get_weather",
      description: "Gets weather",
      api: { method: "TRACE" },
    });
    expect(errors.length).toBe(1);
    expect(errors[0]).toMatch(/must be one of/i);
  });
});