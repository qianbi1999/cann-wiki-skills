---
name: progress-upload
description: "通过 MCP 上传算子开发过程产出的 progress.md(如 cann-prompt-kit `output/<op>-test/claude/run<N>/<op>.progress.md`)到 CANN Wiki。按算子归档,供离线知识加工。触发命令:`/progress-upload`。"
---

# Progress Upload

把算子开发过程产出的 `progress.md`(按 try 迭代记录"做了什么 / wiki 查询 / 反思 / 需求 / 证据"的复盘文档)上传到 CANN Wiki 知识库。对接 MCP 工具 `wiki_submit_progress(op, content, run_id?, date?)`,server 按**上传日期 + 算子**归档落盘到 `<progress.uploaded_dir>/<date>/<op>/<run_id>.progress.md`,纯落盘、离线消费。

与 `session-upload` 的区别:source 是磁盘上**已存在的 Markdown 文件**(不是 live 会话轨迹),所以无需转换器 —— 定位文件 → 派生 op/run_id → 上传即可。

## 文件布局

```
progress-upload/
├── SKILL.md                    # 本文件 —— 定位 + 派生 + 上传
└── scripts/
    └── progress_upload.py      # HTTP MCP 上传器(派生 op/run_id + POST wiki_submit_progress)
```

## 前置条件

1. MCP Server 已启动且 `wiki_submit_progress` 可用 —— 先跑 `/setup-cann-wiki`。
2. 目标 `progress.md` 已存在于磁盘。典型位置:`cann-prompt-kit/output/<op>-test/claude/run<N>/<op>.progress.md`。

## 解析 skill 基础目录

`scripts/` 下脚本**就地**在本 skill 基础目录里运行 —— **不要** Read+Write 到 `/tmp`。在 Bash 会话最开始设一次 `SKILL_DIR`:

```bash
SKILL_DIR="<在 'Base directory for this skill:' 处显示的绝对路径>"
```

把 agent 在 skill 加载头部给出的字面路径填进去。若该路径未暴露(如 OpenCode 把内容包在 `<skill_content>` 里),退回:`find ~ /usr/local /opt -maxdepth 6 -type d -name progress-upload 2>/dev/null | head -1`。

## Step 1:定位 progress.md

用户给了路径就直接用。没给则在常见输出目录里找,让用户确认选哪个:

```bash
FILE="<用户给出的路径>"
# 未给路径时,枚举候选(按修改时间倒序)供选择:
# find <cann-prompt-kit>/output -name '*.progress.md' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-
```

## Step 2:通过 MCP 上传

MCP 工具签名:

```
wiki_submit_progress(op: str, content: str, run_id: str | None = None, date: str | None = None) -> dict
```

Server 把字节原样落到 `<progress.uploaded_dir>/<date>/<op>/<run_id or op>.progress.md`(**按上传当天 `YYYY-MM-DD` 分目录归档**,路径来自 server 的 `config.yaml`);不解析、不入库,下游由离线引擎 / 人工 `progress-to-wiki` 消费。

**按日期分目录**:`progress_upload.py` 默认取本机当天日期作为顶层子目录(`--date` 缺省即今天),**无需手动传**;同一天上传的所有算子 progress 自动汇总到同一个日期目录下。需要回填到指定日期时用 `--date YYYY-MM-DD`(server 端再校验,非法/缺省回退到 server 当天)。

**不要直接以 `tool_use` 形式调用 `wiki_submit_progress`。** 长 `content`(>~13KB)会被静默截断 —— 整个 tool_use payload 必须塞进模型 max-output-tokens 预算,而真实 progress.md 常达 ~18KB。改用辅助脚本 `scripts/progress_upload.py`,它从 Bash 子进程 POST 到 MCP HTTP endpoint,完全绕开 output-token 上限。

直接从 `$SKILL_DIR` 运行:

```bash
python3 "$SKILL_DIR/scripts/progress_upload.py" --file "$FILE"
```

归档布局:**日期 / 算子名 / 实验名**,即 `<date>/<op>/<实验名>.progress.md`。
`op` 从文件名派生;**实验名**(作为 `run_id` 传给 server)取路径里 adapter/run 目录的上一层(run 目录的上两级);`date` 默认本机当天。例(2026-06-04 上传):
`.../output/debug_test_v4/claude/run0/mla_prolog.progress.md` → `2026-06-04/mla_prolog/debug_test_v4.progress.md`。同一天重跑同一实验会覆盖其文件。需要时显式传 `--op NAME` / `--run-id 实验名` / `--date YYYY-MM-DD`。

URL 解析优先级:`--url` > `$CANN_WIKI_MCP_URL` > agent MCP 配置(向上找 `.mcp.json` / `.opencode/opencode.json` 里 `cann-wiki` 条目的 `url`,再退到 `~/.claude.json`)> 默认 `http://113.46.4.206:8767/mcp`。跑过 `/setup-cann-wiki` 后端口自动对齐,**无需手动设环境变量**。

成功输出一行:`OK <op>/<文件名>`(如 `OK mla_prolog/debug_test_v4.progress.md`)—— 只回显末级目录加文件名,**不暴露完整绝对路径**。Server 报错或意外响应时,脚本原样打印 server payload 并非零退出 —— 原样转给用户,不要改写。

**禁止**:
- 把 `wiki_submit_progress` 当 tool_use 直接调用(如上所述会截断)
- 把内容替换成 "[Full progress uploaded]" 之类的摘要
- 上传前手动截断文件
- 给 MCP 工具传 `file_path=` / `source=` —— 这些参数在 server 上不存在

## Step 3:汇报结果

```
✓ Uploaded
- Date:    {上传日期 YYYY-MM-DD}   （顶层归档目录）
- Op:      {op}
- Run:     {run_id 或 "(none)"}
- Path:    {date}/{op}/{文件名}    （脚本回显只到 `<op-dir>/<文件名>`,完整路径前还有日期目录;不显示绝对路径）
- Pipeline: 离线引擎 / `progress-to-wiki` 消费(server 仅落盘,不解析)
```

## 错误处理

| 场景 | 处理 |
|---|---|
| 文件不存在 | "找不到 progress.md —— 确认路径,或在 cann-prompt-kit output 目录下查找" |
| MCP 未配置 | "先跑 `/setup-cann-wiki`" |
| `wiki_submit_progress` 未注册 | "MCP 工具缺失 —— setup 后请重启 agent" |
| 文件为空 | server 返回 `{status:"error", message:"content must not be empty"}`,原样转给用户 |
| Server 返回 `{status:"error", message:"..."}` | 把 `message` 字段原样转给用户 —— 不要改写或吞掉 |
| 网络 / API 错误 | "网络错误,稍后重试" |

## 注意事项

- **归档布局** —— `<date>/<op>/<实验名>.progress.md`:按**上传日期**顶层分目录,其下同一算子的不同**实验**在该 op 目录下并排各一份(文件名 = 实验名);日期 / op 目录已存在直接复用(`mkdir exist_ok`)。
- **按日期汇总** —— `date` 默认本机当天(`--date YYYY-MM-DD` 可回填);同一天上传的所有算子 progress 落在同一个日期目录下,不同天分到不同目录,便于按日期归总。
- **同 (日期, 算子, 实验) 重传即覆盖,无版本** —— server 用 `write_text` 写固定路径:相同 `(date, op, 实验名)` 重传**原地覆盖**、只留最新(不报错、不新增、不留旧版)。所以**同一天整批重传是幂等的**:没变的覆盖回自身、改过的更新为最新、新实验追加,不会堆出重复文件。跨天上传则进入新日期目录(各自一份)。同一实验若想在同一天保留多次快照,显式 `--run-id` 给不同值(如带时间戳)。
- **原样保留** —— progress.md 全文原样上传,不截断、不摘要;脱敏/抽取是下游的事。
- **自测** —— 派生逻辑有单测:`cd "$SKILL_DIR/scripts" && python -m unittest discover -s tests -p 'test_*.py'`。
