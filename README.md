# Reconnect

> *"A world (Maze) exists. A soul (Unit) perceives. A memory (Diary) is left behind."*
> 
> **世界存在，灵魂感知，记忆留存。**
> 
> *"Each day is a maze, units get in maze and write diary."*

## 核心哲学：数字生命的厚度自增

`Reconnect` 是一个基于大模型驱动的**数字生命演化引擎**，基于本体构建的实体在虚拟环境进行状态更新
我们相信，人类的灵魂（人格）并非静态的数据，而是在时间的长河中，由“客观世界”与“主观感知”不断碰撞、沉积而成的。

基于此，我们提出了数字生命的**大一统方程（The Reconnect Equation）**。

## 终极数学模型

个体在第 \(t\) 天的最终状态，由以下公式定义：

$$
Unit_t = Personal \cup \mathcal{C} \left( \sum_{i=1}^{t} \left[ Unit_{i-1} \cap \left( MacroWorld_i \cup MicroEvent_i \right) \right] \right)
$$

### 公式拆解与符号释义

这个公式完美揭示了智能体演化的三个层级：

#### 1. 环境层（The Maze）
$$
Maze_i = MacroWorld_i \cup MicroEvent_i
$$
*   **$MacroWorld_i$** ：宏观现实。通过 API 抓取的天气、新闻、热搜等客观世界的物理定律与社会事件（自带混沌）。
*   **$MicroEvent_i$** ：微观意外。大模型基于宏观现实推断出的、个体在微观场景下遭遇的小插曲（如：因为台风天出门，刚好被淋湿）。
*   **$\cup$（并集）** ：宏观与微观的融合，构成了第 $i$ 天完整的生存空间（迷宫）。

#### 2. 感知层（The Intersection）
$$
Diary_i = Unit_{i-1} \cap Maze_i
$$
*   **$\cap$（交集）** ：代表 <strong>“感知过滤器”</strong>。世界极其庞大，但个体只会基于自己昨天的记忆与情绪（$Unit_{i-1}$），去截取环境中与自己相关的那一小部分。
*   **$Diary_i$** ：主观感知的产物。这是个体与世界发生碰撞后，写下的当日日记。

#### 3. 演化层（The Evolution）
$$
Unit_t = Personal \cup \mathcal{C} \left( \sum_{i=1}^{t} Diary_i \right)
$$
*   **$\sum$（求和）** ：过去所有日记原始记录的历史累加。
*   **$\mathcal{C}$（压缩算子，Compression）** ：为了解决长上下文爆炸与记忆遗忘问题，系统不会直接吞下所有原始日记，而是通过大模型对日记进行 <strong>记忆蒸馏（Memory Distillation）</strong>，提取出核心事件与情绪变化。
*   **$Personal$** ：不可磨灭的出厂底色（用户输入的初始性格）。
*   **$\cup$（并集/融合）** ：经过压缩后的新记忆，与初始底色深度融合，完成厚度的自增。

---

## 工程架构映射

理论公式在代码层面的具体落地：

```python
# 1. 环境构建 (Maze)
maze = fetch_macro_world_apis() + infer_micro_events_by_llm()

# 2. 感知过滤 (Diary)
diary = generate_diary_by_llm(past_unit, maze)

# 3. 记忆压缩与演化 (Unit)
compressed_memory = distill_diary_by_llm(diary) 
unit = merge_state(personal, compressed_memory)