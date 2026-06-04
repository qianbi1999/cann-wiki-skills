# MCP 工具契约详解（cann-ask 契约快照）

> 本文件是 **cann-ask 使用的契约快照**,不是 `/help` 的盲从副本。内容来自上游 `mcp-server/server.py` + `response_schema.py` + `help_doc.py` 三处汇总。
>
> **响应 shape 以 `response_schema.py`/`server.py`(实际响应)为准**;当 `wiki_help()` / `help_doc.py` 与之冲突时(例如 `help_doc.py` 仍把 `path` 列进 results),以实际响应为准 —— **`wiki_search` 的 `static`/`dynamic` results 不再含 page path;页面正文只能用 result `id` 调 `wiki_get_page(id)`**。
>
> 注意:这只针对 wiki_search 结果链路。**raw 工具(`wiki_grep_raw` 返回 `hits[].path`、`wiki_read_raw(path,…)` 入参)返回/使用的 raw file path 仍按其各自契约正常使用**,不在"下沉"范围。
>
> 响应结构与本文件描述不一致时,可调 `wiki_help()` 对照,但最终以实际响应为准。

## `wiki_search` —— 三分区知识检索（核心）

**签名**：

```
wiki_search(
  query, phase=None, tags=None, type=None, limit=10,
  intent=None, progress=None, task_description=None,
  call_count=0, seen_ids=None, device_feedback=None
) -> dict
```


**作用**：一次调用同时返回三层知识：tier0 `phase_rule`（踩坑规则，强制注入）+ tier1 `static`（静态事实，传统漏斗）+ tier2 `dynamic`（实践 recipe，单次 Agent）。

### 入参字段

| 参数 | 类型 | 必填 | 含义 | cann-ask 取值 |
|---|---|---|---|---|
| `query` | str | ✓ | 自然语言查询；空串时 static 区返回 warning | B.1 合成的 30-150 字聚焦提问 |
| `phase` | str\|None | 否 | `correctness` \| `precision` \| `performance` \| `all`；None→correctness。决定 tier0 返回哪篇规则 + 派生 tier1 Q-sort intent（correctness/precision→q1，performance→q2，all→mixed）。**禁止 `all`** —— tier0 一次返三篇正文 token 爆炸 | B.2 从 device_feedback 派生 |
| `tags` | list[str]\|None | 否 | 标签硬过滤（交集）；server 端会与 entry.tags 取交集 | 固定 `[]`（当前不启用 tag 召回） |
| `type` | str\|None | 否 | 限定 `entry.domain`（不是页面类型过滤）；全库 domain 都是 `ascendc`，传了无效 | `null`（不传） |
| `limit` | int | 否 | tier1 返回上限，截到 `min(limit, K3=10)` | 固定 `3` |
| `intent` | str\|None | 否 | `q1` \| `q2` \| `mixed` 显式覆盖；`phase` 给定时由 phase 派生覆盖。非法值 server 返回 warning | 不传（让 phase 派生） |
| `progress` | str\|None | 否 | `phase` 的兼容别名（server 端 `eff_phase = phase if phase is not None else progress`） | 不传（用 phase） |
| `task_description` | str\|None | 否 | 任务描述，**同时传给 tier1 与 tier2 Agent** 做相关性判断（越具体越能过滤无关结果） | caller 缓存的 round-1 原文（跨轮 verbatim） |
| `call_count` | int | 否 | 当前阶段已调用知识库的次数；**仅 `call_count==0` 时返回 tier0 rules 正文**，>0 时 `phase_rule.content_mode="suppressed"` 且 `content=""` | caller 维护的轮次计数器（0 起步） |
| `seen_ids` | list[str]\|None | 否 | 前几轮已召回的知识 ID；本轮 tier1+tier2 候选都剔除，避免重复召回 | caller 累积的 id 列表 |
| `device_feedback` | str\|None | 否 | 前几轮生成代码的上板报错文本（compile/precision/runtime error），注入主 Agent 上下文 | caller 已截断的原文（50-200 字关键段） |


### 返回结构

**顶层信封**（v5.2 — 主 Agent 编排,顶层 4 个诊断字段）：

```json
{
  "phase_rule": {
    "phase":             "correctness | precision | performance | all",
    "content_mode":      "inject | suppressed",       // call_count==0 → inject; >0 → suppressed
    "content":           "str",                        // 规则正文 markdown(suppressed 时为 "")
    "description":       "str",                        // ★ 强制约束告警语 —— cann-ask 把它抬到答案最顶
    "estimated_tokens":  "int",
    "rule_refs":         "list[{phase, version, full_page_id}]"
  },
  "ai_suggestion": "str | null",                       // 主 Agent 跨 tier 综述,内嵌 [id:xxx] 引用
  "agent_status":  "\"ok\" | \"degraded\" | \"disabled\"",
  "agent_turns":   "int | null",                       // 主 Agent 跑了几轮 retrieve(static)
  "sub_queries":   "list[str] | null",                 // 主 Agent 设计的所有 sub-query
  "static":  { "results": [...], "total": "int", "no_useful_results": "bool", "warning": "str | null" },
  "dynamic": { "results": [...], "total": "int", "no_useful_results": "bool", "warning": "str | null" },
  "warning":  "str (可选)  // 顶层跨层告警汇总,无告警时不出现"
}
```


**顶层诊断字段语义**（来自 `mcp-server/response_schema.py:25-28`,authoritative）：

| 字段 | 含义 |
|---|---|
| `ai_suggestion` | 主 Agent 跨 tier 合成的检索建议;**单一来源**,不再分 tier1/tier2;内嵌 `[id:xxx]` 引用可指 tier1 或 tier2 ID |
| `agent_status` | `"ok"` = 主 Agent 跑通,综述与两 tier results 都可信;`"degraded"` = 主 Agent 失败/超时,results 来自纯漏斗兜底;`"disabled"` = 本次未走 Agent（cfg 关闭 / query 为空 / phase_err / invalid intent） |
| `agent_turns` | 主 Agent 调 `retrieve(static)` 工具的轮数（`disabled` 时通常为 `None`） |
| `sub_queries` | 主 Agent 设计的所有 sub-query 列表(透明化检索路径,debug 用) |

**`phase_rule`（tier0 踩坑规则）字段**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `phase` | str | 当前阶段（`correctness`/`precision`/`performance`） |
| `content_mode` | `"inject"` \| `"suppressed"` | `inject` = 返回规则正文；`suppressed` = 抑制（`call_count>0` 时） |
| `content` | str | 规则正文 markdown（`suppressed` 时为 `""`） |
| `description` | str | **强制约束告警语**，告诉下游模型这些规则必须严格遵守。**cann-ask 的核心职责是把它抬到答案最顶 `## ⚠️ 强制规则` section** |
| `estimated_tokens` | int | 规则正文 token 估计（`suppressed` 时 `0`） |
| `rule_refs` | list[{phase, version, full_page_id}] | 规则的完整页 ID 列表（可用 `wiki_get_page` 取全文） |

**`static`（tier1）/ `dynamic`（tier2）同构信封字段**（v5.2 简化 —— ai_suggestion / Agent 状态已抬到顶层）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `results` | list[dict] | 检索结果项数组（详见下方 results 字段） |
| `total` | int | 召回总数（可能 > `len(results)`，仅返回 top-`limit`） |
| `no_useful_results` | bool | 主 Agent 判定本区无可用结果（true 时建议提示用户重述 query / 补充 device_feedback） |
| `warning` | str \| None | 本区告警（原样透传，不重试） |


**`static.results[i]` 字段**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | str | 知识 ID `{type}:{slug}`（例 `api_reference:datacopy-intf`）。**唯一锚点**，要正文必须 `wiki_get_page(id)` |
| `summary` | str | 简短摘要 |
| `score` | float | **本次 query 的相关性**（`rerank_score`，兜底到 `rrf_score`） |
| `tags` | list[str] | 页面标签 |
| `branch` | str | 固定 `"traditional"`（tier1 漏斗标识） |
| `extra` | dict | 内部分数：`{qValue, qDraft, qRefine, qIntent, rerankRank, rerankScore, rrfScore}`。`qValue` = **历史有用度**（Q-sort 主键）。客户端通常不读不展示 |

**`dynamic.results[i]` 字段**：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | str | 知识 ID（例 `recipe:simd-perf-memory-access`） |
| `summary` | str | 简短摘要 |
| `scenario` | str | 适用场景描述（cann-ask 渲染时用作 `### {scenario}` 子标题） |
| `steps_outline` | list[str] | 修复步骤大纲（可能为空） |
| `cautions` | list[str] | 注意事项（可能为空） |
| `score` | float | 相关性 |
| `tags` | list[str] | 页面标签 |
| `branch` | str | 固定 `"dynamic"`（tier2 漏斗标识；与 tier1 的 `"traditional"` 对应） |
| `citations` | list[str] | 引用的其他知识 ID |
| `community_id` | str \| None | 社区聚类 ID（图谱探索用，cann-ask 单轮不展开） |
| `neighbors_top3` | list[str] | top-3 邻居 ID（cann-ask 单轮不展开） |

**重要不变量**：
- **`path` 字段已彻底移除** —— `id` 是唯一锚点，要正文必须 `wiki_get_page(id)`。不要从 id 文本猜路径
- **tier 由响应分区决定** —— `phase_rule`→tier0 / `static.results`→tier1 / `dynamic.results`→tier2。不要从 id 文本前缀反推
- **score 仅在各自 tier 内部降序** —— 两 tier 之间不可比（打分器不同），不要跨 tier 合并排序

### 多轮约定

首次 `call_count=0` 取 tier0 规则；后续轮次：
- `call_count` 递增（每轮 +1）
- `seen_ids` 累积前几轮所有 `static.results[i].id` + `dynamic.results[i].id`，去重
- `device_feedback` 传最近一次上板报错（caller 已截断）

→ 规则不重复返回、已用知识不重复召回、tier2 据上板反馈调整召回。

## `wiki_get_page` —— 按 ID 取单页正文（一次一页）

**签名**：`wiki_get_page(id: str) -> dict`

**作用**：取单篇知识正文（rule 全页 / static 页 / recipe 正文都通用）。**一次只能取一页**；要多页就对每个 id **逐个循环调用**（server 已不再支持批量 `ids`）。

### 入参字段

| 参数 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `id` | str | ✓ | **单个**知识 ID；来自 `wiki_search` 各分区的 `.id` 或 `phase_rule.rule_refs[].full_page_id` |

### 返回结构

命中：

```json
{
  "id":          "api_reference:datacopy-intf",
  "frontmatter": {"title": "...", "tags": [...], ...},
  "content":     "完整 markdown 正文（含 frontmatter section）",
  "qValue":      0.73
}
```

未命中：

```json
{"id": "operator:nonexistent", "error": "id not found"}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | str | 知识 ID（与请求一致） |
| `frontmatter` | dict | 已解析的 YAML frontmatter（含 `title`/`tags`/...）；渲染 References 时取 `frontmatter.title` |
| `content` | str | 完整 markdown 正文（含 frontmatter section） |
| `qValue` | float | server 端 Q 值；**客户端不读不展示** |
| `error` | str | 仅未命中时出现；某个 id 取失败不阻塞其余循环调用，若影响覆盖范围在答案里提一句 |

## `wiki_grep_raw` —— 核对 raw 源码（grep 真源，按需）

**签名**：`wiki_grep_raw(pattern: str, scope: str|None=None, context: int=4, glob: str|None=None, include_tests: bool=False, max_hits: int=30) -> dict`

**作用**：在 `raw/`（官方源码 / 手册的只读副本）里 grep，**用真源核对 wiki 说法**（API 签名 / tiling 结构体字段 / kernel 细节是否和官方一致）。只返回命中行窗口、不返整文件，巨型手册也不会撑爆上下文。当 wiki 正文里某个符号可能过时、且你要据它写 kernel 时用来佐证。

| 参数 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `pattern` | str | ✓ | grep 正则。通常是要核对的符号：API 名（`DataCopyPad`）/ 算子名 / tiling 结构体或字段（`SwiGluTilingData`）/ 函数签名片段 |
| `scope` | str\|None | 否（强烈建议传） | 收窄检索面：传**该知识的 ID**（如 `operator:swi_glu`）或**算子名**；server 据其 raw_links 解析到算子目录 + 官方总册降级链。None=全量（噪声多） |
| `context` | int | 否 | 命中行上下文行数（clamp [0,10]，默认 4） |
| `glob` | str\|None | 否 | 再过滤文件，如 `*.h` / `*tiling*` |
| `include_tests` | bool | 否 | 是否纳入 tests/ut/examples（默认 False，砍噪声） |
| `max_hits` | int | 否 | 返回上限（默认 30，server 硬 clamp） |

**返回**：`{hits:[{path, line, snippet, role}], returned, total_lower_bound, truncated, scope_resolved, warning?, error?}`
- `role ∈ {host, doc, kernel, guide, other}`；host/doc/guide 优先排序。
- `snippet` 形如 `"42: <text>"`，每条 ≤ `2*context+1` 行；`path` 相对仓库根（`raw/...`）。
- `truncated=true` 时 `total_lower_bound` 仅为下界，可缩 `scope` / 收紧 `pattern`。
- 拿到 hit 后可用 `wiki_read_raw(path, start, end)` 按行段扩读更多上下文。

## `wiki_read_raw` —— 扩读 raw 文件行段（配合 grep）

**签名**：`wiki_read_raw(path: str, start: int=1, end: int|None=None) -> dict`

**作用**：读 `raw/` 内单个文件的指定行段（只读、沙箱）。通常在 `wiki_grep_raw` 命中后，对某个 hit 的 `path` 扩读更多上下文。`path` 必须落在 `raw/` 内，穿越 / 越界路径会被拒。

| 参数 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `path` | str | ✓ | raw 内文件路径（相对仓库根，如 `raw/coding-sources/.../swi_glu_tiling.h`；一般直接用 `wiki_grep_raw` 返回的 `path`） |
| `start` | int | 否 | 1-indexed 起始行（默认 1） |
| `end` | int\|None | 否 | 1-indexed 结束行（含端点）；省略=到文件末尾或单次行数上限 |

**返回**：`{path, start, end, content, total_lines, truncated}` | `{path, error}`
- 单次硬封顶 server 配置的 max_lines / max_bytes，超限置 `truncated=true`。

> **何时用 raw 工具**：cann-ask 主流程是 `wiki_search → wiki_get_page → 综合`；raw 工具是**可选的核对增强** —— 当你要把某个 API 签名 / 结构体字段 / 编译选项写进 kernel、又担心 wiki 与官方源/手册有漂移时，用 `wiki_grep_raw`（必要时 `wiki_read_raw` 扩读）拿真源佐证再落笔。**不是每次问答都要调。**

## `wiki_help` —— 拿当前 server 契约（自描述）

**签名**：`wiki_help() -> dict`

**作用**：返回 server 端工具签名/参数/返回结构的自描述。**仅在响应结构与本文件描述不一致时调用** —— 平常不调，避免增加延迟。返回的是上游 `help_doc.py` 内容,可能滞后于实际响应(例如仍列 `path`),冲突时以实际响应 / `response_schema.py` / `server.py` 为准。

**返回**：`{server, overview, tiers, tools, text}`，其中 `text` 是人类可读 markdown 契约说明。
