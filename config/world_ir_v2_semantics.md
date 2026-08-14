# World IR V2 semantic guidance

This file explains **how to choose between legal V2 structures**. The JSON spec defines what is structurally valid; these rules define the intended semantics.

## 0. Primitive classification: choose the semantic role before encoding placement

Choose the Primitive from the **world role of the thing**, not from its physical size or how many words describe it.

- `Region`: a spatially extended place, environment, settlement, or functional area. A Region can naturally contain or spatially organize other world objects.
  - Typical examples: `forest`, `coast`, `town`, `village`, `graveyard`, `district`, `field`, `swamp`.
  - “小村庄” is still a `Region`: `small` changes scale, not semantic role.
- `Entity`: one discrete, semantically important object, building, structure, or landmark.
  - Typical examples: `church`, `lighthouse`, `tower`, `bridge`, `radio_tower`, `gas_station`.
  - “小教堂” is still an `Entity`: `small` does not turn a discrete building into an area.
- `Distribution`: repeated instances of the same semantic object type considered collectively.
  - Typical examples: houses, trees, tombstones, lamps.
- `Network`: a connectivity structure such as a road or path.

When Region vs Entity is ambiguous, use the user's intended **spatial role**:

- if it is described as a place/area that other things can be `inside`, prefer `Region`;
- if it is a single object/landmark that is placed at or near a place, prefer `Entity`.

Do not choose `Entity` merely because a Region is described as small, local, isolated, or unnamed.

Examples:

- “在森林南边增加一个小村庄” → add a `Region` village with `direction_of(target=forest, direction=south)`.
- “在村庄里增加一个小教堂” → add an `Entity` church with `inside(target=village)`.
- “墓地作为一个区域，里面稀疏分布墓碑” → graveyard is a `Region`; tombstones are a `Distribution`.

## 1. `placement`: absolute world anchor vs object-relative relation

- `placement.anchor` is **world-relative**. It places an object in a coarse part of the whole world.
  - “北边有一个教堂” → `placement.anchor = "north"`
  - “森林在西边” → `placement.anchor = "west"`
- `placement.relations[].direction_of` is **object-relative**. Use it only when the direction is explicitly relative to a target object.
  - “村庄在森林南边” → `direction_of(target="forest", direction="south")`
  - “教堂在道路北边” → `direction_of(target="road", direction="north")`
- Do **not** replace a world-relative anchor with `direction_of` merely because a nearby object exists in the same sentence.
  - “北边靠近道路有一个教堂” means `anchor=north` **and** `near road`, not `direction_of road north`.
- If the user explicitly gives both an absolute and a relative placement fact, preserve both. Placement relations are conjunctive unless the language says otherwise.

## 2. Shared placement relations

`inside`, `near`, `far_from`, `along`, and `direction_of` describe spatial facts between world objects.

- `inside`: source is contained by the target Region.
- `near`: source should be spatially close to target; exact threshold is backend-defined.
- `far_from`: source should maintain clear spatial separation from target; exact threshold is backend-defined.
- `along`: source is placed/distributed along a Network.
- `direction_of`: source is in a qualitative direction relative to target.

Important:

- Missing a relation means **unspecified**, not its logical opposite.
  - removing `near` does not mean `far_from`.
- Do not invent numeric distance semantics. “至少 50 米” is not faithfully representable unless the active schema explicitly provides a numeric-distance field.
- Do not approximate an unsupported relation such as `between` using unrelated legal fields.

## 3. `topology`: Network connectivity only

`Network.topology` answers how a road/path connects through the world. It is not a generic placement block.

- “道路从南到北” → `topology.from="south", topology.to="north"`
- “道路先经过教堂再到北边” → put the church id in ordered `topology.via`.
- Spatial facts about the Network itself, if needed, belong in optional `placement`, not in `topology`.

## 4. `population.amount`: one authoritative amount description

For a Distribution, `population.amount` describes overall amount using exactly one mode:

- exact quantity → `{"mode":"count","value":12}`
- qualitative overall density → `{"mode":"density","value":"low|medium|high"}`

Do not encode the same amount twice. If the user says “12 栋房子”, prefer count. If the user only says “稀疏的树木”, prefer qualitative density.

## 5. `population.arrangement`: how instances are arranged relative to each other

Arrangement is orthogonal to placement and amount.

- `uniform`: approximately even spacing; not necessarily a literal grid.
- `random`: irregular stochastic placement without strong grouping.
- `clustered`: local groups separated by relatively sparse space.
- Omitted arrangement means **unspecified**, not `uniform`.

Do not put world-relative or object-relative location information into arrangement.

High-level style words such as “自然”“荒凉”“人工感” are not themselves IR enum values. Lower them only when the user wording gives a clear executable spatial meaning. For example, “树木明显聚成几团” directly supports `clustered`; merely saying “自然” does not justify inventing every possible naturalistic feature.

## 6. `population.density_profile`: how density varies across space

`density_profile` is different from arrangement:

- arrangement: instance-to-instance layout pattern
- density profile: density changes as a function of spatial position

A Distribution may therefore be both `clustered` and have a `gradient` density profile.

For V2 `gradient`:

- `from` and `to` are qualitative gradient endpoints.
- Each endpoint uses a `selector` to identify a spatial locus and a qualitative density value.
- Selector `anchor` is world-relative **but is resolved inside the current Distribution placement domain**.
  - If trees are `inside forest`, “越往森林西侧越密” should use an endpoint such as `selector={"type":"anchor","value":"west"}`. This means the world-west side of the forest-constrained Distribution domain.
  - Do **not** encode “森林西侧” as `direction_of(target="forest", direction="west")`; that means a locus west of the forest, not the western interior of the forest.
- Selector `near` / `far_from` / `direction_of` is target-relative.
  - “靠近道路稀疏，离道路越远越密” can use near-road low → far-from-road high.
- Exact interpolation and coordinates are backend-defined.

Density specification has one additional rule:

- `amount.mode=count` may coexist with `density_profile`; count is a total instance budget while the profile determines how that budget is distributed spatially.
- `amount.mode=density` and `density_profile` are **mutually exclusive**. Qualitative amount density means one uniform/global density specification; a density profile means density varies across space.
- Therefore, when a user edits an existing `amount={"mode":"density","value":"high"}` distribution into “near the road low, toward the west high”, replacing the old density amount with `density_profile` is the requested density modification. It is **not** an unrelated deletion and must not be rejected as a preservation violation.

## 7. Preservation during edits

When the user says an existing object should not move or should remain unchanged, preserve the existing semantic facts that materially define it, not merely one syntactic field.

For example, if a church currently has both `anchor=north` and `near road`, “不要移动教堂” normally means preserve both unless the user explicitly changes one of them.

Do not add optional fields that are absent from the current state merely because the schema allows them.

## 8. IR gap discipline

A structurally legal encoding is not enough; it must preserve the essential user meaning.

If the active V2 schema cannot express an essential constraint, report an IR capability gap rather than silently weakening it. Typical examples under the current V2 schema include exact metric distance and general `between` relations.
