# Reconnect 设计收敛与执行项

> 日期：2026-11-16
> 状态：设计已收敛，待执行
> 依据：[README.md](../README.md)（哲学与方程）、[BRAINSTORM.md](BRAINSTORM.md)（探索记录）
> 目的：把讨论固定为「已锁定决策 / 数据契约 / 写入协议 / 里程碑 / 开放问题」

---

## 0. 一句话定义

> **一个事件驱动的、基于三元组存储的数字生命演化引擎：世界产生客观事件，人格把事件投影为感知，感知压缩为记忆，记忆与底色合成第 t 天的 Unit。**

---

## 1. 已锁定决策（Decision Log）

| ID | 决策 | 状态 | 说明 |
|---|---|---|---|
| D1 | **事件驱动**，墙钟只做排序与背景 | 已锁定 | 事件是原子，时间只是事件的条件与排序维度 |
| D2 | **事件密度可配置**，由疏向密，密度随状态自适应 | 已锁定 | 配置是上下限/先验，不是恒定速率 |
| D3 | **事件 = 状态快照的差分** | 已锁定 | 快照是一组 Holding，事件是 Delta + 元数据 |
| D4 | **三元组只做存储**，计算与算子在外层 | 已锁定 | 存储层哑、可查询；操作层维护不变量 |
| D5 | **LLM 只做语义**：叙述、提议、规则起草 | 已锁定 | 不承担每 tick 的确定性计算 |
| D6 | **客观事件与主观感知分离** | 已锁定 | 共享客观存储 + 每个 Unit 的过滤视图 |
| D7 | **人格 = 声明性三元组 + 固定算子集** | 方向已定·细节待定 | 声明性存本体；算术在代码；算子参数可数据化 |
| D8 | **写入走统一协议** | 已锁定 | assert / retract / revise / decay / merge / query |
| D9 | **随机算子的输出必须落盘** | 已锁定 | 确定性推导可重算；随机结果带来源持久化 |
| D10 | **删除 = retract（软删）** | 已锁定 | 历史不可物理抹除 |
| D11 | **单线世界，不追求可复现** | 已锁定 | 但全量记录 prompt / response / state |
| D12 | **God view 可暂停、检查** | 已锁定 | 前提：状态结构化可读；tick 事务化 |
| D13 | **世界 = 惰性生成器 + 持久客观状态** | 方向已定·细节待定 | 只生成穿过接口的部分 |
| D14 | **NPC = 低保真 Unit（同 schema）** | 待确认 | 与 unit ∩ unit 一致，保真度可调 |
| D15 | **观察者 vs 行动者** | 开放 | v0 只做「感知 + 写日记」，schema 预留 action |
| D16 | **显式 reward** | 开放 | 默认不做（描述性，非规范性） |
| D17 | **Reconnect 的定义** | 开放 | 影响产品形态与长期叙事，见 §10 |
| D18 | **评估 = 跨生命 cohort** | 方向已定 | 单线不可 A/B，只能跑多条短命对照 |

---

## 2. 系统分层

```
┌──────────────────────────────────────────────────┐
│ 语义层    LLM: 叙述 Diary / 提议三元组 / 起草规则     │
├──────────────────────────────────────────────────┤
│ 操作层    算子: attend / appraise / decay / compress │
│           写入协议: assert / retract / revise / ...  │
├──────────────────────────────────────────────────┤
│ 存储层    三元组 + 来源 + 双时态                      │
│           视图: world / unit_i / god                 │
├──────────────────────────────────────────────────┤
│ 事件源    世界生成器 | Unit 行动 | God 干预            │
└──────────────────────────────────────────────────┘
```

**核心不变量：**

1. 所有状态变化都经由事件或算子写入，不存在静默漂移。
2. 所有写入都带来源（event_id / operator / god），可追溯。
3. 任何时刻可回答「第 N 天它相信什么」。
4. 客观层与主观层严格分离：Unit 只能读世界的过滤视图。

---

## 3. 数据契约（v0）

### 3.1 Entity（实体）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 稳定 ID，跨生命唯一 |
| type | enum | Unit / Person / Place / Object / Concept / Time |
| name | string | 显示名 |
| aliases | string[] | 别名，用于实体消解 |
| created_at | time | 创建时间 |

### 3.2 Triple（三元组 / Holding）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 三元组 ID |
| s / p / o | string | 主语 / 谓词 / 宾语（实体 ID 或字面量） |
| event_id | string? | 来源事件（provenance） |
| source | enum | world / unit:<id> / god / derived:<op> |
| confidence | float 0–1 | 置信度 |
| salience | float 0–1 | 显著性（随时间衰减） |
| valid_from / valid_to | time | 有效时间（何时成立） |
| asserted_at | time | 断言时间（何时被写入/相信） |
| status | enum | active / retracted / superseded |

**谓词规范：** 角色命名，如 `给·theme`、`给·recipient`；n-ary 事件用 `event_id` 绑定，不发明复合谓词。

### 3.3 Event（事件 / Occurrence）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 事件 ID |
| time | time | 发生时间 |
| type | enum | occurrence / state_change / message / god |
| occurs | string | 事件类型（open / give / meet / rain…） |
| roles | map | agent / theme / recipient / place / … |
| delta | object | `{add: Triple[], retract: triple_id[]}` |
| causal_parent | string? | 因果父事件 |
| source | enum | world / unit:<id> / god |
| raw_text | string? | 非结构化质感（供叙述使用） |

### 3.4 Perception（感知）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 感知 ID |
| event_id | string | 客观事件 |
| unit_id | string | 观察者 |
| attended | map | entity_id → salience（被注意的实体） |
| transforms | object[] | 施加的变换（加重/删减/改写/推断） |
| affect | object | `{label, delta, reason}` |
| confidence | float | 该感知的可信度 |
| asserted_at | time | 写入时间 |

### 3.5 Diary（日记）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 日记 ID |
| unit_id | string | 作者 |
| period | time[] | 覆盖的时间窗 |
| perception_ids | string[] | 依据的感知 |
| text | string | 日记正文 |

> Diary 原文只写不读（Unit 不直接读）；God 与评估工具可读。

### 3.6 Personality（人格槽位）

| 层 | 变化速度 | 内容 | 存储 |
|---|---|---|---|
| 底色层 | 极慢 | 价值观、依恋风格、核心恐惧、语言习惯 | 三元组 |
| 状态层 | 每天 | 情绪、精力、需求水平、身体状态 | 三元组 |
| 结构层 | 数周 | 习惯、关系账本、未了之事、自我叙事 | 三元组 |
| 算子参数 | 可调 | 衰减率、评估权重、注意阈值 | 三元组 |

### 3.7 Memory（记忆）

- 由 `compress` 产出的信念三元组 + `salience` / `decay` 状态。
- 包含三类：刻痕（永不衰减）、语义记忆（压缩后的结论）、灰记忆（正在淡出）。

### 3.8 一个手写日（示例）

```json
{
  "entities": [
    {"id": "u1", "type": "Unit", "name": "她"},
    {"id": "p1", "type": "Place", "name": "卧室"},
    {"id": "o1", "type": "Object", "name": "电脑"}
  ],
  "holdings": [
    {"id": "h1", "s": "u1", "p": "located_in", "o": "p1",
     "valid_from": "d1T19:00", "valid_to": null,
     "source": "world", "confidence": 1.0, "salience": 0.3,
     "asserted_at": "d1T19:00"}
  ],
  "event": {
    "id": "e1", "time": "d1T20:00", "type": "occurrence", "occurs": "open",
    "roles": {"agent": "u1", "theme": "o1"},
    "delta": {"add": [
      {"s": "o1", "p": "powered_on", "o": true,
       "valid_from": "d1T20:00", "valid_to": null}
    ]},
    "causal_parent": null, "source": "unit", "raw_text": "她打开电脑"
  },
  "perception": {
    "id": "pe1", "event_id": "e1", "unit_id": "u1",
    "attended": {"o1": 0.9, "u1": 0.4},
    "affect": {"label": "satisfaction", "delta": 0.4,
               "reason": "need_play 长期未满足，事件满足需求"},
    "confidence": 0.8, "asserted_at": "d1T20:00"
  },
  "memory_delta": {
    "add": [
      {"s": "u1", "p": "belief", "o": "电脑让我放松",
       "source": "derived:compress", "confidence": 0.7, "salience": 0.6}
    ]
  },
  "diary": {
    "id": "di1", "unit_id": "u1", "period": ["d1"],
    "perception_ids": ["pe1"], "text": "（由 LLM 生成）"
  }
}
```

---

## 4. 写入协议

所有写入必须经过同一组操作，不得散落 `set/get`：

```
assert(triple, provenance, confidence, valid_interval)
retract(triple_id, at_time)        // 软删除，保留历史
revise(old_id, new_triple, reason) // 生成新版本，旧版 superseded
decay(scope, rate, at_time)        // 显著性/记忆衰减
merge_entities(a, b, policy)       // 实体消解
query(pattern, as_of, scope)       // 读视图
```

**写入必须满足：**

- **来源**：每条三元组带 `event_id` / `source`。
- **双时态**：`valid_from/to` + `asserted_at`。
- **事务**：一个 event → 一次状态转移是原子的。
- **冲突**：新旧信念矛盾时走 `revise` 或保留高置信度者，不允许静默覆盖。
- **身份**：实体合并走 `merge_entities`，不允许产生两个「老板」。

---

## 5. 算子层

| 算子 | 输入 | 输出 | 性质 | 是否落盘 |
|---|---|---|---|---|
| `attend` | personality_state, event | entity → salience | 规则/可加噪 | 可重算 |
| `appraise` | personality_state, event, history | Affect | 随机 | **是** |
| `decay` | personality_state, dt | state' | 确定性 | 定期快照 |
| `compress` | personality_state, perceptions, period | memory delta | 随机 | **是** |
| `narrate` | attended, affect, event, memory | Diary text | LLM 随机 | **是** |

**评估维度（appraise 的最小集）：** 相关性、目标一致性、归因、新奇、应对能力。

**注意：** 只做「过滤」会退回 RAG；感知必须是变换（加重、删减、改写、推断）。

---

## 6. 主循环（伪代码）

```python
def step(state, now):
    # 1. 事件源
    event = world.next_event(state, now) or unit.act(state, now)

    # 2. 客观层：事务化写入
    store.apply(event, source=event.source)

    # 3. 主观层：每个有权限的 Unit 各自投影
    for u in units_with_access(event):
        attended = attend(u.state, event)
        affect   = appraise(u.state, event, u.history)
        store.assert_(Perception(event, u, attended, affect))

        if period_end(now):
            memory = compress(u.state, u.pending_perceptions)
            store.assert_(memory)
            diary = narrate(u, memory, event)
            store.assert_(diary)

    # 4. 背景演化
    state = decay(state, dt=now - state.last_time)
    return state
```

---

## 7. 里程碑（可执行项）

依赖：**M0 → M1 → M2 → M3**；M4、M5 在 M2 后可并行；M6 依赖 M3。

### M0 数据契约

- [ ] 定义 §3 全部字段与类型
- [ ] 写 `examples/day1.json`（手写一天）
- [ ] 明确谓词规范（角色命名、event_id 绑定）
- [ ] 明确「Holding vs Occurrence」的边界

**交付物：** 本文 §3、`examples/day1.json`
**验收：** 一个手写日可被读出、字段无歧义、可区分状态与事件。

### M1 存储 + 写入协议

- [ ] SQLite schema（entity / triple / event / perception / diary / memory）
- [ ] 实现 §4 六个操作
- [ ] 来源、双时态、事务
- [ ] `inspect` CLI：查看某日状态、某事件的因果链

**交付物：** `store/`、`inspect` 命令
**验收：** 单测覆盖 assert / retract / revise / as_of 查询；软删后历史仍可查；能从事件日志重放出某日状态。

### M2 单日闭环

- [ ] 手写/脚本化世界（先不接 LLM 生成世界）
- [ ] 人格 v0：3 个特质 + 2 个需求 + 1 个未了之事
- [ ] `attend` / `appraise` / `compress` v0
- [ ] LLM `narrate`
- [ ] 全链路持久化

**交付物：** `run_day`、一日完整产物
**验收：** `inspect` 能看到 event → perception → affect → memory → diary 的完整链；日记具体、有细节，不是「今天天气很好，我感到平静」。

### M3 连续运行

- [ ] 事件驱动调度器、`time_advance`
- [ ] 衰减、需求累积、未了之事、截止日期
- [ ] 密度配置（上下限 + 状态自适应）
- [ ] 退化检测（重复率、情绪锁死、主题塌缩）

**交付物：** 可无人值守运行的生命循环
**验收：** 连续跑 7 天无崩溃；无重复实体超阈值；无未处理矛盾；重复率检查通过；任意一天可 inspect。

### M4 生活接口

- [ ] 接口注册表：带宽 / 显著性 / 信任 / 主动性 / 可回应性
- [ ] 首批三个接口：手机信息流、面对面、通勤/环境
- [ ] 世界生成器按接口分辨率工作

**交付物：** `interfaces/`、世界生成器 v1
**验收：** 同一客观事件经两个接口进入，产生可测量的不同显著性/感知；日记能体现差异。

### M5 unit ∩ unit

- [ ] NPC 作为低保真 Unit（同 schema，低调用量）
- [ ] 共享客观事件 → 各自 Perception
- [ ] 双向关系账本（A 对 B、B 对 A 分开存）

**交付物：** 多 Unit 运行
**验收：** 两个 Unit 对同一事件产生不同感知并各自落盘；能演示一次误解；关系边不对称。

### M6 长跑与评估

- [ ] `replay(day)` / `diff(day_a, day_b)` / 指标
- [ ] cohort runner（同世界、不同 Personal，跑多条短命）
- [ ] 盲读协议

**交付物：** 评估工具与一次 30 天生命
**验收：** 能回答三个问题：第 N 天它相信什么？第 10→100 天变了什么？第 X 天情绪为何下降？盲读者能区分第 5 天与第 200 天；cohort 显示人格差异。

---

## 8. v0 的完成定义（DoD）

v0 = **M0–M3 全部验收通过**，具体为：

- 一个 Unit，连续运行 **7 天**
- 全部状态变化经由事件与算子
- 三元组存储，可 `as_of` 查询
- 每天产出：事件、感知、情绪、记忆增量、日记
- 任意一天可回放因果链
- 无人工补数据

**v0 成功的最低证据：** 一个不了解系统的读者，读完 7 天日记后，能说出这个人的两个性格特征。

---

## 9. 明确不在 v0 做

- 多 Unit / 社会 / 群像
- 真实天气、新闻 API
- 前端 / 可视化界面
- 向量检索 / embedding
- 完整本体工程与谓词策展
- RL / reward / 策略优化
- 分支、回档、反事实
- 模型微调

---

## 10. 开放问题（需要拍板）

| ID | 问题 | 影响 | 阻塞 | 当前默认 |
|---|---|---|---|---|
| Q1 | **Reconnect 到底指什么？** | 产品形态、长期主线 | M6 | 无默认，需作者定义 |
| Q2 | 观察者还是行动者？ | 事件 schema、循环结构 | M2 | 预留 action，v0 只感知 |
| Q3 | 是否引入显式 reward？ | 描述性 vs 规范性 | 不阻塞 v0 | 不做 |
| Q4 | NPC 保真度？ | M5 成本与架构 | M5 | 低保真 Unit，同 schema |
| Q5 | 首批生活接口选哪几个？ | 世界生成器范围 | M4 | 手机 / 面对面 / 通勤 |
| Q6 | 人格最小插槽集？ | 单日闭环表现 | M2 | 3 特质 + 2 需求 + 1 未了之事 |
| Q7 | 谓词表规模与治理策略？ | 本体漂移风险 | M1/M3 | 30–50 核心谓词 + 开放式扩展 |
| Q8 | 时间粒度起点？ | schema 与调度 | M0 | 日级 tick，事件级 schema |
| Q9 | 模型版本与风格锚点？ | 长期声音连续性 | M3 长跑前 | 固定模型版本 + 风格锚点 prompt |
| Q10 | 单生命还是 cohort？ | 评估方法 | M6 | 调参用 cohort，产品用单生命 |

---

## 11. 风险登记

| 风险 | 表现 | 缓解 |
|---|---|---|
| 本体漂移 | 数十种「在家附近」的说法 | 开放世界 + 谓词规范化 + 策展循环 |
| 实体消解失败 | 老板 / 王总 / 那个男的 重复 | 稳定 ID + merge 策略 + 定期审计 |
| 模式塌缩 | 几十天后重复、单一情绪 | 退化检测 + 熵源 + 周期性扰动 |
| 通用 LLM 腔 | 日记空泛、无细节 | 结构化骨架 + 具体名词 + 反模板约束 |
| 成本失控 | 细粒度 tick 空转 | 事件驱动 + 自适应密度 + 分层模型 |
| 模型漂移 | 换模型后声音断裂 | 固定版本 + 风格锚点 + 全量归档 |
| 范围蔓延 | 过早做多 Unit / UI | 严格遵守 §9 |

---

## 12. 术语表

| 术语 | 含义 |
|---|---|
| Unit | 数字生命个体 |
| Maze | 环境层（MacroWorld ∪ MicroEvent） |
| Diary | Unit 的主观日记，只写不读 |
| Personal | 出厂底色，极慢漂移 |
| Holding | 某段时间内成立的状态三元组 |
| Occurrence | 一次事件（时间、角色、差分、因果父） |
| Event | 状态快照的差分 + 元数据 |
| Perception | Unit 对事件的投射（有偏） |
| Belief | Unit 私有三元组，可能为真也可能为假 |
| Memory | Perception 压缩后的信念集合 |
| Operator | 作用于三元组的确定性/随机函数 |
| Interface | 世界与 Unit 之间的读写契约 |
| God | 可暂停、检查、干预的外部角色 |
