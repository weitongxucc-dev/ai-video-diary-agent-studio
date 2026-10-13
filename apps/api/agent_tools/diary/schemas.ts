const hostEditFields = new Set(["task_id", "workspace_id", "member_id", "user_id", "raw_command", "assistant_turn_id",
  "request_id", "expected_timeline_version_id", "expected_timeline_sequence"]);
export function modelParameters(apiSchema: any) {
  const schema = structuredClone(apiSchema);
  for (const field of hostEditFields) delete schema.properties[field];
  schema.required = (schema.required || []).filter((field: string) => !hostEditFields.has(field));
  schema.additionalProperties = false;
  return schema;
}

export function typedEditSchema(capabilities: any) {
  const schema = modelParameters(capabilities.schema);
  schema.properties.operation_type = {...schema.properties.operation_type, enum: capabilities.supported_operations};
  return schema;
}

export const isHostEditField = (field: string) => hostEditFields.has(field);
