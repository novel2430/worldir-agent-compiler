# WorldIR Agent Compiler — Experiment V0

这是一个刻意保持很小的实验项目，用来验证我们目前讨论出来的 **LLM Agent Workflow 形式的 World Compiler Frontend**：

```text
没有 Current State：

User Prompt
   ↓
Initial Translator
   ↓
Deterministic Validator
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
                         Local Validator + LLM Validator
                                  ↑ retry │
                                        ↓
                                       IR'
```

重点不是把它做成一个完整 Agent Framework，而是**方便反复改 Prompt、改 IR、换模型、跑 Case，然后看每个 pass 到底发生什么**。

项目目前只有 Python 标准库依赖。

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
  --prompt '做一个海边小镇，森林在西边，海岸在东边。'
```

---

## 2. 修改已有 State

```bash
python -m worldir_agent \
  --config config/config.toml \
  --state examples/state0.json \
  --prompt-file examples/prompt_edit_explicit.txt \
  --out runs/state1.json \
  --trace runs/state1.trace.json
```

也支持直接传 JSON string：

```bash
python -m worldir_agent \
  --state-json '{"regions":[],"networks":[],"entities":[],"distributions":[]}' \
  --prompt '增加一个位于北边的教堂。'
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
├── initial_translator.md
├── router.md
├── planner.md
├── planner_checker.md
├── expressibility.md
├── editor.md
└── ir_validator.md
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

所以后续我们真的决定把 World IR 从 V0 改成 V0.1 / V1 时，主要修改：

```text
config/world_ir_v0.json
prompts/*.md
```

而不是去 workflow.py 里面翻 Prompt 字符串。

---

## 7. Expressibility 是 first-class result

例如：

> 在森林南边增加一个村庄。

如果当前 Region 只有：

```text
location = north/south/east/west/...
```

而没有表达：

```text
south_of(village, forest)
```

那么正确结果允许是：

```json
{
  "status": "ir_gap",
  "detail": {
    "expressibility": {
      "expressible": false,
      "unsupported": [
        "south_of(village, forest)"
      ]
    }
  }
}
```

Workflow 不应该强迫 Planner 把它偷换成：

```text
village.location = south
```

否则 IR language 本身的问题会被 LLM 的聪明 approximation 隐藏。

---

## 8. 两个 loop

当前只做很小的 retry loop。

### Planner ↔ Checker

负责：

> “Planner 有没有正确、克制地解释用户抽象需求？”

默认最多 3 次。

### Editor ↔ Validator

负责：

> “已经确定可表达以后，Editor 有没有生成正确合法的新 IR？”

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

使用 `examples/state0.json`：

```text
Explicit:
把教堂移到西北边，房子增加到20栋，其他保持不变。

Structural:
让主路先经过教堂，再继续通向北边。

Expected IR GAP:
在森林南边增加一个小村庄，其他内容保持不变。

Abstract / compositional:
让森林有一种逐渐侵入小镇的感觉，一些树木应该开始出现在主路附近，但西边仍然是主要森林区域。

Highly abstract:
让整个世界更有“从文明逐渐走向荒野”的空间感觉，但不要改变海岸在东边、森林在西边这两个基本事实。
```

建议每次保留：

```text
最终输出
+ trace.json
+ model/config
```

这样很快就能得到一批我们后续讨论 IR V1 的真实 failure cases。

---

## 12. Test

本地单测完全不请求 API：

```bash
python -m unittest discover -s tests -v
```

当前测试包含：

- State0 schema/reference validation
- `via: "church"` 会被抓出来，要求 array
- broken reference 会被抓出来
- explicit bypass workflow
- expressibility → IR GAP 会在 Editor 前停止

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
