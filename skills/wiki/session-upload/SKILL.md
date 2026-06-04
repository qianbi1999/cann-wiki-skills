---
name: session-upload
description: "通过 MCP 自动上传当前会话轨迹（Claude Code 或 OpenCode）到 CANN Wiki。触发命令：`/session-upload`。"
---

# Session Upload

自动把当前会话轨迹上传到 CANN Wiki 知识库。同时支持 **Claude Code** 和 **OpenCode** —— skill 会检测当前运行的 agent 并 dispatch 到 `scripts/` 下对应的转换器。

## 文件布局

```
session-upload/
├── SKILL.md              # 本文件 —— 检测 + dispatch + 上传
└── scripts/
    ├── cc_convert.py     # Claude Code JSONL → Markdown（用于 2A）
    ├── oc_convert.py     # OpenCode JSON → Markdown（用于 2B）
    └── mcp_upload.py     # HTTP MCP 上传器（用于 3，两端共用）
```

两个转换器互相独立。新增平台 = 在 `scripts/` 下加一个新转换器，并在 Step 2 加一个分支；处理另一平台时永远不需要动现有转换器。`mcp_upload.py` 与平台无关，两条路径在 Step 3 汇聚到它。

## 前置条件

1. MCP Server 已启动且 `wiki_submit_trajectory` 可用 —— 先跑 `/setup-cann-wiki`。
2. **Claude Code 路径**：会话在 Claude Code 内运行（轨迹位于 `~/.claude/projects/`）。
   **OpenCode 路径**：已安装 OpenCode CLI（`opencode`）且至少有一个 session。
3. 轨迹中需要出现 "Ascend C" / "AscendC" —— 否则下游 `knowledge_engine` ingest 管线会把它路由到配置的待审 (to-review) 目录由人工 triage（这是预期行为，不算错误）。MCP server 自身只负责持久化；路由由下游决定。

## Step 1：检测 Agent

挑选平台分支。**优先级：项目配置 > 活跃 session > 历史文件**。

```bash
if [ -f ".opencode/opencode.json" ] || [ -f ".agents/skills" ]; then
  AGENT=opencode
elif [ -f ".mcp.json" ] || [ -f ".claude/settings.json" ] || [ -d ".claude" ]; then
  AGENT=claude-code
elif command -v opencode >/dev/null 2>&1 \
     && opencode session list -n 1 --format json 2>/dev/null | jq -e '.[0].id' >/dev/null 2>&1; then
  AGENT=opencode
else
  # Claude Code 把 cwd 编码成项目目录名时, 同时把 `/` 和 `_` 都替换成 `-`
  # (例: /mnt/ws/claude_code/x → -mnt-ws-claude-code-x), 故 sed 要同时替换两者。
  ENC_CWD="$(pwd | sed 's/[/_]/-/g')"
  CC_DIR="$HOME/.claude/projects/$ENC_CWD"
  # 兜底: 编码规则有其他边角 (如 `.`) 时按 basename 反查
  if [ ! -d "$CC_DIR" ]; then
    CC_DIR=$(find "$HOME/.claude/projects" -maxdepth 1 -type d \
             -name "*$(basename "$(pwd)")" 2>/dev/null | head -1)
  fi
  if [ -n "$CC_DIR" ] && [ -d "$CC_DIR" ] && ls "$CC_DIR"/*.jsonl >/dev/null 2>&1; then
    AGENT=claude-code
  else
    echo "No supported agent transcript found"; exit 1
  fi
fi
echo "Agent: $AGENT"
```

之后：若 `AGENT=claude-code` → 走 **2A**；若 `AGENT=opencode` → 走 **2B**。

## 解析 skill 基础目录

`scripts/` 下所有脚本**就地**在本 skill 的基础目录里运行 —— **不要** Read+Write 到 `/tmp`（之前那种做法每次浪费约 4K 输出 token）。在 Bash 会话最开始设一次 `SKILL_DIR`，之后所有步骤都引用它：

```bash
SKILL_DIR="<在 'Base directory for this skill:' 处显示的绝对路径>"
```

把 agent 在 skill 加载头部给出的字面路径填进去。Claude Code 会作为单行前缀输出；OpenCode 把 skill 内容包在 `<skill_content name=...>` 里，可能不会暴露绝对路径 —— 这种情况下退回到 `find ~ /usr/local /opt -maxdepth 6 -type d -name session-upload 2>/dev/null | head -1`。

## Step 2A：Claude Code 路径

```bash
# Claude Code 编码 cwd 时把 `/` 和 `_` 都替换成 `-`, 故 sed 要同时替换两者。
ENC_CWD="$(pwd | sed 's/[/_]/-/g')"
CC_DIR="$HOME/.claude/projects/$ENC_CWD"
# 兜底: 编码有其他边角时按 basename 反查项目目录
if [ ! -d "$CC_DIR" ]; then
  CC_DIR=$(find "$HOME/.claude/projects" -maxdepth 1 -type d \
           -name "*$(basename "$(pwd)")" 2>/dev/null | head -1)
fi
LATEST=$(ls -t "$CC_DIR"/*.jsonl 2>/dev/null | head -1)
SESSION_ID=$(basename "$LATEST" .jsonl)

python3 "$SKILL_DIR/scripts/cc_convert.py" "$LATEST" > /tmp/session_output.md
```

输出：`/tmp/session_output.md`。继续 **Step 3（上传）**。

## Step 2B：OpenCode 路径

```bash
SESSION_ID=$(opencode session list -n 1 --format json | jq -r '.[0].id')
opencode export "$SESSION_ID" 2>/dev/null | python3 "$SKILL_DIR/scripts/oc_convert.py" > /tmp/session_output.md
```

输出：`/tmp/session_output.md`。继续 **Step 3（上传）**。

## Step 3：通过 MCP 上传

MCP 工具签名就是这样：

```
wiki_submit_trajectory(session_id: str, content: str) -> dict
```

Server 把字节原样落到 `<trajectory.uploaded_dir>/<上传日期>/{session_id}.md`（**按上传当天 `YYYY-MM-DD` 分目录归档**，路径来自 server 的 `config.yaml`）；下游的脱敏和抽取由 knowledge engine 的 monitor 进程负责。

**按日期分目录**：`mcp_upload.py` 默认取本机当天日期作为子目录前缀（`--date` 缺省即今天），**无需手动传**；同一天上传的轨迹自动汇总到同一个日期目录下。需要回填到指定日期时用 `--date YYYY-MM-DD`（server 端再校验，非法/缺省回退到 server 当天）。

**按平台加前缀**：为区分两端来源，上传文件名按平台加前缀 —— Claude Code → `claudecode-{session_id}.md`，OpenCode → `opencode-{session_id}.md`。前缀由 `mcp_upload.py` 的 `--agent` 参数统一施加（幂等：已带前缀不会重复叠加），**不要**自己手动改 `SESSION_ID`。

**不要直接以 `tool_use` 形式调用 `wiki_submit_trajectory`。** 长 `content`（>~13KB）会被静默截断 —— 整个 JSON tool_use payload（含 `content`）必须塞进模型的 max-output-tokens 预算里。已观察到的截断：38KB 渲染轨迹只有 13.4KB 到了 server。改用辅助脚本 `scripts/mcp_upload.py` —— 它从 Bash 子进程 POST 到 MCP HTTP endpoint，payload 走本地 socket，完全绕开 output-token 上限（已在 250KB 上验证字节级一致）。

直接从 `$SKILL_DIR`（上一节已设）运行，不需要复制：

```bash
python3 "$SKILL_DIR/scripts/mcp_upload.py" --file /tmp/session_output.md --session-id "$SESSION_ID" --agent "$AGENT"
```

`--agent` 取 Step 1 检测出的 `$AGENT`（`claude-code` 或 `opencode`），脚本据此给落盘文件名加 `claudecode-` / `opencode-` 前缀。

成功时输出一行：`OK <末级目录>/<文件名>`（如 `OK 2026-06-04/claudecode-xxx.md`，末级目录即上传日期）—— 脚本只回显末级目录加文件名，**不暴露完整绝对路径**。Server 报错或返回意外响应时，脚本原样打印 server payload 并以非零退出码退出 —— 原样转给用户，不要改写。

脚本按以下优先级解析 MCP URL：`--url` 参数 > `$CANN_WIKI_MCP_URL` > agent MCP 配置（向上层目录找 `.mcp.json` / `.opencode/opencode.json` 里 `cann-wiki` 条目的 `url`，再退到 `~/.claude.json`）> 默认 `http://113.46.4.206:8767/mcp`。

**实际效果**：跑过 `/setup-cann-wiki` 之后，端口选什么这里就用什么，**不需要再手动设环境变量**。

**禁止**：
- 把 `wiki_submit_trajectory` 当 tool_use 直接调用（如上所述会截断）
- 把内容替换成 "[Full session uploaded]" 之类的摘要
- 上传前手动截断文件
- 给 MCP 工具传 `file_path=` 或 `source=` —— 这些参数在 server 上不存在

## Step 4：汇报结果

```
✓ Uploaded
- Agent:   claude-code | opencode
- Session: {session_id}
- Path:    {上传日期}/claudecode-{session_id}.md | {上传日期}/opencode-{session_id}.md
           （只展示 `<末级目录>/<文件名>`，末级目录=上传日期 `YYYY-MM-DD`，**不显示完整绝对路径**；文件名前缀按平台，见 Step 3）
- Pipeline: knowledge_engine 自动脱敏 + 抽取
```

## 错误处理

| 场景 | 处理 |
|---|---|
| `~/.claude/projects/<cwd>/*.jsonl` 不存在且没有 `opencode` CLI | "找不到 agent 轨迹来源 —— 请在 Claude Code 内运行，或安装 opencode" |
| MCP 未配置 | "先跑 `/setup-cann-wiki`" |
| `wiki_submit_trajectory` 未注册 | "MCP 工具缺失 —— setup 后请重启 agent" |
| 轨迹为空 | "没有消息可上传" |
| JSON/JSONL 解析错误 | "跳过坏行，继续" |
| Server 返回 `{status: "error", message: "..."}` | 把 `message` 字段原样转给用户 —— 不要改写或吞掉 |
| 网络 / API 错误 | "网络错误，稍后重试" |

## 注意事项

- **平台隔离的转换器** —— `cc_convert.py` 和 `oc_convert.py` 互不知情。改一个不会破坏另一个。
- **新增平台** —— 在 `scripts/` 下加一个 `<name>_convert.py`（签名：读轨迹源，把 Markdown 打到 stdout），并加一节 `Step 2X`。SKILL.md 保持精简。
- **格式对齐** —— 两个转换器输出同一种 Markdown 布局，下游抽取统一。
- **保留 thinking** —— 保留 `thinking` 块（Claude Code）和 `reasoning` 部分（OpenCode）。
- **工具细节原样保留** —— 所有 tool input/output 完整保留。转换器的 `_PLUMBING_BASENAMES` 过滤器按 basename 剥离那些罕见的、模型自己 Read/Write 转换器 / 上传器脚本的情形（调试场景），与源路径无关。正常流程不再把脚本复制到 `/tmp`，所以这个过滤器更像安全网而非主链路。
