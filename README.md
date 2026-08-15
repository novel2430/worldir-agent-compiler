# WorldIR Agent Compiler — Experiment V0

这是一个刻意保持很小的实验项目，用来验证我们目前讨论出来的 **LLM Agent Workflow 形式的 World Compiler Frontend**：

```text
没有 Current State：

User Prompt
   ↓
Initial Expressibility
   ├─ NO → IR GAP
   └─ YES
       ↓
Initial Translator
   ↓
Deterministic Validator
   ↓
Independent Semantic Judge
   ↑ retry / IR GAP
   ↓
State0


有 Current State：

User Edit + Current IR
   ↓
Router
   ├─ bypass ──────────────────────────────┐
   └─ deliberate → Planner ↔ Plan Checker │
                                          ↓
                               Semantic Edit Intent
                                          ↓
                               IR Expressibility
                                  ├─ NO → IR GAP
                                  └─ YES
                                        ↓
                                     Editor
                                        ↓
                     Local Validator + Independent Judge
                                  ↑ retry / IR GAP
                                        ↓
                                       IR'
```

重点不是把它做成一个完整 Agent Framework，而是**方便反复改 Prompt、改 IR、换模型、跑 Case，然后看每个 pass 到底发生什么**。

Compiler core 保持轻量；Server V0 另外使用 FastAPI、Pydantic v2 和 Uvicorn。

---

## Server V0

Server V0 是本地 sidecar HTTP 服务，按 `docs/server_v0/LLM_COMPILER_SERVER_V0_DESIGN.md` 暴露：

```text
POST /v1/compile
GET  /health
GET  /info
```

准备配置并启动：

```bash
cp config/config.example.toml config/config.toml
export DEEPSEEK_API_KEY='你的 key'
uv sync
uv run worldir-agent-server --config config/config.toml
```

也可以直接运行模块：

```bash
uv run python -m worldir_agent.server --config config/config.toml
```

服务默认监听 `127.0.0.1:8787`。Server 使用 World IR V2、Runtime Context V1 和 Compile Result V1；它不保存 world session，调用方必须在每次编译时传入 Current World IR 与 Runtime Context。

如果希望相同 `/v1/compile` 请求直接复用结果，可以在 config 中打开持久化 request cache：

```toml
[cache]
enabled = true
dir = ".cache/worldir-compiler"
```

Cache key 由 canonical JSON request body 和 compiler fingerprint 共同生成；fingerprint 覆盖模型与 workflow 配置、Prompt、IR Schema、语义指导、Runtime 规则和 World Catalog。修改任何编译语义都会自动避开旧缓存。只缓存成功的 `ok` / `ir_gap` 结果，命中时不会进入 Compiler workflow 或调用 LLM。

---

## 1. 最快运行

需要 Python 3.11+。

### DeepSeek

```bash
cp config/config.example.toml config/config.toml
export DEEPSEEK_API_KEY='你的 key'
mkdir -p runs
```

### 生成 State0

```bash
python -m worldir_agent \
  --config config/config.toml \
  --prompt-file examples/prompt_initial.txt \
  --out runs/state0.json \
  --trace runs/state0.trace.json
```

也可以直接写 prompt：

```bash
python -m worldir_agent \
  --prompt '做一个西边的海岸森林和东边的废弃研究基地，中间有一条小路。'
```

---

## 2. 修改已有 State

```bash
python -m worldir_agent \
  --config config/config.toml \
  --state examples/state0_v2.json \
  --prompt-file examples/prompt_edit_explicit.txt \
  --out runs/state1.json \
  --trace runs/state1.trace.json
```

也支持直接传 JSON string：

```bash
python -m worldir_agent \
  --state-json '{"regions":[],"networks":[],"entities":[],"distributions":[]}' \
  --prompt '增加一片位于北边的雪森林。'
```

规则很简单：

- **没有 `--state / --state-json`** → 输入被视为“建立新世界”的 Prompt。
- **有 Current State** → 输入被视为“修改这个世界”的 Prompt。

---

## 3. Router / Planner 实验

默认：

```bash
--route auto
```

也可以人为强制路线，方便 A/B 测试：

```bash
--route bypass
--route deliberate
```

例如测试抽象描述：

```bash
python -m worldir_agent \
  --state examples/state0.json \
  --prompt-file examples/prompt_edit_abstract.txt \
  --route deliberate \
  --trace runs/abstract.trace.json
```

这可以直接比较：

```text
Prompt → Editor
```

和：

```text
Prompt → Planner → Checker → Editor
```

到底差多少。

---

## 4. Trace 是这个项目很重要的一部分

指定：

```bash
--trace runs/foo.trace.json
```

会保存每个 LLM node 的：

```text
node
attempt
最终 render 后的完整 prompt
raw_response
parsed_response
```

所以之后改：

- Prompt
- Router 规则
- Planner 规则
- Checker
- World IR Schema
- 模型

都可以直接比较 trace，而不是只看最后 JSON。

---

## 5. 所有 Prompt 都在代码外

```text
prompts/
├── common/
│   └── runtime_semantics.md
├── initial_translator.md
├── router.md
├── planner.md
├── planner_checker.md
├── expressibility.md
├── editor.md
├── semantic_judge.md
└── json_repair.md
```

Workflow code 只负责：

```text
load prompt
→ 替换 {{...}}
→ call LLM
→ parse JSON
→ route
```

以后想改 Planner 思想，不需要改 Python。

---

## 6. IR 规范也外化

目前：

```text
config/world_ir_v0.json
```

里面定义：

```text
Region
Network
Entity
Distribution
允许字段
required / optional
enum
reference 类型
```

所有 LLM Prompt 都会把这份规范注入进去。

World IR V2 还通过：

```text
config/world_catalog_v2.json
```

声明当前后端真正支持的 closed-world semantic contract。Catalog 是 Prompt 与确定性类型校验共享的唯一来源，并同时保存 canonical types、有限 aliases、`allowed_regions` compatibility 和 Region activation-time `default_realization`。语义 realization 不再依赖 ordinary-world knowledge。

当前 Region archetypes 只有 `coastal_forest`、`research_base`、`snow_forest`；Network 只有 `path`。没有 canonical type 或明确 alias 的概念返回 IR GAP，禁止 nearest-profile approximation。

World IR 与 World Catalog 独立版本化；Server `/info` 分别暴露
`world_ir_version` 与 `world_catalog_version`，Catalog vocabulary 不属于
World IR V2 本体。

所以后续我们真的决定把 World IR 从 V0 改成 V0.1 / V1 时，主要修改：

```text
config/world_ir_v0.json
prompts/*.md
```

而不是去 workflow.py 里面翻 Prompt 字符串。

---

## 7. Expressibility 是 first-class result

例如：

> 生成一个墓地。

如果 active Catalog 只有：

```text
coastal_forest / research_base / snow_forest
```

而没有 canonical `graveyard` 或明确 alias，正确结果是：

```json
{
  "status": "ir_gap",
  "detail": {
    "expressibility": {
      "expressible": false,
      "unsupported": [
        "graveyard"
      ]
    }
  }
}
```

Workflow 不应该强迫 Planner 把它偷换成：

```text
research_base
```

否则 IR language 本身的问题会被 LLM 的聪明 approximation 隐藏。

---

## 8. 两个 loop

当前只做很小的 retry loop。

### Planner ↔ Checker

负责：

> “Planner 有没有正确、克制地解释用户抽象需求？”

默认最多 3 次。

### Generator / Editor ↔ Independent Semantic Judge

负责：

> “候选 IR 是否忠实、完整、克制地实现了原始用户请求？”

Judge 使用独立 LLM 请求，只看到原始请求、Current IR、Runtime Context、候选结果和正式契约，不接收 Planner、Semantic Intent 或生成器推理。

默认最多 3 次。

次数在：

```toml
[workflow]
planner_max_attempts = 3
editor_max_attempts = 3
initial_max_attempts = 3
```

修改。

---

## 9. Provider

### OpenAI-compatible Chat Completions

默认 config：

```toml
[llm]
provider = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"
```

Client 会请求：

```text
POST {base_url}/chat/completions
Authorization: Bearer ...
```

因此 DeepSeek 或其他兼容 Chat Completions 的服务可以共用这一层。

### Anthropic Messages API

项目也放了：

```text
config/config.anthropic.example.toml
```

复制成 `config/config.toml`，填写你要测试的 Claude model 名称，再：

```bash
export ANTHROPIC_API_KEY='...'
```

即可使用 native `/v1/messages`。

Provider-specific 代码全部集中在：

```text
worldir_agent/llm.py
```

未来真要接新的 API shape，只需要继续加 adapter，不需要动 workflow。

---

## 10. Local Deterministic Validator

`worldir_agent/schema.py` 会检查：

- required / unknown fields
- 基础类型
- enum
- `via` 是否是 array
- duplicate id
- `inside / near / along / via` reference integrity
- `from / to` 是 anchor 或现有 id

它**不负责**判断自然语言有没有被正确表达。

因此我们故意把：

```text
structural legality
```

和：

```text
semantic fidelity / expressibility
```

分开。

---

## 11. 建议现在直接跑的 Case

使用 `examples/state0_v2.json`：

```text
Explicit:
在海岸森林里增加一个帐篷。

Region replacement:
把西边的海岸森林变成雪森林。

Expected IR GAP:
在雪森林里增加一艘划艇。

Default override:
生成一片雪森林，但是不要木屋。

Highly abstract:
让环境稍微更冷一点，但不要进入完整雪森林状态（当前会成为 possible IR GAP）。
```

建议每次保留：

```text
最终输出
+ trace.json
+ model/config
```

这样很快就能得到一批我们后续讨论 IR V1 的真实 failure cases。

也可以把 Server 启动后运行不含 expected IR 映射的语义回归输入集：

```bash
python scripts/run_semantic_regression.py \
  --out runs/semantic-regression.json
```

案例只保存原始请求、Current IR 与 Runtime Context；每个结果由编译流程中的独立 Semantic Judge 判定，输出文件保留实际 Compile Result 供跨模型、Prompt、Schema 和 Catalog 版本比较。

---

## 12. Test

本地单测完全不请求 API：

```bash
python -m unittest discover -s tests -v
```

当前测试包含：

- Catalog metadata/cross-reference validation
- exactly-one Region owner 与 `allowed_regions` compatibility
- Region nesting prohibition
- Catalog-driven defaults、override、replacement 与 no-regrow
- initial/edit expressibility → IR GAP

### OneAPI 真实模型配置

内部 OpenAI-compatible 入口使用 `https://oneapi.qunhequnhe.com/v1`。首次运行：

```bash
./run_oneapi_smoke.command
```

`[llm]` 中的 `thinking` 控制 OpenAI-compatible 请求的思考模式：

```toml
thinking = false # true 开启，false 关闭；删除此项则使用模型/网关默认值
```

首次运行时，终端会隐藏读取 API Key，并保存到权限为 `600`、已被 Git 忽略的 `config/oneapi.env`。再次运行测试脚本会自动复用该 Key，不再询问。其他命令可这样复用：

```bash
source config/oneapi.env
uv run worldir-agent --config config/config.oneapi.deepseek-v4-flash.toml --prompt '...'
uv run worldir-agent --config config/config.oneapi.gpt-5.6-sol.toml --prompt '...'
```

---

## 13. 当前刻意没有引入的东西

没有：

```text
LangGraph
Pydantic
FastAPI
ORM
数据库
Async Framework
Tool Calling Framework
Persistent Chat Memory
2D / Web3D / UE Backend
```

因为这一版的目标只是：

> **快速验证 Agentic Semantic Compiler Frontend 到 World IR 这段到底好不好用。**

World IR 之后的 2D / Web3D / UE 仍然保持独立 Target Backend。
