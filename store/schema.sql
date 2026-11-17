-- Reconnect v0 —— 三元组存储骨架
-- 设计依据：docs/EXECUTION.md §3 数据契约、§4 写入协议
--
-- 约定：
--   * 所有时间均为 UTC ISO 8601 字符串 `YYYY-MM-DDTHH:MM:SSZ`，
--     定长、字典序 = 时间序，可直接用 < <= > 比较。
--   * triple 是"哑存储"：只负责持久化与过滤，不做任何推理。
--   * 软删除用 retracted_at，历史永不物理删除（D10）。
--   * 双时态：valid_from/valid_to（世界时间）+ asserted_at（断言时间）。
--   * unit_id 标识私有信念的归属；NULL = 客观世界。这是对 §3.2 的扩展，
--     因为 §3.2 的 source=derived:<op> 无法回答"这是谁的信念"。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 3.1 Entity -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL CHECK (type IN ('Unit','Person','Place','Object','Concept','Time')),
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  merged_into TEXT REFERENCES entity(id),
  merged_at   TEXT
);

CREATE TABLE IF NOT EXISTS entity_alias (
  entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  alias     TEXT NOT NULL,
  norm      TEXT NOT NULL,
  PRIMARY KEY (entity_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_alias_norm    ON entity_alias(norm);
CREATE INDEX IF NOT EXISTS idx_entity_merged ON entity(merged_into);

-- 3.3 Event ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event (
  id            TEXT PRIMARY KEY,
  time          TEXT NOT NULL,
  day_index     INTEGER,                                  -- 便捷列：便于 inspect day N
  type          TEXT NOT NULL CHECK (type IN ('occurrence','state_change','message','god')),
  occurs        TEXT NOT NULL,
  roles         TEXT NOT NULL DEFAULT '{}',               -- JSON：n-ary 角色
  delta         TEXT NOT NULL DEFAULT '{"add":[],"retract":[]}',  -- JSON
  causal_parent TEXT REFERENCES event(id),
  source        TEXT NOT NULL,
  raw_text      TEXT,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_time   ON event(time);
CREATE INDEX IF NOT EXISTS idx_event_day    ON event(day_index);
CREATE INDEX IF NOT EXISTS idx_event_parent ON event(causal_parent);

-- 3.2 Triple / Holding -------------------------------------------------------
CREATE TABLE IF NOT EXISTS triple (
  id            TEXT PRIMARY KEY,
  s             TEXT NOT NULL REFERENCES entity(id),
  p             TEXT NOT NULL,
  o             TEXT NOT NULL,
  o_kind        TEXT NOT NULL DEFAULT 'entity' CHECK (o_kind IN ('entity','literal')),
  unit_id       TEXT REFERENCES entity(id),               -- NULL = 客观世界
  event_id      TEXT REFERENCES event(id),
  source        TEXT NOT NULL,                            -- world | unit:<id> | god | derived:<op>
  confidence    REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  salience      REAL NOT NULL DEFAULT 1.0 CHECK (salience  >= 0 AND salience  <= 1),
  valid_from    TEXT NOT NULL,
  valid_to      TEXT,
  asserted_at   TEXT NOT NULL,
  retracted_at  TEXT,                                     -- 软删时刻；NULL = 仍被相信
  superseded_by TEXT REFERENCES triple(id),               -- revise 生成的新版本
  supersedes    TEXT REFERENCES triple(id),               -- 本版本替代的旧版本
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS idx_triple_spo   ON triple(s, p, o);
CREATE INDEX IF NOT EXISTS idx_triple_po    ON triple(p, o);
CREATE INDEX IF NOT EXISTS idx_triple_event ON triple(event_id);
CREATE INDEX IF NOT EXISTS idx_triple_unit  ON triple(unit_id, asserted_at);
CREATE INDEX IF NOT EXISTS idx_triple_asof  ON triple(asserted_at, retracted_at);
CREATE INDEX IF NOT EXISTS idx_triple_valid ON triple(valid_from, valid_to);

-- 3.4 Perception -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perception (
  id          TEXT PRIMARY KEY,
  event_id    TEXT NOT NULL REFERENCES event(id),
  unit_id     TEXT NOT NULL REFERENCES entity(id),
  attended    TEXT NOT NULL DEFAULT '{}',                 -- JSON: entity_id -> salience
  transforms  TEXT NOT NULL DEFAULT '[]',                 -- JSON: 加重/删减/改写/推断
  affect      TEXT NOT NULL DEFAULT '{}',                 -- JSON: {label, delta, reason}
  confidence  REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  asserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perception_event ON perception(event_id);
CREATE INDEX IF NOT EXISTS idx_perception_unit  ON perception(unit_id, asserted_at);

-- 3.5 Diary ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diary (
  id             TEXT PRIMARY KEY,
  unit_id        TEXT NOT NULL REFERENCES entity(id),
  day_index      INTEGER,
  period_from    TEXT NOT NULL,
  period_to      TEXT NOT NULL,
  perception_ids TEXT NOT NULL DEFAULT '[]',              -- JSON
  text           TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  CHECK (period_to >= period_from)
);

CREATE INDEX IF NOT EXISTS idx_diary_unit_day ON diary(unit_id, day_index);

-- 3.7 Memory -----------------------------------------------------------------
-- 记忆不是新表里的事实，而是"对某条 triple 的分类"。
CREATE TABLE IF NOT EXISTS memory (
  triple_id TEXT PRIMARY KEY REFERENCES triple(id),
  kind      TEXT NOT NULL CHECK (kind IN ('imprint','semantic','grey')),
  decay_rate REAL,
  updated_at TEXT NOT NULL
);

-- 修订审计：revise 的完整历史 ------------------------------------------------
CREATE TABLE IF NOT EXISTS revision (
  old_id TEXT NOT NULL,
  new_id TEXT NOT NULL,
  reason TEXT,
  at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revision_old ON revision(old_id);

-- D11：全量记录 LLM 调用 ------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_call (
  id       TEXT PRIMARY KEY,
  at       TEXT NOT NULL,
  purpose  TEXT NOT NULL,
  model    TEXT,
  unit_id  TEXT,
  event_id TEXT,
  prompt   TEXT,
  response TEXT,
  meta     TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_call_at ON llm_call(at);
