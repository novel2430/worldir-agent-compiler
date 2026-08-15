# World IR V2 + World Catalog V2 rules

## 1. Contract overview

World IR V2 keeps the same four-Primitive shape:

```json
{
  "regions": [],
  "networks": [],
  "entities": [],
  "distributions": []
}
```

The compiler now follows a closed capability path:

```text
Natural language
  -> Catalog-declared canonical type or alias
  -> finite backend-supported archetypes and objects
  -> Catalog-defined activation realization
  -> World IR V2
```

World IR remains V2 because its root shape, Primitives, placement, topology, and
population structures did not change. The semantic capability contract is World
Catalog V2.

Source files:

- `config/world_ir_v2.json`: structure and Catalog reference;
- `config/world_catalog_v2.json`: types, aliases, compatibility, and defaults;
- `config/world_ir_v2_semantics.md`: lowering and edit policy;
- `config/world_catalog_v1.json`: historical Catalog only.

## 2. Closed-world rule

The Catalog is exhaustive. A concept is supported only when the phrase is a
canonical type or an explicitly declared alias. There is no nearest-profile
fallback. `desert`, `graveyard`, `town`, `village`, `swamp`, `field`, and an
unsnowed pine forest produce an IR GAP when essential to the request.

Declared compound types are atomic semantic keys. `snow_forest` is not
`forest + snow`; arbitrary new compound types remain illegal.

## 3. Supported semantic set

### Region

```text
coastal_forest
research_base
snow_forest
```

`Region.type` denotes a complete environment archetype. Backend terrain,
materials, vegetation variants, fog, lighting, precipitation, and dressing are
selected from it and never appear as World IR fields.

Important normalizations include:

- `forest`, `temperate forest`, `森林`, `普通森林` -> `coastal_forest`;
- `snowy forest`, `winter forest`, `雪森林` -> `snow_forest`;
- `research facility`, `abandoned research base`, `研究基地` -> `research_base`.

### Network

```text
path
```

`Network.type` is structurally a string. Catalog validation is the single source
of truth for its allowed value. A path may cross Regions; the Backend changes its
rendered surface by Region.

### Entity

```text
rowboat
tent
cabin
research_station
radar_tower
radiation_warning_sign
tidal_danger_sign
cargo_truck
crate
maritime_memorial
ruined_archway
bunker
concrete_wall
```

These are semantic objects, not asset names. A `cabin` in `coastal_forest` and a
`cabin` in `snow_forest` may use different backend art.

### Distribution

```text
tree
grass
shrub
rock
fallen_log
```

These are semantic populations. Species and art variants are chosen from the
owner Region profile.

## 4. Catalog metadata

Every type has required `roles`. Primitive-specific optional keys are:

- `aliases`: exact natural-language normalization hints;
- `allowed_regions`: Region archetypes allowed to own an Entity/Distribution;
- `default_realization`: contents materialized when a Region is activated.

Defaults contain semantic `type` and Distribution `population` only. They cannot
contain IDs, placement, assets, meshes, textures, materials, terrain, lighting,
fog, coordinates, probabilities, Chunks, or Profile IDs.

`WorldCatalog` validates metadata shapes, aliases, cross-references,
compatibility, and default population. Its public helpers are:

```text
allowed_types(primitive)
metadata(primitive, type_name)
roles(primitive, type_name)
aliases(primitive, type_name)
allowed_regions(primitive, type_name)
default_realization(region_type)
canonical_type_for_alias(primitive, phrase)
```

The alias helper performs normalized exact lookup only, not fuzzy NLP matching.

## 5. Region default realization

Defaults apply only on initial creation, new Region creation, or explicit Region
type replacement/reinterpretation.

| Region | Default Entity | Default Distribution |
|---|---|---|
| `coastal_forest` | `rowboat` | `tree=high`, `grass=high`, `shrub=medium`, `rock=low` |
| `research_base` | `research_station`, `radar_tower`, `cargo_truck`, `tidal_danger_sign`, `crate` | `tree=low`, `grass=low`, `shrub=low`, `rock=medium` |
| `snow_forest` | `cabin`, `ruined_archway` | `tree=high`, `shrub=low`, `rock=high` |

`radiation_warning_sign`, `maritime_memorial`, `bunker`, and `concrete_wall` are
supported explicit content rather than mandatory defaults. Snow forest has no
default grass.

Explicit constraints override defaults. A snow forest without a cabin remains
`snow_forest`; a sparse-tree snow forest changes tree density to low. Defaults
are not invariants: deleting the cabin and later reducing rocks must not restore
the cabin.

## 6. Ownership and compatibility

Every Entity and Distribution must have exactly one owner Region:

```json
{
  "id": "tower",
  "type": "radar_tower",
  "placement": {
    "relations": [
      {"type": "inside", "target": "base"},
      {"type": "near", "target": "main_path"}
    ]
  }
}
```

Other placement facts remain allowed and conjunctive.

| Region | Entity types | Distribution types |
|---|---|---|
| `coastal_forest` | `rowboat`, `tent`, `cabin` | `tree`, `grass`, `shrub`, `rock`, `fallen_log` |
| `research_base` | `research_station`, `radar_tower`, `radiation_warning_sign`, `tidal_danger_sign`, `cargo_truck`, `crate` | `tree`, `grass`, `shrub`, `rock` |
| `snow_forest` | `cabin`, `maritime_memorial`, `ruined_archway`, `bunker`, `concrete_wall` | `tree`, `shrub`, `rock` |

Thus `rowboat inside coastal_forest` is valid, `rowboat inside snow_forest` is
invalid, and `cabin` is valid in either forest archetype. Regions cannot have
`inside`; Region nesting is unsupported. Networks need no owner Region.

## 7. Region replacement

Changing an existing coastal forest into a snow forest updates the same Region:

1. keep Region ID;
2. keep placement unless explicitly changed;
3. preserve compatible contained objects;
4. remove incompatible contained objects;
5. apply new Catalog defaults without equivalent duplicates;
6. preserve explicit quantities/arrangements unless changed;
7. apply explicit exclusions and overrides last.

Typical migration:

```text
coastal_forest: tree, grass, shrub, rock, rowboat, cabin
-> snow_forest, same id and placement
snow_forest: tree, shrub, rock, cabin, ruined_archway
```

Grass and rowboat are removed as incompatible. The compatible cabin is kept and
the missing ruined archway comes from the new default.

## 8. V2 structure retained

The narrower vocabulary still retains:

- world-relative `placement.anchor`;
- `inside`, `near`, `far_from`, `along`, and `direction_of`;
- Network `topology.from`, ordered `via`, and `to`;
- Distribution count/density amount;
- `uniform`, `random`, and `clustered` arrangement;
- qualitative density gradients.

For a new non-default Distribution with neither amount nor density profile, the
Compiler still canonicalizes amount to medium density. Existing Distributions
are not backfilled during unrelated edits.

## 9. Validator and workflow responsibilities

The deterministic validator checks schema, IDs, references, all four Primitive
type vocabularies, relation legality, exactly-one ownership, Region nesting,
`allowed_regions`, and population constraints. It never generates world content.

Translator, Editor, and Semantic Judge consume defaults as activation-time
policy. Initial generation now starts with a small Expressibility gate so an
unsupported initial concept returns `ir_gap` instead of exhausting invalid-type
retries. Edit flow remains Router -> optional Planner -> Expressibility -> Editor
-> Validator -> Judge.

## 10. Runtime and backend boundary

Runtime Context V1, Runtime Binding V1, Compile Result V1, and HTTP endpoint
shapes remain unchanged. Runtime Bindings may use one-shot `at`, `inside`, or
`near`; they cannot expand the Catalog or legalize incompatible ownership.

World IR adds no Environment, Biome, Profile, Asset, Chunk, streaming, terrain,
material, light, weather, species, mesh, or prototype fields.

## 11. Examples

- `生成一片森林` -> declared alias `coastal_forest`, then its Catalog defaults,
  each with the required Region owner.
- `把这片森林变成雪森林` -> same Region ID/placement, compatibility migration,
  then snow defaults without duplicates.
- `生成一个墓地` -> IR GAP because no canonical Region or alias exists; it is
  not mapped to `research_base`.
