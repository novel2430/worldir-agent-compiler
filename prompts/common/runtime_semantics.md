# Runtime semantics

- Runtime Facts are not World IR objects. They describe meaningful player-created runtime facts owned by Godot.
- Runtime Bindings are one-compile lowering hints. They are never persistent World IR relations.
- Preserve existing Runtime Facts by default.
- Emit a `runtime_fact_ops` `clear` operation only when the user explicitly overrides or restores the referenced fact.
- Never add backend-specific data such as coordinates, transforms, polygons, node paths, assets, or instance IDs to World IR.
- A Runtime Fact may be used as a spatial anchor only through a Runtime Binding with placement `at`, `inside`, or `near`.
- Do not promote Runtime Facts into World IR and do not invent a Runtime selector language.
