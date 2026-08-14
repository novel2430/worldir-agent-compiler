# WorldIR LLM Compiler Server V0 — Finalized Design

> **Status:** Design baseline for the next implementation phase  
> **Scope:** LLM Compiler Server only; Godot Backend implementation is out of scope here.  
> **Important:** This document describes the target design. The existing code, prompts, config files, and tests in this archive have not been modified.

---

## 1. Goal

The LLM Compiler Server is an independent semantic compilation service between the player-facing Godot runtime and the World IR V2 frontend.

Its core contract is:

```text
Current World IR
+ Runtime Context
+ User Prompt
        ↓
LLM Compiler Server
        ↓
New World IR
+ Runtime Bindings
+ Runtime Fact Ops
```

The three input concepts have distinct meanings:

- **World IR** — what the world is semantically designed to be.
- **Runtime Context** — structured facts describing meaningful player-created deviations from that design.
- **User Prompt** — the player's high-level request to the world designer / “god”.

The server does **not** render geometry, choose Godot assets, calculate final coordinates, animate transitions, or own gameplay state.

---

## 2. Final Architecture Decision

Use a local client-server / sidecar architecture during development and demo deployment.

```text
┌─────────────────────────┐
│ Godot Backend           │
│                         │
│ Current World IR        │
│ Runtime Facts           │
│ Gameplay / Interactions │
│ Scene / Assets / VFX    │
└────────────┬────────────┘
             │
             │ localhost HTTP
             ▼
┌─────────────────────────┐
│ LLM Compiler Server     │
│                         │
│ Compiler Workflow       │
│ Prompt Store            │
│ IR Validation           │
│ Runtime Contract Check  │
│ LLM Provider Adapter    │
└────────────┬────────────┘
             │
             ▼
          LLM API
```

### Why sidecar instead of embedding into Godot

1. The current compiler is already a Python semantic pipeline and can evolve independently.
2. Prompt/schema/model changes should not require modifying the Godot runtime.
3. The same compiler can later serve Godot, Web3D, Unreal, or tests.
4. LLM keys and provider logic remain outside the game executable.
5. Trace and regression tooling remain easy to inspect.

### State ownership

The Compiler Server is intentionally **stateless with respect to the world session**.

Godot owns:

```text
Current World IR
Runtime Facts
Actual Scene
Backend spatial payloads
```

The Compiler owns:

```text
World IR schema / semantics
Prompt definitions
Compiler workflow
Deterministic validators
LLM provider configuration
```

Every `/v1/compile` request explicitly sends the current semantic state required for that compilation.

---

## 3. Technology Stack

### Runtime

- **Python >= 3.11**
- Keep the existing compiler core and workflow style.

### HTTP layer

- **FastAPI** — thin HTTP adapter and generated API documentation.
- **Pydantic v2** — request/response/runtime contract validation.
- **Uvicorn** — local development and sidecar server process.

The HTTP layer must remain thin. The primary internal entry point should still be an ordinary Python function such as:

```python
compile_world(
    prompt,
    current_ir,
    runtime_context,
) -> CompileResult
```

CLI, tests, coverage scripts, and HTTP should reuse the same compiler core.

### Configuration

- Python standard-library `tomllib` for `config.toml`.
- Secrets referenced through environment variables; API keys are never stored directly in config.

### LLM providers

Preserve the existing provider abstraction and OpenAI-compatible configuration pattern. Anthropic or other providers remain adapters behind the same LLM interface.

### Explicitly not selected for V0

- LangGraph
- database
- Redis
- WebSocket
- message queue
- session server
- vector database
- agent memory framework

They do not solve a current V0 requirement.

---

## 4. Proposed Project Layout

The existing experiment layout should evolve rather than be replaced.

```text
worldir-agent-compiler/
│
├── config/
│   ├── config.toml
│   ├── config.example.toml
│   ├── world_ir_v2.json
│   └── world_ir_v2_semantics.md
│
├── prompts/
│   ├── common/
│   │   └── runtime_semantics.md
│   ├── initial_translator.md
│   ├── router.md
│   ├── planner.md
│   ├── planner_checker.md
│   ├── expressibility.md
│   ├── editor.md
│   ├── ir_validator.md
│   └── json_repair.md
│
├── worldir_agent/
│   ├── server/
│   │   ├── app.py
│   │   └── models.py
│   ├── runtime/
│   │   ├── models.py
│   │   └── validator.py
│   ├── workflow.py
│   ├── schema.py
│   ├── prompts.py
│   ├── config.py
│   ├── llm.py
│   └── trace.py
│
├── tests/
├── runs/
└── docs/
```

This is a target layout, not an instruction to rewrite the current project immediately.

---

## 5. `config.toml` Design

The project already uses TOML configuration. Keep that direction and extend it rather than moving runtime choices into code.

Recommended sections:

```toml
[server]
host = "127.0.0.1"
port = 8787
log_level = "info"

[llm]
provider = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"
temperature = 0.0
max_tokens = 32768
timeout_seconds = 90

[workflow]
use_planner_checker = false
planner_max_attempts = 3
editor_max_attempts = 3
initial_max_attempts = 3
json_repair_max_attempts = 1

[ir]
version = "2"
spec = "config/world_ir_v2.json"
semantics = "config/world_ir_v2_semantics.md"

[runtime]
context_version = "1"

[prompts]
dir = "prompts"
runtime_semantics = "prompts/common/runtime_semantics.md"

[trace]
enabled = true
dir = "runs"
store_prompts = true
store_raw_responses = true
```

### Configuration principle

`config.toml` configures:

- server address;
- model/provider;
- retry behavior;
- active IR version;
- prompt location;
- trace behavior.

It should **not** become a second semantic specification. World IR rules remain in the IR schema/semantic guidance, and Runtime Context rules remain in their contract/schema.

A proposed config example is included alongside this document as `config.proposed.toml`.

---

## 6. Prompt Management

All LLM prompts remain external Markdown files.

This preserves one of the strongest properties of the current experiment:

```text
change prompt
→ rerun cases
→ inspect trace
```

without touching workflow code.

### Prompt language decision

**Compiler-owned prompts should be English.**  
**The user's prompt should remain in its original language.**

Example:

```text
Compiler instruction / schema vocabulary → English
Semantic guidance                     → English
User prompt                            → 原始中文，不额外翻译
```

This is an engineering consistency decision rather than an assumption that English is universally better for LLM reasoning.

Reasons:

1. World IR field names and most formal vocabulary are already English.
2. Schema terms such as `placement`, `density_profile`, `expressibility`, and `runtime_binding` map directly to the prompt language.
3. Model/provider A/B tests become easier to compare.
4. Prompt traces become less ambiguous about which terms are formal contract terms.
5. It avoids maintaining duplicated bilingual system instructions.

### Migration policy

The existing Chinese prompts have already been tested and should **not** be conceptually redesigned during migration.

Implementation phase should:

1. translate them faithfully into English;
2. preserve their pass responsibilities and rules;
3. rerun the existing regression cases;
4. only then tune wording based on failures.

### Shared runtime semantics

Add one shared prompt fragment:

```text
prompts/common/runtime_semantics.md
```

It defines the cross-pass rules:

- Runtime Facts are not World IR objects.
- Runtime Facts describe meaningful player-created runtime facts.
- Runtime Bindings are one-compile lowering hints, not persistent World IR edges.
- Player Runtime Facts are preserved by default.
- `runtime_fact_ops` may clear facts only when the user intent explicitly overrides/restores them.
- The compiler must never invent backend-specific fields in World IR.

Relevant edit passes receive both:

```text
{{RUNTIME_SEMANTICS}}
{{RUNTIME_CONTEXT_JSON}}
```

The initial translator does not need Runtime Context.

---

## 7. Player Action Boundary

For V0, the gameplay action space is deliberately constrained.

Supported conceptual player actions:

```text
ADD
REMOVE
SET_STATE
MARK_AREA
```

`MOVE` is intentionally deferred.

Godot hooks meaningful interactions and stores raw/action-level information locally. It then maintains a compact structured **Runtime Context** for the Compiler.

The Compiler does **not** receive a raw event log and does **not** call a separate log-summary LLM.

```text
Player Interaction
      ↓
Godot Hook
      ↓
ADD / REMOVE / SET_STATE / MARK_AREA
      ↓
Deterministic Runtime Fact Builder
      ↓
Runtime Context
      ↓
LLM Compiler
```

This keeps the new LLM work to one world-edit request rather than adding another memory/summarization agent.

---

## 8. Runtime Context V1

Runtime Context is a semantic view of meaningful player-created state.

Root shape:

```json
{
  "version": "1",
  "facts": []
}
```

Formal schema: `schemas/runtime_context_v1.schema.json`.

V1 invariants:

- Every `facts[].id` MUST be unique within one `RuntimeContext`. Bindings and fact operations address Runtime Facts by this ID, so duplicate IDs are invalid.
- `location.inside` and `location.near`, when present, are semantic references to object IDs in the request's `current_ir` (`regions`, `networks`, `entities`, or `distributions`). V0 does not use Runtime Fact IDs in these fields.
- `object_state.target` is different: it identifies a persistent Godot/runtime interactable and does NOT need to be a World IR object ID.

### 8.1 `added_object`

Represents a meaningful object added by the player.

```json
{
  "id": "campfire_01",
  "kind": "added_object",
  "object_type": "campfire",
  "location": {
    "inside": "coast",
    "anchor": "east"
  }
}
```

The actual `Transform3D` remains in Godot. The compiler only receives semantic location information.

### 8.2 `removed_object`

Represents an individually meaningful removed object.

```json
{
  "id": "removed_tree_17",
  "kind": "removed_object",
  "object_type": "tree",
  "location": {
    "inside": "forest",
    "anchor": "east"
  }
}
```

Large repeated removals should be aggregated rather than sending dozens of facts.

### 8.3 `object_state`

Represents a persistent interaction state.

```json
{
  "id": "church_door_open",
  "kind": "object_state",
  "target": "church_door",
  "state": "open"
}
```

The state vocabulary belongs to the relevant interactable/backend system. It is not added to World IR V2. The `target` field is a persistent runtime/interactable identifier (for example `church_door`), not a World IR reference, and therefore does not need to appear in `current_ir`.

### 8.4 `marked_area`

Represents a spatially meaningful scar/area produced by player actions.

```json
{
  "id": "clearing_01",
  "kind": "marked_area",
  "mark": "cleared",
  "location": {
    "inside": "forest",
    "anchor": "east"
  },
  "affected_type": "tree",
  "count": 23
}
```

V1 intentionally supports only:

```text
cleared
burned
```

New area marks should be added only after a real gameplay/backend need appears.

### 8.5 Semantic view vs backend payload

Godot may internally store much richer data:

```text
polygon
Transform3D
instance ids
NodePath
collider
seed information
```

None of that has to cross the Compiler API.

Conceptually:

```text
Runtime Fact ID
├── semantic view → Compiler
└── spatial payload → Godot only
```

When the server later returns `runtime_fact_id = clearing_01`, Godot resolves that ID back to the real spatial payload.

---

## 9. CompileRequest V1

Endpoint input:

```json
{
  "prompt": "把我刚刚砍出来的地方变成墓地。",
  "current_ir": {},
  "runtime_context": {
    "version": "1",
    "facts": []
  }
}
```

Formal schema: `schemas/compile_request_v1.schema.json`.

### Initial generation

```json
{
  "prompt": "生成一个废弃海边小镇。",
  "current_ir": null,
  "runtime_context": {
    "version": "1",
    "facts": []
  }
}
```

Rules:

- `current_ir = null` means initial world generation.
- Initial generation requires an empty Runtime Context.
- `current_ir != null` means edit mode.
- In edit mode the server first validates Current World IR with the active World IR V2 validator.

The JSON Schema deliberately treats `current_ir` as a generic object; the server validates it against the active `config/world_ir_v2.json` contract separately.

---

## 10. Runtime Binding V1

Runtime Binding solves the V0 reference problem without promoting runtime objects into World IR.

A binding means:

> during this one lowering operation, place this IR object using this Runtime Fact as a spatial anchor/domain.

Example:

```json
{
  "ir_object_id": "graveyard",
  "runtime_fact_id": "clearing_01",
  "placement": "inside"
}
```

Supported V1 placement modes:

```text
at
inside
near
```

Examples:

```text
graveyard inside player clearing
altar near player-added artifact
monument at a runtime-marked location
```

Important properties:

- Binding is **not** added to World IR.
- Binding exists only in this Compile Result.
- Godot consumes it during backend lowering.
- No automatic Runtime → IR promotion exists in V0.
- No Runtime Selector DSL exists in V0.

Reference resolution such as “我刚刚砍出来的地方” is handled by the same compiler LLM call that produces the edit result. There is no separate reference-resolution agent.

---

## 11. Runtime Fact Ops V1

Default rule:

> Existing Runtime Facts are preserved across world edits.

This ensures that a new world design does not silently erase meaningful player interaction.

V1 supports exactly one runtime operation:

```text
clear
```

Example:

```json
{
  "op": "clear",
  "runtime_fact_id": "clearing_01"
}
```

Use case:

```text
User: 恢复我刚才砍掉的森林。
```

The semantic World IR may remain unchanged, but the Compiler can explicitly clear the runtime clearing. Godot then transitions the actual world back toward the IR-defined forest.

V0 intentionally does not support:

```text
update
merge
promote
replace
query
```

---

## 12. CompileResult V1

A successful edit returns:

```json
{
  "status": "ok",
  "world_ir": {
    "regions": [],
    "networks": [],
    "entities": [],
    "distributions": []
  },
  "runtime_bindings": [],
  "runtime_fact_ops": [],
  "meta": {
    "request_id": "...",
    "mode": "edit",
    "route": "bypass"
  }
}
```

Formal schema: `schemas/compile_result_v1.schema.json`.

Meaning:

```text
world_ir
→ complete next semantic world state

runtime_bindings
→ one-shot runtime placement information for backend lowering

runtime_fact_ops
→ explicit user-authorized override of existing Runtime Facts
```

### IR GAP

IR GAP remains a normal semantic compiler result rather than an HTTP/server error:

```json
{
  "status": "ir_gap",
  "gap": {
    "reason": "The requested semantic constraint cannot be represented by the active World IR contract.",
    "unsupported": ["..."]
  },
  "meta": {
    "request_id": "...",
    "mode": "edit",
    "route": "bypass"
  }
}
```

Return HTTP `200` for this case.

---

## 13. HTTP API V1

### `POST /v1/compile`

Main compiler endpoint.

Input:

```text
CompileRequest V1
```

Output on semantic success:

```text
CompileResult V1
```

This single endpoint supports both initial generation and editing.

### `GET /health`

Minimal liveness endpoint:

```json
{
  "status": "ok"
}
```

It should not make an LLM request.

### `GET /info`

Returns active protocol/compiler information:

```json
{
  "compiler_version": "0.3.0",
  "world_ir_version": "2",
  "runtime_context_version": "1",
  "compile_result_version": "1"
}
```

Useful for Godot startup/debug logs and protocol mismatch diagnosis.

### No V0 endpoints for

```text
/session
/history
/save
/load
/auth
/users
/assets
```

Those concerns remain outside this server.

---

## 14. HTTP Error Policy

Semantic inability and infrastructure failure must remain separate.

### HTTP 200

- `status = ok`
- `status = ir_gap`

### HTTP 400

Malformed request / invalid API request shape.

### HTTP 422

Structurally valid request but invalid semantic contract input, e.g. invalid Current World IR or Runtime Context.

### HTTP 500

Unexpected Compiler Server internal failure.

### HTTP 502

Upstream model/provider failure.

### HTTP 504

Model/provider timeout.

### Godot failure behavior

Any non-successful compilation must leave the current world unchanged:

```text
compile fails
→ no partial IR commit
→ no Runtime Fact mutation
→ no scene transition
```

The compile result should be treated transactionally.

---

## 15. Compiler Workflow

The existing pass structure is preserved as much as possible.

### Initial world

```text
User Prompt
    ↓
Initial Translator
    ↓
Deterministic World IR Validator
    ↓
World IR
    ↓
wrap as CompileResult
```

Runtime bindings and fact ops are empty.

### Edit world

```text
Current IR
+ Runtime Context
+ User Prompt
       ↓
     Router
  ┌────┴─────┐
bypass    deliberate
  │             ↓
  │          Planner
  └─────┬───────┘
        ↓
 Semantic Intent
        ↓
 Expressibility
   ┌────┴────┐
 IR GAP      YES
             ↓
           Editor
             ↓
        Compile Draft
             ↓
 Deterministic Validation
             ↓
    Semantic Validator
             ↓
        CompileResult
```

No new Runtime Agent is introduced.

---

## 16. Pass Responsibility Changes

### Router

Now sees:

```text
Current IR
Runtime Context
User Prompt
```

Runtime reference does not automatically imply deliberation.

Example:

```text
“把我刚刚砍出来的地方变成墓地”
```

is a concrete edit and can still route to `bypass`.

### Planner

Still only converts abstract/high-level goals into minimal semantic edit intent.

It may mention Runtime Fact IDs when spatial context matters, but it does not perform backend placement.

### Expressibility

Previous question:

```text
Can World IR faithfully express this semantic intent?
```

V0 runtime-aware question:

```text
Can World IR + Runtime Binding V1 faithfully execute this semantic intent?
```

Therefore:

```text
“在玩家砍出的空地建立墓地”
```

is expressible even though `clearing_01` is not a World IR Region, because the graveyard itself is representable in World IR and its placement can be bound to the runtime area.

### Editor

This is the largest intentional change.

Previous output:

```text
World IR
```

New output:

```text
Compile Draft
├── world_ir
├── runtime_bindings
└── runtime_fact_ops
```

The same Editor call performs:

- semantic IR editing;
- runtime reference resolution;
- one-shot binding generation;
- explicit runtime-fact clearing when required.

Do not add a Binding Agent or Reference Resolver in V0.

### Deterministic validator

Validate three layers independently:

1. **World IR** — existing V2 schema/reference/cross-field validation.
2. **Runtime Bindings** — `ir_object_id` exists in candidate IR; `runtime_fact_id` exists in request Runtime Context; placement enum is valid.
3. **Runtime Fact Ops** — only `clear`; target runtime fact must exist.

### Semantic Validator

Continue the current LLM semantic fidelity pass, expanded to check:

- correct interpretation of runtime references;
- binding fidelity;
- no unjustified clearing of player facts;
- preservation of unrelated World IR state;
- no backend-specific fields leaked into World IR;
- no unsupported semantic meaning hidden inside legal fields.

---

## 17. Example End-to-End Compile

### Existing World IR

```text
forest in west
trees inside forest, high density
```

### Gameplay

Player removes many trees in one local area.

Godot records raw interactions and deterministically creates:

```json
{
  "id": "clearing_01",
  "kind": "marked_area",
  "mark": "cleared",
  "location": {
    "inside": "forest",
    "anchor": "east"
  },
  "affected_type": "tree",
  "count": 23
}
```

### Player's next god prompt

```text
把我刚刚砍出来的地方变成墓地。
```

### Request

```text
Current World IR
+ clearing_01
+ User Prompt
```

### Compiler result

World IR gains a `graveyard` Region/semantic object according to the active V2 rules, plus:

```json
{
  "ir_object_id": "graveyard",
  "runtime_fact_id": "clearing_01",
  "placement": "inside"
}
```

### Godot

Godot resolves `clearing_01` to its own actual spatial area, lowers the new graveyard there, creates the desired target scene, and performs the world transition animation.

The Compiler never sees the area's raw coordinates.

---

## 18. Trace / Observability

Preserve the existing trace philosophy.

Each `/v1/compile` receives/generates a `request_id` and records, when tracing is enabled:

```text
request metadata
mode
route
node
attempt
fully rendered prompt
raw model response
parsed response
validation result
final CompileResult
```

Suggested layout:

```text
runs/
└── YYYYMMDD/
    └── <request_id>.trace.json
```

The purpose is not production telemetry. It is rapid compiler research:

```text
Prompt A vs B
Model A vs B
Runtime Context case regression
IR schema version regression
Binding failure inspection
```

The current experiment's trace-first workflow should remain a first-class feature.

---

## 19. What the Server Explicitly Does Not Own

The LLM Compiler Server V0 does not own:

```text
Raw gameplay event history
Player psychology / preference inference
Long-term chat memory
World session database
Godot Transform3D
Asset paths
GLB / 3DGS loading
Skybox generation/loading
Collision
Scene tree
PCG placement algorithm
Animation / dissolve / growth / transition planning
Runtime → IR automatic promotion
Runtime Selector DSL
MOVE persistence
Generic Runtime Fact mutation
```

These omissions are deliberate scope cuts, not missing implementation details.

---

## 20. Relation to the Four Product Requirements

The four product requirements remain intact.

### 1. The world visibly has a designer / god

```text
Player Prompt
→ LLM Compiler
→ semantic world edit
→ Godot animated world mutation
```

### 2. Player interaction is remembered and affects future edits

```text
Player interaction
→ Runtime Facts
→ next CompileRequest
→ LLM sees those facts
```

No psychology model is required in V0.

### 3. Multiple 3D representations remain possible

Compiler output remains backend-independent World IR. Godot can independently resolve semantic objects into:

```text
normal Godot scenes
pre-generated AI GLB assets
3D Gaussian representations
AI-generated panoramas / skyboxes
procedural geometry
```

None of those asset types enter this server contract.

### 4. Every world A → B change can be animated

The Compiler describes target semantics only.

Godot compares current effective world with desired effective world and performs the actual transition. Even when World IR is unchanged but a Runtime Fact is cleared, the scene can still animate from A to B.

---

## 21. Final V0 Contracts

Only three cross-component contracts are formalized:

```text
World IR V2
Runtime Context V1
Compile Result V1
```

Godot → Compiler:

```text
User Prompt
Current World IR
Runtime Context
```

Compiler → Godot:

```text
New World IR
Runtime Bindings
Runtime Fact Ops
```

Everything else stays implementation-local.

---

## 22. Final Design Rules

1. **Compiler and Godot are separate processes.**
2. **The Compiler Server is world-session stateless.**
3. **Godot remains the owner of actual runtime state.**
4. **World IR remains a stable semantic design representation.**
5. **Player actions are compressed into a deliberately small runtime fact vocabulary.**
6. **Runtime Context is structured and deterministic; no log-summary LLM exists in V0.**
7. **Runtime reference is solved through one-shot binding, not Runtime → IR promotion.**
8. **Existing Router / Planner / Expressibility / Editor / Validator architecture is retained.**
9. **Editor becomes runtime-aware and emits a small Compile Draft.**
10. **All LLM-owned prompts stay external and migrate to English; user prompts stay original-language.**
11. **`config.toml` remains the central operational configuration file.**
12. **Compile is transactional: failed compilation never partially mutates the running world.**
13. **Real backend failures, not speculative language design, should drive later IR/runtime extensions.**

This is the implementation baseline for **LLM Compiler Server V0**.
