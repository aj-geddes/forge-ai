// Best-effort, dependency-free, PARTIAL validation against a JSON-Schema-like
// object (the subset Pydantic's model_json_schema() emits: type, required,
// properties, items, enum, anyOf, and $ref/$defs). This is intentionally not
// a spec-complete JSON Schema engine -- it exists so a wizard can catch
// obvious shape mistakes before submitting, avoiding a round-trip 400 from
// the backend. Every function fails open (returns no errors / undefined)
// on anything it doesn't understand, rather than throwing or blocking a
// submit it can't confidently validate.

export interface JsonSchemaLike {
  type?: string | string[];
  required?: string[];
  properties?: Record<string, JsonSchemaLike>;
  items?: JsonSchemaLike;
  enum?: unknown[];
  $ref?: string;
  $defs?: Record<string, JsonSchemaLike>;
  anyOf?: JsonSchemaLike[];
  [key: string]: unknown;
}

function resolveRef(root: JsonSchemaLike, ref: string): JsonSchemaLike | undefined {
  if (!ref.startsWith("#/")) {
    return undefined;
  }
  
  const segments = ref.slice(2).split("/");
  let current: unknown = root;
  
  for (const segment of segments) {
    if (current === null || typeof current !== 'object') {
      return undefined;
    }
    current = (current as Record<string, unknown>)[segment];
  }
  
  return current as JsonSchemaLike;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function resolveSchemaPath(root: JsonSchemaLike, path: string[]): JsonSchemaLike | undefined {
  if (!root || !Array.isArray(path) || path.length === 0) {
    return undefined;
  }

  let current: Record<string, unknown> = root;

  for (const segment of path) {
    // If the current node has a $ref, resolve it first.
    if (typeof current.$ref === "string") {
      const resolved = resolveRef(root, current.$ref);
      if (!isRecord(resolved)) {
        return undefined;
      }
      current = resolved;
    }

    const next = current[segment];
    if (!isRecord(next)) {
      return undefined;
    }
    current = next;
  }

  // The final resolved node may itself be a dangling $ref (e.g. a Pydantic
  // `list[SomeModel]`'s `items: {"$ref": "#/$defs/SomeModel"}`) -- resolve
  // it too so callers get the actual schema, not a pointer to it.
  if (typeof current.$ref === "string") {
    const resolved = resolveRef(root, current.$ref);
    return isRecord(resolved) ? (resolved as JsonSchemaLike) : undefined;
  }

  return current as JsonSchemaLike;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function validateAgainstSchema(
  schema: JsonSchemaLike | undefined,
  value: unknown,
  root: JsonSchemaLike = schema ?? {},
  path: string = ""
): string[] {
  const errors: string[] = [];
  
  // If no schema, fail open (no errors)
  if (!schema) {
    return errors;
  }
  
  // Handle $ref
  if ('$ref' in schema && typeof schema.$ref === 'string') {
    const resolved = resolveRef(root, schema.$ref);
    if (resolved) {
      return validateAgainstSchema(resolved, value, root, path);
    }
    // If ref can't be resolved, fail open (no errors)
    return errors;
  }
  
  // Handle anyOf
  if ('anyOf' in schema && Array.isArray(schema.anyOf) && schema.anyOf.length > 0) {
    let matched = false;
    for (const subSchema of schema.anyOf) {
      const subErrors = validateAgainstSchema(subSchema, value, root, path);
      if (subErrors.length === 0) {
        matched = true;
        break;
      }
    }
    if (!matched) {
      errors.push(`${path || "value"}: does not match any allowed schema`);
    }
    return errors;
  }
  
  // Handle type validation
  if ('type' in schema) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    let typeMatched = false;
    
    for (const t of types) {
      if (typeof t !== 'string') continue;
      
      switch (t) {
        case 'string':
          typeMatched = typeof value === 'string';
          break;
        case 'number':
        case 'integer':
          typeMatched = typeof value === 'number';
          break;
        case 'boolean':
          typeMatched = typeof value === 'boolean';
          break;
        case 'array':
          typeMatched = Array.isArray(value);
          break;
        case 'object':
          typeMatched = value !== null && typeof value === 'object' && !Array.isArray(value);
          break;
        case 'null':
          typeMatched = value === null;
          break;
      }
      
      if (typeMatched) break;
    }
    
    if (!typeMatched) {
      errors.push(`${path || "value"}: expected ${types.join(" | ")}`);
      return errors;
    }
  }
  
  // Handle enum validation
  if ('enum' in schema && Array.isArray(schema.enum)) {
    let matched = false;
    for (const enumValue of schema.enum) {
      if (deepEqual(value, enumValue)) {
        matched = true;
        break;
      }
    }
    if (!matched) {
      errors.push(`${path || "value"}: must be one of ${schema.enum.map(String).join(", ")}`);
      return errors;
    }
  }
  
  // Handle object schema
  const isObjectSchema = 
    ('type' in schema && schema.type === 'object') || 
    ('properties' in schema && schema.properties !== undefined);
  
  if (isObjectSchema) {
    // Treat undefined/null as empty object for required checks
    const obj = (value !== null && typeof value === 'object' && !Array.isArray(value)) 
      ? (value as Record<string, unknown>) 
      : {};
    
    // Check required fields
    if ('required' in schema && Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (typeof key !== 'string') continue;
        
        const fullPath = path ? `${path}.${key}` : key;
        const val = obj[key];
        
        if (!(key in obj) || val === undefined || val === null || val === '') {
          errors.push(`${fullPath}: is required`);
        }
      }
    }
    
    // Validate properties
    if ('properties' in schema && schema.properties !== undefined) {
      for (const [key, subSchema] of Object.entries(schema.properties)) {
        if (key in obj) {
          const fullPath = path ? `${path}.${key}` : key;
          const subErrors = validateAgainstSchema(subSchema, obj[key], root, fullPath);
          errors.push(...subErrors);
        }
      }
    }
  }
  
  // Handle array schema
  const isArraySchema = 
    ('type' in schema && schema.type === 'array') || 
    ('items' in schema && schema.items !== undefined);
  
  if (isArraySchema && Array.isArray(value)) {
    if ('items' in schema && schema.items !== undefined) {
      for (let i = 0; i < value.length; i++) {
        const item = value[i];
        const itemPath = path ? `${path}[${i}]` : `[${i}]`;
        const itemErrors = validateAgainstSchema(schema.items, item, root, itemPath);
        errors.push(...itemErrors);
      }
    }
  }
  
  return errors;
}
