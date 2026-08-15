# World IR V2 完整规则参考

本文档描述 `worldir-agent-compiler-server-dev` 当前实际支持的 World IR 规则。

它同时覆盖四个层次：

1. World IR V2 的结构规则；
2. World Catalog V1 的受控词表；
3. Compiler 的语义选择与 canonicalization 规则；
4. deterministic validator 与 Semantic Judge 实际检查的边界。

实现的权威来源是：

- `config/world_ir_v2.json`：World IR V2 结构契约；
- `config/world_ir_v2_semantics.md`：语义选择规则；
- `config/world_catalog_v1.json`：World Catalog V1 受控词表；
- `worldir_agent/schema.py`：确定性结构、引用和类型校验；
- `worldir_agent/workflow.py`：Compiler canonicalization 与工作流行为。

如果本文档与实现出现冲突，应以这些机器可读契约和当前代码为准，并同步修正文档。

## 目录

1. 版本边界
2. World IR 根结构
3. 四类 Primitive
4. World Catalog V1
5. Placement
6. Network Topology
7. Population
8. Semantic Completion
9. Compiler Distribution canonicalization
10. 编辑保存规则
11. Deterministic validation
12. IR capability gap
13. 完整示例
14. V1 到 V2 的字段迁移
15. 明确不属于 World IR 的内容

## 1. 版本边界

当前相关版本为：

| 契约 | 版本 | 责任 |
|---|---:|---|
| World IR | `2` | 世界语义结构与可表达字段 |
| World Catalog | `1` | 当前 Backend/Compiler 可使用的 object `type` 词表 |
| Runtime Context | `1` | 当前运行时事实，独立于 World IR |
| Compile Result | `1` | HTTP 编译结果 envelope |

World IR V2 与 World Catalog V1 是独立概念。Catalog vocabulary 不属于 World IR V2 本体；V2 只引用当前启用的 Catalog。Server 的 `/info` 分别返回：

```json
{
  "compiler_version": "0.3.0",
  "world_ir_version": "2",
  "world_catalog_version": "1",
  "runtime_context_version": "1",
  "compile_result_version": "1"
}
```

Runtime Binding、Runtime Fact Operation 和 Runtime Context 不是 World IR object，不在本文的 IR 字段中展开。

## 2. World IR 根结构

一个完整 World IR 文档必须是 JSON object，并且必须恰好包含以下四个根字段：

```json
{
  "regions": [],
  "networks": [],
  "entities": [],
  "distributions": []
}
```

规则：

- 四个根字段全部必需；
- 每个根字段都必须是 array；
- 不允许额外的根字段；
- 每个数组元素都必须是对应 Primitive 的 object；
- object 不允许出现对应 Primitive 未声明的字段；
- 所有 World IR object 的 `id` 在整个文档内全局唯一；
- `id` 必须是去除空白后仍非空的 string；
- 当前没有进一步强制复杂的 ID 命名格式，但建议使用稳定、描述性的 `snake_case`；
- `type` 必须属于当前 World Catalog 中对应 Primitive 的词表。

ID 只是引用标识，不是额外的语义表达通道。例如：

```json
{"id": "abandoned_town", "type": "town"}
```

并不能表达“小镇处于废弃状态”；如果没有正式字段承载该含义，它仍然是 IR capability gap。

## 3. 四类 Primitive

### 3.1 Region

Region 表示空间上延展的地点、环境、聚落或功能区域。

结构：

```json
{
  "id": "forest",
  "type": "forest",
  "placement": {
    "anchor": "west"
  }
}
```

字段：

| 字段 | 必需 | 规则 |
|---|---:|---|
| `id` | 是 | 全局唯一的非空 string |
| `type` | 是 | World Catalog V1 的 Region type |
| `placement` | 否 | Placement object |

不要因为 Region 很小、孤立或没有名字就把它编码成 Entity。只要用户把它描述成可以容纳或组织其他对象的地点/区域，就优先使用 Region。

### 3.2 Network

Network 表示连接结构，目前支持道路和路径。

结构：

```json
{
  "id": "main_road",
  "type": "road",
  "topology": {
    "from": "south",
    "to": "north",
    "via": ["town"]
  },
  "placement": {
    "relations": [
      {"type": "inside", "target": "town"}
    ]
  }
}
```

字段：

| 字段 | 必需 | 规则 |
|---|---:|---|
| `id` | 是 | 全局唯一的非空 string |
| `type` | 是 | `road` 或 `path` |
| `topology` | 是 | Topology object |
| `placement` | 否 | Placement object |

`topology` 描述网络如何连接；`placement` 描述网络位于哪里。两者不能混用。

### 3.3 Entity

Entity 表示单个、离散且具有独立语义重要性的物体、建筑、结构或地标。

结构：

```json
{
  "id": "church",
  "type": "church",
  "placement": {
    "anchor": "north",
    "relations": [
      {"type": "near", "target": "main_road"}
    ]
  }
}
```

字段：

| 字段 | 必需 | 规则 |
|---|---:|---|
| `id` | 是 | 全局唯一的非空 string |
| `type` | 是 | World Catalog V1 的 Entity type |
| `placement` | 否 | Placement object |

### 3.4 Distribution

Distribution 表示同一种语义对象的重复实例集合，例如房屋、树木或墓碑。

结构：

```json
{
  "id": "houses",
  "type": "house",
  "placement": {
    "relations": [
      {"type": "inside", "target": "town"},
      {"type": "along", "target": "main_road"}
    ]
  },
  "population": {
    "amount": {"mode": "density", "value": "medium"}
  }
}
```

字段：

| 字段 | 必需 | 规则 |
|---|---:|---|
| `id` | 是 | 全局唯一的非空 string |
| `type` | 是 | World Catalog V1 的 Distribution type |
| `placement` | 否 | Placement object |
| `population` | 否 | Population object |

结构层仍允许省略 `population` 或 `population.amount`。但 Compiler 新创建的 Distribution 通常会输出显式 amount，详见第 9 节。

## 4. World Catalog V1

Catalog 是当前可生成 object type 的唯一机器可读来源。类型使用单数 `snake_case`。同义词和描述性短语可以规范化到现有 type，但不能通过拼接形容词发明新 type。

例如：

- “树林”可以规范化为 `forest`；
- “小村庄”仍是 `village`，`small` 不会生成新 type；
- “废弃海边小镇”不能编码成 `abandoned_seaside_town`；
- 如果 `abandoned` 是必要含义而当前字段无法承载，应返回 IR gap。

### 4.1 Region types

| type | Catalog roles |
|---|---|
| `town` | `settlement`, `composite_place` |
| `village` | `settlement`, `composite_place` |
| `forest` | `natural_area`, `vegetation_domain`, `composite_place` |
| `coast` | `natural_area`, `shoreline`, `composite_place` |
| `graveyard` | `functional_area`, `burial_place`, `composite_place` |
| `district` | `settlement_area`, `composite_place` |
| `field` | `natural_area`, `open_land`, `composite_place` |
| `swamp` | `natural_area`, `wetland`, `composite_place` |

### 4.2 Network types

| type | Catalog roles |
|---|---|
| `road` | `transport_network` |
| `path` | `transport_network` |

### 4.3 Entity types

| type | Catalog roles |
|---|---|
| `church` | `building`, `landmark` |
| `lighthouse` | `building`, `landmark` |
| `tower` | `structure`, `landmark` |
| `bridge` | `structure`, `crossing` |
| `radio_tower` | `structure`, `landmark` |
| `gas_station` | `building`, `service` |

### 4.4 Distribution types

| type | Catalog roles |
|---|---|
| `house` | `building`, `habitation`, `repeated_constituent` |
| `tree` | `vegetation`, `repeated_constituent` |
| `tombstone` | `burial_marker`, `repeated_constituent` |
| `lamp` | `street_furniture`, `repeated_constituent` |

Catalog role 是给 Prompt 和语义判断使用的通用元数据。Catalog 本身不保存 Region 到 constituent 的固定映射，deterministic validator 也只检查 Primitive 对应词表中的 type membership，不根据 role 自动添加对象。

## 5. Placement

Placement 回答“对象位于哪里”。

结构：

```json
{
  "anchor": "north",
  "relations": [
    {"type": "near", "target": "main_road"}
  ]
}
```

两个字段都可选，也可以同时存在：

| 字段 | 含义 |
|---|---|
| `anchor` | 相对于整个世界的粗粒度绝对位置 |
| `relations` | 相对于其他 World IR object 的关系列表 |

所有 relations 是合取语义。例如 `anchor=north` 加 `near main_road` 表示对象既在世界北部，又靠近主路。

### 5.1 Placement anchors

`placement.anchor` 允许：

```text
north
south
east
west
center
northwest
northeast
southwest
southeast
whole
```

`whole` 在 Placement 中合法，表示作用域覆盖整个世界。

绝对 anchor 与相对方向不能互换：

- “北边有教堂” → `placement.anchor="north"`；
- “教堂在道路北边” → `direction_of(target="road", direction="north")`；
- “北边靠近道路有教堂” → 同时使用 `anchor=north` 和 `near road`。

### 5.2 Placement relation 通用规则

- relation 的 `target` 必须引用文档内存在的全局 ID；
- relation 不允许声明类型之外的字段；
- 缺少某个 relation 只表示“未指定”，不表示其逻辑反面；
- `near` 缺失不等于 `far_from`；
- `near` 和 `far_from` 都是定性关系，没有数值距离阈值；
- 对称关系不需要在目标对象上重复写一条反向 relation。

### 5.3 Relation 类型表

| relation | 必需字段 | 合法 source | 合法 target | 语义 |
|---|---|---|---|---|
| `inside` | `type`, `target` | Region, Network, Entity, Distribution | Region | source 位于 target Region 内 |
| `near` | `type`, `target` | 全部 Primitive | 全部 Primitive | source 靠近 target，语义对称 |
| `far_from` | `type`, `target` | 全部 Primitive | 全部 Primitive | source 与 target 明显分离，语义对称 |
| `along` | `type`, `target` | Entity, Distribution | Network | source 位于或分布在 Network 沿线 |
| `direction_of` | `type`, `target`, `direction` | 全部 Primitive | 全部 Primitive | source 位于 target 的指定方向 |

`direction_of.direction` 允许：

```text
north
south
east
west
northwest
northeast
southwest
southeast
```

不允许 `center` 或 `whole`。

Relation 示例：

```json
{"type": "inside", "target": "forest"}
```

```json
{"type": "near", "target": "main_road"}
```

```json
{"type": "direction_of", "target": "forest", "direction": "south"}
```

## 6. Network Topology

Topology 只用于 Network，并回答网络“从哪里连接到哪里”。

结构：

```json
{
  "from": "south",
  "to": "north",
  "via": ["town", "church"]
}
```

规则：

- `from` 必需；
- `to` 必需；
- `via` 可选；
- `from` 和 `to` 可以是任一 Placement anchor，或者任一现有 World IR object ID；
- `via` 必须是有序 ID array；
- `via` 中每个 ID 必须存在；
- `topology` 不能承载 `near`、`inside` 等空间关系；这些信息放在 Network 自己的 `placement` 中。

## 7. Population

Population 回答“一个重复对象集合如何被实现”。

结构：

```json
{
  "amount": {"mode": "density", "value": "medium"},
  "arrangement": {"type": "clustered"}
}
```

可选字段：

| 字段 | 含义 |
|---|---|
| `amount` | 总数量或全局定性密度 |
| `arrangement` | 实例彼此之间的排列方式 |
| `density_profile` | 密度如何随空间位置变化 |

### 7.1 Amount

Amount 是 tagged union，只能选择一种 mode。

精确数量：

```json
{"mode": "count", "value": 12}
```

规则：

- `value` 必须是 JSON integer；
- `value >= 0`；
- boolean 不作为 integer 接受；
- 不接受字符串数字。

定性全局密度：

```json
{"mode": "density", "value": "medium"}
```

`value` 只能是：

```text
low
medium
high
```

同一个 Amount 不能同时保存 count 和 density。

### 7.2 Arrangement

结构：

```json
{"type": "clustered"}
```

`type` 只能是：

| value | 含义 |
|---|---|
| `uniform` | 大致均匀间隔，不保证严格网格 |
| `random` | 不规则、随机，且没有强分组 |
| `clustered` | 形成局部群组，群组间更稀疏 |

Arrangement 与 placement、amount、density profile 是不同维度：

- arrangement 不说明 Distribution 位于哪里；
- 省略 arrangement 表示未指定，不等于 `uniform`；
- “自然”“荒凉”“人工感”本身不是合法 arrangement enum；
- 只有用户语义明确支持时才能把描述降低为某个 arrangement。

### 7.3 Density Profile

V2 当前只支持定性连续 gradient：

```json
{
  "type": "gradient",
  "from": {
    "selector": {"type": "near", "target": "main_road"},
    "density": "low"
  },
  "to": {
    "selector": {"type": "anchor", "value": "west"},
    "density": "high"
  }
}
```

规则：

- `type` 必须是 `gradient`；
- `from` 和 `to` 都必需；
- 每个 endpoint 必须恰好包含 `selector` 和 `density`；
- endpoint `density` 只能是 `low`、`medium`、`high`；
- gradient 表达定性、连续的密度变化；具体插值、阈值、坐标和采样算法由 Backend 决定。

### 7.4 SpatialSelector

SpatialSelector 只选择 density profile 的空间位置，不是 Placement relation。

支持四种 selector：

Anchor selector：

```json
{"type": "anchor", "value": "west"}
```

允许的 value：

```text
north
south
east
west
center
northwest
northeast
southwest
southeast
```

`whole` 在 SpatialSelector 中非法，因为它不能标识 gradient 的方向性或局部 endpoint。它只在 `placement.anchor` 中合法。

Near selector：

```json
{"type": "near", "target": "main_road"}
```

Far selector：

```json
{"type": "far_from", "target": "main_road"}
```

Direction selector：

```json
{
  "type": "direction_of",
  "target": "forest",
  "direction": "west"
}
```

Selector 中所有 `target` 必须引用现有 World IR object ID。`direction` 使用与 `direction_of` relation 相同的八方向 enum。

Anchor selector 在当前 Distribution 的 placement domain 内解释。例如 trees 已经 `inside forest` 时，selector `anchor=west` 表示森林约束域内部的世界西侧。它不同于 `direction_of(target=forest, west)`；后者表示森林外部、位于森林西边的 locus。

### 7.5 Population 组合约束

| 组合 | 是否合法 | 原因 |
|---|---:|---|
| `amount.count` + `arrangement` | 是 | 总预算与排列方式正交 |
| `amount.density` + `arrangement` | 是 | 全局密度与排列方式正交 |
| `density_profile` + `arrangement` | 是 | 空间密度变化与排列方式正交 |
| `amount.count` + `density_profile` | 是 | count 可作为总实例预算 |
| `amount.density` + `density_profile` | 否 | 两者都是权威 density specification，互相冲突 |

当用户把原有均匀 density 编辑为空间 gradient 时，可以删除原 `amount.mode=density` 并改为 `density_profile`。这是对同一语义维度的替换，不是无关状态丢失。

## 8. Semantic Completion

Semantic Completion 的目的，是避免 composite Region 只剩一个不可观察的标签。当前策略是有限、保守的，不是开放式 worldbuilding。

当前默认 completion：

| 新建或直接重新解释的 Region | 最小 observable constituent |
|---|---|
| `forest` | `tree` Distribution，通常 `inside` forest |
| `town` | `house` Distribution，通常 `inside` town |
| `village` | `house` Distribution，通常 `inside` village |
| `graveyard` | `tombstone` Distribution，通常 `inside` graveyard |

这些是 Compiler semantic policy，不是 World Catalog 内的 K-V 映射，也不会由 deterministic validator 自动插入。

限制：

- 用户明确排除 constituent 时，以用户要求为准；
- “没有树的森林”不能重新补 tree；
- 不自动为 `coast` 添加 `lighthouse`；
- 不自动为 `swamp` 添加 tree 或 landmark；
- 不自动为 `field`、`district` 或其他 Region 添加无依据 constituent；
- 不添加只是合理、好看、常见或叙事上有趣的设施；
- 不进行完整场景库存式展开；
- 编辑模式只 completion 本次新建、替换或直接重新解释的 Region；
- 无关编辑不能回头展开旧 Region。

判断 constituent 是否有依据时，应问：隐藏 Region label 后，是否还需要这个最小对象集合才能直接观察出用户请求的地点类型。这个测试不能用来合理化装饰性内容。

## 9. Compiler Distribution canonicalization

World IR V2 schema 允许 Distribution 不包含 amount。Compiler 正常输出采用更严格的 canonical form，以避免 Backend 暗中选择实例数量。

对于 Compiler 新创建的 Distribution：

- 用户明确给出 count 时，保留 count；
- 用户明确给出定性 density 时，保留该 density；
- 用户未指定 amount，且没有 `density_profile` 时，补：

```json
{
  "population": {
    "amount": {
      "mode": "density",
      "value": "medium"
    }
  }
}
```

- 如果已有 `population`，则只补其中的 `amount`；
- 如果已有 `density_profile`，不补 uniform density；
- canonicalization 不修复显式 null、错误类型或其他 malformed value，这些仍由 validator 拒绝。

编辑模式通过 Distribution ID 判断对象是否为新建：

- ID 已存在于 `current_ir.distributions`：不回填缺失 amount；
- ID 不存在于 `current_ir.distributions`：按新 Distribution 处理。

因此，无关编辑不会改变 legacy/current IR 中原本缺少 amount 的 Distribution。

## 10. 编辑保存规则

Compiler 编辑完整 World IR，而不是返回局部 patch。

规则：

- 保留用户没有要求改变的 object 和字段；
- 修改既有对象时复用原 ID；
- 删除对象时不能留下断开的 relation、topology、selector 或 runtime reference；
- 保存是语义保存，不是机械字段保存；
- 当两个结构是同一维度的替代表达时，用户修改该维度可以替换旧结构；
- 不因为 schema 允许某个 optional 字段就给既有对象补字段；
- 不因本次编辑无关而重新进行旧 Region 的 semantic completion；
- Runtime Facts 默认保存，只有用户明确覆盖或恢复时才生成对应 operation；
- Runtime Binding 是一次性 lowering hint，不写进 World IR。

例如，既有 church 同时有 `anchor=north` 与 `near main_road`，用户说“不要移动教堂”时通常要保留两者，而不是只保留其中一个字段。

## 11. Deterministic validation

在进入 Semantic Judge 前，validator 会确定性检查：

1. root 必须是 object；
2. 四个 root collection 必须完整且不能有额外字段；
3. 每个 collection 必须是 array；
4. 每个 Primitive 的 required/optional 字段；
5. nested object 和 tagged union 的 required/optional 字段；
6. enum、const、非负 integer 和非空 string；
7. 全局 ID 唯一性；
8. Catalog type membership；
9. 所有 ID reference 必须存在；
10. relation source/target Primitive compatibility；
11. `amount.mode=density` 与 `density_profile` 互斥；
12. SpatialSelector anchor 不允许 `whole`。

Validator 不负责判断：

- 用户请求是否被完整表达；
- 某个合法对象是否属于无依据发明；
- composite Region 是否需要 observable constituent；
- 编辑是否错误改变了无关状态；
- 某个无法表达的含义是否应该成为 IR gap。

这些语义问题由独立上下文的 Semantic Judge 审查。

## 12. IR capability gap

“结构合法”不等于“忠实表达”。当必要含义无法用当前 World IR、Runtime Binding 和 Catalog 表达时，Compiler 应返回 `ir_gap`，不能静默弱化或伪造字段。

当前典型 gap：

- 精确米制距离，例如“森林距离海岸精确 50 米”；
- 通用 `between` 关系；
- schema 未提供的精确 spacing、坐标、尺寸或几何约束；
- Catalog 外且无法忠实规范化的对象类型；
- 必须保留、但没有正式字段承载的状态或风格，例如“废弃状态必须被保留”；
- 需要 Backend geometry、mesh、asset、collision、polygon、transform 或 node path 才能表达的要求。

不得使用以下方式伪装支持：

- 把状态或风格拼进 `type`；
- 把语义藏进 `id`；
- 用合法但不同含义的 relation 近似；
- 发明 schema 外字段或 enum；
- 用 Catalog 中“最接近”的类型替换后导致核心含义变化。

## 13. 完整示例

输入语义：西边是森林，中央有海边小镇，东边是海岸，主路从南到北，房屋沿主路分布。

```json
{
  "regions": [
    {
      "id": "town",
      "type": "town",
      "placement": {
        "anchor": "center",
        "relations": [
          {"type": "near", "target": "coast"}
        ]
      }
    },
    {
      "id": "forest",
      "type": "forest",
      "placement": {"anchor": "west"}
    },
    {
      "id": "coast",
      "type": "coast",
      "placement": {"anchor": "east"}
    }
  ],
  "networks": [
    {
      "id": "main_road",
      "type": "road",
      "topology": {
        "from": "south",
        "to": "north"
      },
      "placement": {
        "relations": [
          {"type": "inside", "target": "town"}
        ]
      }
    }
  ],
  "entities": [],
  "distributions": [
    {
      "id": "houses",
      "type": "house",
      "placement": {
        "relations": [
          {"type": "inside", "target": "town"},
          {"type": "along", "target": "main_road"}
        ]
      },
      "population": {
        "amount": {"mode": "density", "value": "medium"}
      }
    },
    {
      "id": "trees",
      "type": "tree",
      "placement": {
        "relations": [
          {"type": "inside", "target": "forest"}
        ]
      },
      "population": {
        "amount": {"mode": "density", "value": "medium"}
      }
    }
  ]
}
```

这里 town 和 forest 得到了当前 policy 要求的最小 observable realization；coast 没有自动生成 lighthouse。

## 14. V1 到 V2 的字段迁移

| V1 表达 | V2 表达 |
|---|---|
| primitive `location` | `placement.anchor` |
| primitive `relations` | `placement.relations` |
| Network `from/to/via` | `topology.from/to/via` |
| Distribution `count` | `population.amount={"mode":"count","value":...}` |
| Distribution `density` | `population.amount={"mode":"density","value":...}` |

迁移注意事项：

- 如果旧 Distribution 同时有 count 和 density，必须明确选择权威 amount，不能机械保留两者；
- 新增 `arrangement` 只在已有语义依据时使用，省略不等于 uniform；
- 明确的空间密度变化迁移到 `population.density_profile`；
- V1 的 flat fields 在 V2 Primitive 上属于 unknown fields，会被 validator 拒绝。

## 15. 明确不属于 World IR 的内容

World IR 只表达 Backend-independent 的世界语义，不直接包含：

- 坐标、旋转、缩放或 transform；
- polygon、mesh、材质或 asset path；
- collision、navigation 或物理参数；
- Godot node path、scene path 或 resource path；
- Backend 采样算法、随机种子、插值函数或实例化细节；
- Runtime Context facts；
- Runtime Binding 和 Runtime Fact Operation；
- Compiler trace、request ID 或 HTTP metadata。

这些内容由 Backend、Runtime Contract 或 Compile Result envelope 分别负责，不能通过给 World IR 增加临时字段来表达。
