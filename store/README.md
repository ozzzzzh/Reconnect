# store/ —— 三元组存储骨架

对应 [docs/EXECUTION.md](../docs/EXECUTION.md) 的 **M0 数据契约** 与 **M1 存储 + 写入协议**。

```
store/
  schema.sql        # 建表 + 索引（契约的可执行版本）
  models.py         # Entity / Triple / Event / Perception / Diary / Memory
  protocol.py       # Store 接口（port）—— 上层只依赖它
  sqlite_store.py   # SQLite 实现（adapter）
  fixture.py        # 导入手写日 JSON
  timeutil.py       # UTC ISO 时间归一化
examples/day1.json  # M0 手写日样例
tools/seed.py       # fixture -> SQLite
tools/inspect_cli.py# 按日 / 事件 / 实体 / as_of 查看
tests/test_store.py # 11 个验收测试
```

## 快速开始

```powershell
# 导入 examples/day1.json 到 build/day1.db
python tools/seed.py --reset

# 查看第 1 天
python tools/inspect_cli.py day 1

# 查看事件的因果链
python tools/inspect_cli.py event e3

# 按别名查实体
python tools/inspect_cli.py entity 周姐

# “第 T 天它相信什么”（unit 读视图 = 世界 + 私有信念）
python tools/inspect_cli.py asof 2026-11-01T12:30:00Z --scope view:u1 --at 2026-11-01T12:30:00Z

# 测试
python -m unittest discover -s tests -t . -v
```

> 若 `python` 指向 Microsoft Store 占位符，使用安装路径：
> `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`。
> Windows 控制台建议先 `$env:PYTHONUTF8='1'` 以免中文输出乱码。

## 关键语义

### 时间
内部一律用 **UTC ISO 8601 定长字符串** `YYYY-MM-DDTHH:MM:SSZ`。字典序 = 时间序，SQL 直接比较。入口处 `norm_ts()` 归一化，带 `+08:00` 的样例会被转成 UTC。

`day_index` 是逻辑日编号，由运行器写入事件/日记，**不是**从时间戳推导的日历日。v0 不处理时区日界，等 M3 接入 `world_epoch` 再定。

### 双时态

| 字段 | 含义 |
|---|---|
| `valid_from` / `valid_to` | 世界时间：这个事实何时成立 |
| `asserted_at` | 断言时间：Unit 何时开始相信它 |
| `retracted_at` | 软删时刻：从何时起不再相信（NULL = 仍相信） |
| `superseded_by` / `supersedes` | revise 的新旧链 |

“第 N 天它相信什么” = 两个时间轴同时切片：

```sql
SELECT * FROM triple
WHERE asserted_at <= :T
  AND valid_from <= :t AND (valid_to IS NULL OR valid_to > :t)
  AND (retracted_at IS NULL OR retracted_at > :T);
```

### 写入协议
所有写入都经过 `Store` 的六个操作，上层不直接写 SQL：

```
assert_triple / retract / revise / decay / merge_entities / query
```

- `retract` 是软删，历史永远可查（`include_retracted=True`）。
- `revise` 先插入新版本，再把旧版本标记 `retracted_at + superseded_by`，并写 `revision` 审计表。
- `merge_entities` **不重写历史三元组**；查询时通过别名闭包把旧实体 ID 一起命中。这样“第 47 天它以为老板是两个人”仍然可回放。
- `decay` 跳过 `memory.kind='imprint'`（刻痕永不衰减）。

### 谓词与 Holding / Occurrence 边界

- **Holding（状态）** = 一条带有效区间的 triple，如 `u1 located_in p1`。
- **Occurrence（事件）** = `event` 行，带 `roles` 与 `delta`；它把状态从一个快照推到下一个。
- **状态变化不是三元组**，而是事件 delta 里的 `add` / `retract`。
- 谓词用 snake_case 英文关系名（`located_in`、`reports_to`、`deadline`、`has_open_loop`），v0 不做谓词治理（Q7）。
- n-ary 事件用 `roles` map 表达（`sender / recipient / theme / place / instrument`），不发明复合谓词。
- 宾语若是实体用 `o_kind="entity"`；若是值或自由文本用 `o_kind="literal"`（如 `true`、`"疲惫"`、`0.35`）。

### scope 与 unit_id

`triple.unit_id` 是**对 §3.2 的扩展**：`source=derived:<op>` 无法回答“这是谁的信念”。

| `unit_id` | 含义 | `query(scope=...)` |
|---|---|---|
| `NULL` | 客观世界 | `'world'` |
| `u1` | Unit 的私有信念 | `'unit:u1'` |
| — | 世界 + 某 Unit 的读视图 | `'view:u1'` |

### 事件原子性
`apply_event` 在一个事务里：插入 event → 应用 `delta.add` → 处理 `delta.retract`。任一条非法（未知实体、重复事件 ID）整体回滚，不会留下半个事件。

delta 里没写 `id` 的三元组会得到确定性 ID `"<event_id>:a<index>"`，因此 `replay_world()` 能从事件日志逐条重建同一状态。

## 与 EXECUTION.md §3 的偏差

| 偏差 | 原因 |
|---|---|
| `triple.unit_id` 新增 | 区分世界事实与私有信念；支撑 `view:u1` 与 M5 的 unit ∩ unit |
| `triple.o_kind` 新增（entity / literal） | 宾语可以是实体，也可以是 `true` / `"她累了"` 这类字面量 |
| `triple.retracted_at` + `supersedes` 新增 | 软删与 revise 需要可回答“何时不再相信” |
| `event.day_index` 新增 | inspect 与日记归档的便捷列 |
| `superseded_by` 用时间戳而非 bool status | `status` 可由 `retracted_at / superseded_by` 推导，少一个会漂移的字段 |
| `memory` 独立表 | 记忆是对 triple 的分类（imprint / semantic / grey），不是第二套事实 |
| `revision` / `llm_call` 表 | D9 随机算子落盘、D11 全量归档 |

## v0 明确不做（与 §9 一致）

- 向量检索 / embedding
- 谓词表治理与自动合并（目前谓词是自由文本）
- 真实世界 API
- 多 Unit 社会调度
- 前端

## 下一里程碑入口

- **M2 单日闭环**：`attend / appraise / compress / narrate` 四个算子接在 `Store` 上；`examples/day1.json` 可直接作为算子输入/期望输出的形状参考。
- **M3 连续运行**：`time_advance` 只需追加 `event` 并调用 `decay`，存储层不需要改。
