---
name: cann-ask
description: "CANN Wiki 知识检索：用自然语言提问检索 AscendC Kernel 开发知识（算子、API、tiling、踩坑规则、排错），返回含强制规则 + 检索建议 + 带引用正文的结构化答案。当用户询问任何 AscendC / Ascend C 相关问题，或 agent loop 需要多轮知识检索时，必须使用本 skill（不要直接调 MCP）。触发命令：`/cann-ask`。"
---

# CANN 知识问答 Agent

面向 AscendC Kernel 任务（人类问答 & agent loop 多轮检索）的自然语言知识检索。把任务总目标 + 上轮设备反馈合成一句聚焦的自然语言 query，通过 MCP server 单次检索 wiki，按"phase_rule 硬约束规则 + ai_suggestion server 检索建议 + page 正文合成"三层渲染给下游。

## 前置条件

**MCP Server 必须已启动**，endpoint: `http://113.46.4.206:8767/mcp`（streamable-http 传输；由 `/setup-cann-wiki` 配置，地址不一致时先跑该 skill）。验证可调 `wiki_search("测试", limit=1)` 或检查 8767 端口。如果未启动，提示用户先启动。

各 MCP 工具的**完整字段级契约快照**（签名 / 入参表含"cann-ask 取值" / 返回结构 / 不变量 / 多轮约定）在 `references/mcp-tools-contract.md`。**需要查它时，用 Read 工具打开本 skill 基础目录下的该文件** —— 基础目录是 Claude Code 在 "Base directory for this skill:" 处给出的绝对路径，即 Read `<基础目录>/references/mcp-tools-contract.md`。主路径（`wiki_search` + `wiki_get_page`）仅凭本 SKILL.md 即可执行，**不必每次都读**；只在拿不准某个参数/返回字段、或本文档与实际响应对不上时才打开它。下文出现"详见 `references/mcp-tools-contract.md` → …"都按这个方式打开。

响应异常时可调 `wiki_help()` 对照，但字段 shape 以**实际响应** / `response_schema.py` / `server.py` 为准（上游 `help_doc.py` 可能滞后，例如仍列 `path`）。

## cann-ask 相关工具速查

按调用时机分组（完整契约逐个详见 `references/mcp-tools-contract.md`）：

| 时机 | 工具 | 一句话用途 |
|---|---|---|
| **主路径**（每次问答必走） | `wiki_search(query, phase, …)` | 三分区检索：tier0 规则 + tier1 static + tier2 dynamic |
| **主路径**（每次问答必走） | `wiki_get_page(id)` | 按 id 取**单页**正文（一次一页，需循环） |
| **按需核对**（可选） | `wiki_grep_raw(pattern, scope, …)` | 在 `raw/` grep 真源，核对 API/结构体是否漂移 |
| **按需核对**（可选） | `wiki_read_raw(path, start, end)` | 扩读某个 raw 文件行段（配合 grep） |
| **异常对照**（平常不调） | `wiki_help()` | 仅当响应结构与本契约不一致时调，平常不调以免增延迟 |

## 触发方式（必须走本 skill，不要直接调 MCP）

**重要**：当 MCP 工具（`mcp__cann-wiki__wiki_search`、`mcp__cann-wiki__wiki_get_page`）可用时，**始终通过 cann-ask skill 调用，不要直接调它们**。直接调会绕过以下能力 → 规则被忽略、ai_suggestion 丢失、phase 错配、跨轮 task_description 漂移、答案无引用：

- **task_description / device_feedback / phase 一体化** —— skill 跨轮稳定 `task_description`、把 `device_feedback` 揉进 query、按 feedback 自动派生 `phase`
- **rule 字段强制渲染** —— 把 `phase_rule.description` 抬到答案最顶 `## ⚠️ 强制规则`，完整 JSON 附末
- **顶层 ai_suggestion 优先抬升** —— 在强制规则之后、相关文档之前以 `> 💡 主 Agent 综述` 块引用渲染一次
- **自动逐页 fetch + 合成带引用的答案** —— 对 top-3 static + top-2 dynamic 的 id 各调一次 `wiki_get_page(id)`，跨页面写实质内容

**触发场景：**
- 用户在任何问题中提到 "AscendC" / "Ascend C"，或询问 kernel 开发、算子、API、模式
- 用户请求对比、how-to、列出枚举、排错
- 用户显式输入 `/cann-ask` 或说"搜 wiki"
- agent loop（如 bench 算子生成循环）每轮调一次，传入累积上下文（`call_count` / `seen_ids` / `device_feedback`）

**反面 / 正确模式：**

```
# ❌ 错误：把中文意图翻成英文后直接调 MCP —— 绕过 skill + 中文转英文降召回
mcp__cann-wiki__wiki_search(query="conv2d implementation", limit=5)

# ✅ 正确：触发 skill 工作流，query 保持中文
/cann-ask 卷积怎么实现
```

## 输入

$ARGUMENTS

调用方在 `$ARGUMENTS` 里以下面结构传入（agent loop / 人类问答 通用）：

```
<本轮自由文本问法（人类问答时是用户原话；agent loop 时可省略或写一句简短意图）>

[TASK_DESCRIPTION: <本次任务的总目标>]              # caller 责任：round 1 起逐字节缓存，每轮 verbatim 重传
[CALL_COUNT: <int>]                                # 当前轮次，0 起步
[SEEN_IDS: id1, id2, ...]                          # 前几轮已召回 id 累积
[DEVICE_FEEDBACK: <报错文本片段>]                   # 第一轮不填；第二轮起必填；原文截断由 caller 管（50-200 字关键段）
```

**关键约束**：
- `TASK_DESCRIPTION` 一旦 round 1 确定，**跨轮逐字节不变**。caller 责任：缓存重传。skill 内**不修改、不重写、不润色**，直接透传给 `wiki_search.task_description`。
- `DEVICE_FEEDBACK` 的截断由 caller 在传入前完成；skill 不再做二次裁剪。
- 单次人类问答（不带 `TASK_DESCRIPTION` / `CALL_COUNT` 等）：把整段 `$ARGUMENTS` 当作 `task_description`，`call_count=0`、`seen_ids=null`、`device_feedback=null`。

## 工作流

**核心**：每次 cann-ask 调用 = 1 次 `wiki_search` + 每个目标页 1 次 `wiki_get_page`（单页接口，需循环；通常 top-3 static + top-2 dynamic ≈ 3-5 次）。query 是 30-150 字的聚焦自然语言提问（**不**抽实体、**不**挑 tags、**不**拆 sub-query）；`phase` 从 `device_feedback` 自动派生；`tags` 一律 `[]`；tier2 由 server 主 Agent 自动预跑。

### 阶段 B：构造 query + 决定 phase + 单次检索

#### B.1 构造 query（自然语言，30-150 字软约束）

> **query 保持原始语言**（中文 task 传中文 query，英文 task 传英文）。Wiki 语料以中文为主，翻译会显著降召回。

| 场景 | query 怎么生成 |
|---|---|
| **round 1**（`device_feedback` 为空） | 把 `task_description` paraphrase 成一句聚焦提问，**不要整段 dump** —— 把"总目标"重写成"本轮想查清楚什么"。<br>例：task ≈ "实现 gemm_add_relu，910B，fp16，权重 NZ 排布…" → query "在 910B 上用 fp16 实现 gemm_add_relu，矩阵乘融合激活的 tiling 和写法要点是什么" |
| **round N**（有 `device_feedback`） | 把 `task_description` 领域上下文 + `device_feedback` 暴露的具体卡点合成一句聚焦提问。<br>例：feedback 含 "DataCopy block size mismatch" → query "gemm_add_relu kernel 中 DataCopy 出现 block size mismatch，常见原因和修复套路有哪些" |

**长度软约束**：30-150 字。**不要**把 `task_description` / `device_feedback` 整段塞进 `query`（它们已分别通过独立参数传给 server，重复 dump 会稀释聚焦度）。

#### B.2 自动派生 phase

| 信号 | `phase` 取值 |
|---|---|
| `device_feedback` 为空（round 1） | `correctness`（默认） |
| 含编译错误（`error:` / `compilation terminated` / undeclared identifier / 语法错等 stderr 文本） | `correctness` |
| 含精度对比（`max_diff` / `mismatch` / 精度阈值未达标 / NaN / 溢出） | `precision` |
| 含性能数字（`µs` / `cycle` / 带宽 / 利用率 / "慢"/"卡顿" 等表述） | `performance` |

**禁止** `phase=all` —— 会让 tier0 一次返三篇规则正文，token 爆炸。

#### B.3 单次调用 wiki_search

```
wiki_search(
  query:            "<B.1 合成的自然语言提问，30-150 字>",
  phase:            "correctness" | "precision" | "performance",   # B.2 派生
  tags:             [],                                            # 显式空数组，server 走全量召回
  type:             null,                                          # 全库 ascendc，传了无效
  limit:            3,
  task_description: "<caller 缓存的 round-1 原文，跨轮 verbatim>",
  call_count:       <int>,                                         # 单次人类问答 = 0
  seen_ids:         [...] | null,                                  # 单次人类问答 = null
  device_feedback:  "<caller 已截断的上轮报错原文>" | null,           # 第一轮可空；第二轮起必填
)
```

各入参语义详见 `references/mcp-tools-contract.md` → wiki_search 入参表。

> ℹ️ **tier2 由 server 预跑** —— 无 `include_dynamic` 客户端开关;主 Agent 一次同时编排 tier1 + tier2。若 `dynamic.results == []`,通常是主 Agent 判定本区无可用 recipe（看 `dynamic.no_useful_results`）而非未跑。

> ⚠️ `call_count == 0` 才返 tier0 规则正文；第二轮起 `phase_rule.content_mode = "suppressed"` 且 `content = ""`，渲染时整段跳过（参见阶段 D）。

### 阶段 C：逐页 fetch（单页接口，循环调用）

**`wiki_get_page` 一次只取一页** —— 把 `static.results` top-3 和 `dynamic.results` top-2 的 id 收齐，**对每个 id 各调一次**（server 不支持批量 `ids`）：

```python
ids = [r["id"] for r in response["static"]["results"][:3]] \
    + [r["id"] for r in response["dynamic"]["results"][:2]]   # round 1 dynamic 通常为空
pages = [wiki_get_page(id) for id in ids]                      # 逐页循环，一页一调
```

返回字段语义详见 `references/mcp-tools-contract.md` → wiki_get_page 返回结构。

- 命中页 `frontmatter.title` → 阶段 D References 列表的 title
- 命中页 `content` → 阶段 D 各 tier section 主体的实质内容来源（不是只抄 summary）
- 返回里带 `error` 的（未命中）→ 个别 id 解析失败时在答案末尾提一句，不阻塞其余页

### 阶段 C+：（可选）raw 真源核对 —— 仅在不确定时调用

`wiki_grep_raw` / `wiki_read_raw` 是**选用工具，不是每次问答都要调**。cann-ask 主流程是 `wiki_search → wiki_get_page → 合成`；只有遇到下面两种情况，才用它们去查官方源码 / 手册的只读副本（`raw/`）核对：

- 你对 wiki 正文里某个符号（API 签名 / tiling 结构体字段 / 编译选项 / 枚举值）的**准确性没把握**，又要据它写进 kernel；
- wiki 的说法与**真实结果冲突** —— 例如对不上 `device_feedback` 报错、上板实际行为、或官方手册。

用法（两步，按需）：

1. **`wiki_grep_raw(pattern, scope=<该知识的 id 或算子名>)`** —— 在 `raw/` 里 grep 要核对的符号（如 `DataCopyPad`、`SwiGluTilingData`）。`scope` 强烈建议传，收窄检索面；只返回命中行窗口（`hits[].path` + 片段），不返整文件。
2. 对某条命中，用 **`wiki_read_raw(path, start, end)`** 按行段扩读更多上下文（`path` 直接用上一步返回的 raw file path）。

拿到真源后再落笔；若真源与 wiki 冲突，**以真源为准**并在答案里注明这点。完整签名 / 参数见 `references/mcp-tools-contract.md` → wiki_grep_raw / wiki_read_raw。

### 阶段 D：合成答案

按下面顺序输出。**每段都基于 `wiki_get_page` 返回的页面正文写实质内容**（不是只抄 summary）。完整版式见下方"输出格式"样例。

1. **`## ⚠️ 强制规则`**（tier0 phase_rule）—— **核心职责，必须最顶部渲染**，详见下方"rule_description 渲染细则"
2. **顶层 ai_suggestion 块** —— 若 `response.ai_suggestion` 非空,在 `## 📚 相关文档` 之前以 `> 💡 **主 Agent 综述**` 块引用渲染一次,保留 `[id:xxx]` 引用原样。标签按 `response.agent_status` 派生。详见"ai_suggestion 渲染细则"
3. **`## 📚 相关文档`**（tier1 static）—— 基于 static page 正文写结构化答案，带 `[Source: <id>]` 内联引用
4. **`## 🔧 实践 recipe`**（tier2 dynamic）—— `dynamic.results` 为空时**整段隐藏**；否则每条 recipe 用 `### {scenario}` 三级标题，下接基于 recipe page 正文的实质内容
5. **References** —— 单一列表，tier 前缀 📚（tier1）/ 🔧（tier2），title 取 `wiki_get_page` 的 `frontmatter.title`
6. **告警 / 状态汇总**（按需）—— 详见"告警状态渲染细则"
7. **诊断 JSON 附末** —— `phase_rule` + `agent_status` + `agent_turns` + `sub_queries`,详见"rule_description 渲染细则"

**引用规则**：每个事实必须**内联**引用 `[Source: <id>]`，紧跟事实写而不放到末尾；多来源 `[Source: <id1>, <id2>]`；id 取 `wiki_get_page` 返回值，不要编造路径。

#### rule_description 渲染细则（核心职责）

`phase_rule.description` 是 server 标注的硬约束告警语（给下游 codegen 模型的"必读"信号）。**抬到答案最顶部、最显眼位置渲染，是 cann-ask 的核心职责**。

- **`content_mode == "inject"`** → 严格渲染如下结构到答案最顶（在 `## 📚 相关文档` 之前）：

  ```markdown
  ## ⚠️ 强制规则

  {phase_rule.description}

  {phase_rule.content}
  ```

  `phase_rule.content` 原样贴出（通常已是规则编号列表，不要二次摘要）。
- **`content_mode == "suppressed"`** → 整段 `## ⚠️ 强制规则` section **不渲染**（不要渲染只有 description 没有 content 的"空规则告警"）

**诊断 JSON 附末**：无论 `inject` 还是 `suppressed`，**都要**把完整 `phase_rule` + 顶层诊断字段（`agent_status`/`agent_turns`/`sub_queries`）原样附在答案最末的独立 ```json fenced block 里。用途：trajectory 上报、phase_rule 命中统计、主 Agent 行为审计、eval 侧解析。

#### ai_suggestion 渲染细则

主 Agent 把跨 tier 综述抬到**顶层** `response.ai_suggestion`（单一来源,内嵌 `[id:<完整ID>]` 引用）。**优先级高 —— 是 server 端"读过 top-N 之后的提炼"，比 cann-ask 只看 summary 更准。**

- 若 `response.ai_suggestion` 非空 → 在 `## ⚠️ 强制规则` 之后、`## 📚 相关文档` 之前**渲染一次**：

  ```markdown
  > 💡 **{label}**
  >
  > {response.ai_suggestion 原文，保留 [id:xxx] 内联引用不改写}
  ```

  `{label}` 按 `response.agent_status` 派生：

  | `agent_status` | `{label}` |
  |---|---|
  | `"ok"` | `主 Agent 综述`（跨 tier 合成,可信） |
  | `"degraded"` | `检索建议`（兜底序,相关性可能下降） |
  | `"disabled"` | `检索建议`（Agent off,纯漏斗序） |

- 若 `response.ai_suggestion` 为 `null` / 空 → 整个 `> 💡` 块不渲染
- 渲染位置：**强制规则之后、tier 主体之前**,让下游先读到精炼建议
- **`[id:xxx]` 引用原样保留**，与 References 列表 ID 对齐，不要改成 `[Source: xxx]`

#### 告警状态渲染细则

按下面顺序在 References 之后追加（有则渲染，无则跳过）：

| 场景 | 触发 | 渲染 |
|---|---|---|
| 顶层告警 | `response.warning` 非空 | `> ⚠️ **Server warning**: {warning}` |
| tier1 告警 | `static.warning` 非空 | `> ⚠️ **Tier1 warning**: {static.warning}` |
| tier2 告警 | `dynamic.warning` 非空 | `> ⚠️ **Tier2 warning**: {dynamic.warning}` |
| tier1 无可用 | `static.no_useful_results == true` | `> ℹ️ Tier1 主 Agent 判定本区无可用结果，建议重述 query` |
| tier2 无可用 | `dynamic.no_useful_results == true` | `> ℹ️ Tier2 主 Agent 判定本区无可用 recipe，建议补充 device_feedback 关键信息` |
| 主 Agent 兜底 | `response.agent_status == "degraded"` | `> ⚠️ 主 Agent 失败/超时，两 tier results 来自漏斗兜底序，相关性可能下降 (turns: {agent_turns})` |
| Agent off | `response.agent_status == "disabled"` | `> ℹ️ 本次未走主 Agent (server cfg / 输入异常);results 为纯漏斗序` |

**原样透传**，不重试不降级。`agent_status`（ok/degraded/disabled）是顶层枚举,tier1/tier2 共享同一个 Agent 状态。

## 输出格式

答案的**版式骨架**如下（`{...}` 是占位符，按实际响应填充；这是结构示意，**不要照抄任何示例文字**，各槽位的具体渲染规则见上方"渲染细则"）：

```markdown
## ⚠️ 强制规则                 # content_mode==suppressed 时整段省略
{phase_rule.description}
{phase_rule.content}            # 规则编号列表，原样贴出

> 💡 **{label}**               # 仅 ai_suggestion 非空时出现；label 按 agent_status 派生
> {response.ai_suggestion}      # 保留 [id:xxx] 引用原样

## 📚 相关文档
{基于 tier1 static 页面正文的结构化答案，带 [Source: <id>] 内联引用}

## 🔧 实践 recipe               # dynamic.results 为空时整段隐藏
### {scenario}
{基于 tier2 recipe 页面正文的实质内容} [Source: <id>]

---
**References:**               # 单一列表，title 取 frontmatter.title
- 📚 {tier1 id} — {title}
- 🔧 {tier2 id} — {title}

> {告警/状态行}                 # 按"告警状态渲染细则"，无则省略

{诊断 JSON：完整 phase_rule + agent_status + agent_turns + sub_queries，原样附在独立 json fenced block}
```

## 注意事项

- **自然语言 query，单次检索** —— 一次 cann-ask = 1 次 `wiki_search` + 逐页 `wiki_get_page`；query 是 30-150 字聚焦提问，**不**抽实体、**不**挑 tags、**不**拆 sub-query
- **task_description 跨轮 verbatim 不变** —— caller 责任：round 1 起逐字节缓存重传；skill 内**不修改、不重写、不润色**
- **phase 从 device_feedback 自动派生** —— round 1 默认 `correctness`；round N 映射到 `correctness` / `precision` / `performance`。**禁止** `phase=all`
- **path 仅 wiki_search 结果链路下沉** —— `wiki_search` 的 static/dynamic results 不含 page path，result `id` 是页面正文唯一锚点，只能用它调 `wiki_get_page(id)`（逐页循环，对 static_top_3 + dynamic_top_2 每个 id 各调一次）；**raw file path** 仅限 raw 工具链（`wiki_grep_raw` 返回 `hits[].path` / `wiki_read_raw(path,…)` 入参）使用，不受此下沉影响
- **score 仅 tier 内可比** —— `static.score` 与 `dynamic.score` 用不同打分器，不要跨 tier 合并排序
- **tier 由响应分区决定** —— 不要从 id 文本前缀（`{type}:{slug}`）反推
- **raw 核对工具按需用** —— `wiki_grep_raw` / `wiki_read_raw` 是可选真源佐证：把 API 签名 / 结构体字段 / 编译选项写进 kernel 前若怀疑 wiki 与官方源漂移才调,不是每次问答必调
- **不要编造** —— 两 tier 都空或 `warning` 非空时明确告知,不猜。MCP 不可达时报错，不阻塞本地功能

## 错误处理

| 场景 | 处理 |
|------|------|
| MCP Server 未启动 | 提示启动命令 |
| `wiki_search` 响应缺 v5.2 顶层字段（`phase_rule`/`ai_suggestion`/`agent_status`/`static`/`dynamic`） | 调 `wiki_help()` 拿当前契约对照，告诉用户 server 版本不匹配；不要强解析 |
| `wiki_search` 结果为空（`static` 和 `dynamic` 都空），或响应携带 `warning` | 把 warning 文本原样转给用户；建议调整 query 措辞 |
| `static.no_useful_results == true` | 渲染 `> ℹ️ Tier1 主 Agent 判定本区无可用结果...` 告警；results 不删 |
| `response.agent_status == "degraded"` | 渲染 `> ⚠️ 主 Agent 失败/超时...` 告警；两 tier results 都来自漏斗兜底序,不删 |
| `response.agent_status == "disabled"` | 渲染 `> ℹ️ 本次未走主 Agent (server cfg / 输入异常)...` 告警；results 为纯漏斗序,不删 |
| `wiki_get_page` 部分失败 | 列出未解析的 id；用其余 `pages` 继续合成 |
| `phase_rule.content_mode == "suppressed"` | graceful 跳过顶部 `## ⚠️ 强制规则` section；JSON 附末仍保留 |
| 网络超时 | "超时，检查 MCP Server 状态" |
