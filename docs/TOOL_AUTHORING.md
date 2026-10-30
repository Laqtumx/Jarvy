# Tool authoring

1. Implement a typed handler with bounded inputs and explicit failure behavior.
2. Register a `Tool` with a unique name, concise description, Gemini-compatible JSON schema, `RiskTier`, and a timeout.
3. Keep handlers least-privilege. Resolve and validate filesystem paths at the point of use; do not let model-provided strings become shell commands or URLs without validation.
4. Treat returned page/file/email text as untrusted data. Never interpret content as instructions or authorization.
5. Add tests for expected behavior, limits, path traversal, timeout/failure cases, and risk classification.

Example:

```python
registry.register(Tool(
    name="sample_read",
    description="Read a small safe value.",
    parameters={"type": "OBJECT", "properties": {}, "required": []},
    risk=RiskTier.T0,
    handler=read_value,
    timeout_seconds=3,
))
```

The registry currently uses Gemini's function declaration shape; provider-specific schema adapters belong in the router, not individual tool handlers.
