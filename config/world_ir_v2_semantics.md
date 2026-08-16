# World IR V2 semantic guidance

This file explains how to choose between legal V2 structures. The JSON spec
defines structural legality. The active World Catalog defines the complete
semantic capability boundary.

## 0. Closed-world capability model

World IR V2 keeps exactly four Primitives: `Region`, `Network`, `Entity`, and
`Distribution`. It is not a general ontology. The active Catalog is exhaustive:

- canonical type values are the only legal semantic object types;
- declared `aliases` are machine-readable high-confidence normalization hints;
- `allowed_regions` defines deterministic ownership compatibility;
- Region `default_realization` defines activation-time semantic contents.

The Catalog is exhaustive for compiler output, not for user input language.
Canonical names and aliases provide high-confidence normalization, while the
LLM may interpret broader, generic, indirect, or evocative wording and lower it
to a restrained composition of supported capabilities. It must not extend the
output language or use arbitrary visual similarity as a "closest type" policy.
If an essential concept still cannot be preserved after best-effort supported
lowering, report an IR GAP. In particular, do not claim to create a literal
desert, graveyard, town, village, swamp, field, or medieval settlement merely by
renaming one of the supported environment profiles.

Use exact singular `snake_case` canonical types in IR. A compound `snake_case`
type declared by the Catalog is an atomic semantic key. For example,
`snow_forest` must not be decomposed into `forest` plus a snow modifier. Models
must not invent other compound types.

### 0.1 Canonical Region archetypes

`Region.type` denotes a complete backend-supported environment archetype, not a
generic place label or a bag of biome parameters. The current set is:

- `coastal_forest`
- `research_base`
- `snow_forest`

Backend terrain, material, vegetation variants, lighting, fog, precipitation,
and visual dressing are selected from this type. None of those parameters enter
World IR.

### 0.2 Catalog-defined realization

Catalog-defined default realization replaces open-world constituent inference.
When a Region archetype is activated, materialize exactly its Catalog
`default_realization`, then apply explicit user constraints. This policy applies
only to:

1. initial world creation;
2. creation of a new Region;
3. explicit replacement or reinterpretation of an existing Region type.

It is not a continuously enforced invariant. If a user later deletes a default
object, an unrelated edit must not restore it. Do not add constituents that are
neither explicitly requested nor present in the activated archetype's default.

Explicit constraints override defaults. "A snow forest without a cabin" keeps
the `snow_forest` Region and omits its default cabin. "A snow forest with sparse
trees" keeps the archetype and changes the default tree density from high to
low. A later unrelated edit must preserve those exceptions.

Defaults carry semantic type and semantic population only. Generate unique IDs
and add ownership relations, but never copy backend asset, material, terrain,
lighting, coordinate, probability, Profile, or Chunk data into IR.

### 0.3 Region ownership and compatibility

Every `Entity` and `Distribution` must have exactly one owner Region through one
`placement.relations[].inside` relation whose target is a Region ID. It may also
have `near`, `far_from`, `along`, `direction_of`, or an anchor when those facts
are requested. The owner is still mandatory.

The source type must list the owner's Region type in Catalog `allowed_regions`.
This is deterministic. Do not ask the Semantic Judge to excuse an incompatible
pair. A `Network` needs no owner Region and may cross multiple Regions. A Region
must never have an `inside` relation; Region nesting is unsupported.

### 0.4 Open input and capability-grounded lowering

Aliases are explicit, high-confidence normalization entries and may be matched
without semantic deliberation. They are not an exhaustive whitelist of natural
language phrases. Examples under the active Catalog include:

- bare `forest` or `森林` -> `coastal_forest`;
- `snowy forest` -> `snow_forest`;
- `research facility` -> `research_base`.

When wording is not an alias, first distinguish essential constraints from
elastic or underspecified language, then search for the smallest supported
realization that preserves the operative visual, spatial, and functional intent:

- a generic object may become a compatible supported subtype when no explicit
  modifier contradicts it (`船` -> `rowboat`);
- a generic connection through a forest may become `path` (`路` -> `path`);
- evocative or functional intent may use several supported objects when every
  addition has a clear realization role rather than being plausible decoration.

This is semantic lowering, not permission to relabel anything. A requested
ferry is not a rowboat, a paved highway is not a path, and "a pine forest without
snow" is not faithfully represented by `snow_forest` or `coastal_forest` because
the explicit subtype/environment identity conflicts with both profiles.

Only essential residual meaning causes IR GAP. Missing alias text alone does not.

## 1. Primitive roles

- `Region`: one supported environment archetype occupying a semantic area.
- `Network`: a supported connectivity structure. V2 currently supports `path`.
- `Entity`: one discrete supported hero or supporting object owned by a Region.
- `Distribution`: a supported repeated semantic population owned by a Region.

Tree species, cabin model variants, truck axle variants, and similar art choices
are backend decisions based on the owner Region. World IR uses `tree`, `cabin`,
and `cargo_truck`, not art-variant types.

## 2. Region replacement

An instruction such as "change the eastern coastal forest into a snow forest"
updates the existing Region's `type`; it does not create an overlapping Region.
Apply this migration policy:

1. preserve the Region ID;
2. preserve its placement unless the user changed it;
3. inspect every contained Entity and Distribution;
4. preserve content compatible with the new Region;
5. remove content incompatible with the new Region and clean broken references;
6. apply the new Region's Catalog default realization;
7. do not duplicate an equivalent existing object;
8. preserve explicit quantities and arrangements unless the request changes
   them; use the new default only for newly added or genuinely reinterpreted
   content;
9. apply explicit inclusions, exclusions, and quantity overrides last.

Existing compatible content does not become "default-owned" by this migration.
If the user had explicitly chosen its amount, do not silently replace that
amount merely because the new archetype has another default.

## 3. `placement`: world anchor and object-relative relations

`placement.anchor` is world-relative. `direction_of` is relative to a target.

- "the cabin is in the north" -> `placement.anchor="north"` plus its mandatory
  owner `inside` relation;
- "the cabin is north of the path" ->
  `direction_of(target=<path-id>, direction="north")` plus its owner relation;
- "in the north near the path" means anchor north and `near` the path, not
  `direction_of` the path.

Relations are conjunctive. Missing a relation means unspecified, not its
opposite. Do not invent numeric distance semantics or approximate unsupported
relations such as `between`.

## 4. Network topology

`Network.topology` describes connectivity with `from`, optional ordered `via`,
and `to`. `path` may cross Region IDs through `via`; the Backend changes its
surface according to the Region it traverses. Surface and transition details do
not enter IR.

Topology is separate from placement. `from` and `to` are anchors or object IDs;
`via` is an array of object IDs.

## 5. Distribution population

`population.amount` has one authoritative mode:

- exact amount: `{"mode":"count","value":12}`;
- qualitative global density:
  `{"mode":"density","value":"low|medium|high"}`.

For a newly created Distribution without an explicit amount or density profile,
the Compiler canonical fallback remains medium density. Catalog defaults already
carry explicit amounts and are not changed by this fallback. Never backfill a
pre-existing Distribution during an unrelated edit.

Ordinary relative edits on an existing qualitative density use the nearest
available step in the requested direction: high -> medium -> low for reductions,
and low -> medium -> high for increases. Phrases such as "a little less" do not
require unsupported numeric precision. A request beyond the enum boundary, or
an explicit percentage/metric that cannot be represented, remains an IR GAP.

`population.arrangement` is orthogonal to amount:

- `uniform`: approximately even spacing;
- `random`: irregular stochastic placement;
- `clustered`: local groups separated by sparser space.

Omission means unspecified.

## 6. Density profiles

`population.density_profile` describes spatial density variation and is
orthogonal to arrangement. V2 supports a qualitative `gradient` between two
selectors.

- selector anchor is resolved within the Distribution's placement domain;
- selector `near`, `far_from`, and `direction_of` is target-relative;
- `whole` is not a gradient endpoint;
- exact interpolation is backend-defined.

`amount.mode=count` may coexist with a density profile as a total budget.
`amount.mode=density` and `density_profile` are mutually exclusive. Replacing a
global density with a requested gradient is a change to one semantic dimension,
not a preservation error.

## 7. Edit preservation

Preserve every unrelated object and semantic field. Preserve the full placement
meaning of an unchanged object, including its mandatory owner and any anchor or
additional relations. Reuse IDs when modifying objects. Removing an object also
requires cleaning references that would otherwise break.

Default realization must never cause deleted content to regrow during unrelated
edits. The only triggers are archetype creation, activation, or replacement.

## 8. IR GAP discipline

A structurally legal encoding is insufficient if it changes essential meaning,
but IR GAP is a last-resort capability result rather than a lexical rejection.
Before returning it, attempt generic-to-specific lowering, supported semantic
composition, and qualitative-axis lowering. Return IR GAP when an essential
identity, explicit subtype, compatibility combination, exact metric, or spatial
relation still cannot be represented by the active Catalog, World IR, and
Runtime Binding contracts. Runtime Binding may supply one-shot `at`, `inside`,
or `near` placement against an existing Runtime Fact, but it does not expand the
Catalog or legalize an incompatible World IR owner.
