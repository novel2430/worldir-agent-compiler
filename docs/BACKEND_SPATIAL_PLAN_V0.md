# Godot ArtLab Global Spatial Plan V0

`POST /v1/backend/plan` is the deterministic boundary between World IR V2 and
the Godot ArtLab runtime. It validates the unmodified IR with the official V2
schema/catalog, then emits a versioned, backend-owned spatial plan. It never
emits Godot nodes, `res://` resources, asset paths, or local filesystem paths.

## Ownership

- World IR owns global semantic truth: Region, Network, Entity, Distribution,
  qualitative placement, topology, amount, and arrangement.
- Spatial Lowering owns canonical numeric interpretation, profile selection,
  Backend gaps, policy version, world seed, region fields, trail corridor, and
  semantic population scaling.
- Godot owns `resolve_chunk(coord)`, engine assets, terrain, environment,
  lifecycle, materialized-state protection, and rendering.

## Canonical policy `godot_artlab_spatial_v0.1`

- west/east anchor centers: `x=-96m/+96m`;
- cardinal north/south centers: `z=-96m/+96m`;
- forest extent: `112m × 96m`, coast extent: `128m × 112m`;
- region falloff: `32m`;
- logical Chunk: `48m`, transition: `16m`, compile lookahead: `24m`;
- unspecified reality: Backend-only `neutral_plain` profile;
- `forest near coast` realization: `coastal_forest`;
- west→east path: canonical continuous X-axis corridor;
- tree `low/medium/high` and arrangement remain semantic fields, with separate
  documented candidate-area and acceptance-probability scales.

Stable Chunk seeds use explicit FNV-1a 32-bit derivation over:

```text
world_seed | chunk_x | chunk_z | namespace | backend_policy_version
```

Python does not enumerate an infinite Chunk set. Godot resolves coordinates
locally from these global rules.

## Supported subset and gaps

Supported: Region `forest/coast`, Network `path`, Distribution `tree`, needed
`anchor/near/inside/direction_of`, topology, qualitative amount, and
arrangement. Other legal World IR returns `status=backend_gap`; invalid IR is
HTTP 422 with `kind=invalid_world_ir`. An IR gap remains a frontend/compiler
concern and is never silently approximated by this backend.

## Evolution State V0

`WorldEvolutionState V0` is explicitly `demo_experimental_not_world_ir_v2`.
Future Policy and World History remain independent of World IR and of Backend
Profile IDs. Future Policy is copied into the plan for unresolved Chunk
selection. Persistent snow history lowers to a `snow_forest` environment
overlay for existing rewrite and future realization. Plan revision is the
maximum monotonic evolution sequence.
