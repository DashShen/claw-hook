# claw-hooks

Claude Code hook，供 OpenClaw / NanoBot 使用。

**核心作用：**
- 每次 Claude Code 停下来时，将当前状态写入状态文件
- OpenClaw 读取状态文件，替代反复轮询 Claude Code，大幅减少 token 消耗
- 检测并阻断 bot 会话中的递归 claude 调用，防止无限循环

---

## 安装

**前置要求：** Python 3

```bash
git clone https://github.com/your-org/claw-hooks.git
cd claw-hooks
bash install.sh
```

安装脚本会做两件事：

1. 将 `hooks/status_writer.py` 复制到 `~/.claw-hooks/hooks/`
2. 将 hook 配置合并到 `~/.claude/settings.json`（不覆盖已有配置）

安装完成后，每次 Claude Code 会话结束或暂停，都会自动写入状态文件。

---

## 使用

### OpenClaw / NanoBot 启动 Claude 时

设置 `CLAW_INITIATED_BY` 环境变量，告知 hook 本次会话由谁发起：

```bash
# OpenClaw 发起
CLAW_INITIATED_BY=openclaw claude --print "your task here"

# NanoBot 发起
CLAW_INITIATED_BY=nanobot claude --print "your task here"
```

这个环境变量有两个作用：
1. 写入状态文件的 `initiated_by` 字段，调用方可据此追踪调用链
2. 启用递归调用检测——bot 会话中若 Claude 尝试再次调用 `claude`，会被自动阻断

### 读取状态文件

Claude Code 每次停下来后，`~/.claw-hooks/status.json` 会被更新。OpenClaw 读取该文件即可感知状态：

```python
import json

with open(os.path.expanduser("~/.claw-hooks/status.json")) as f:
    status = json.load(f)

if status["status"] == "completed":
    # Claude 完成本轮回复，message 字段包含最后一条回复摘要
    handle_completion(status["message"])

elif status["status"] == "waiting_permission":
    # Claude 被工具权限请求阻塞，等待确认
    # tool_name / tool_input 字段包含具体的工具调用信息
    handle_permission(status["tool_name"], status["tool_input"])
```

---

## 状态文件格式

默认路径：`~/.claw-hooks/status.json`

```json
{
  "session_id": "abc123",
  "timestamp": "2026-02-27T12:00:00.000Z",
  "status": "completed",
  "cwd": "/path/to/project",
  "message": "Claude 最后一条回复的摘要（最长 500 字符）",
  "tool_name": null,
  "tool_input": null,
  "hook_event": "Stop",
  "transcript_path": "/Users/.../.claude/projects/.../session.jsonl",
  "initiated_by": "openclaw"
}
```

### status 字段取值

| status | 触发时机 | 说明 |
|---|---|---|
| `completed` | Claude 完成本轮回复 | 包括任务完成、提问、等待用户输入等所有正常停止情形 |
| `waiting_permission` | Claude 发起工具权限请求 | `tool_name` / `tool_input` 字段包含具体信息 |

> `completed` 并不一定意味着任务完全结束，也可能是 Claude 在等待用户确认某件事。
> OpenClaw 可通过 `message` 字段的内容判断下一步动作（如是否需要转交人工）。

### `waiting_permission` 时的附加字段

| 字段 | 说明 |
|---|---|
| `tool_name` | 请求权限的工具名，如 `Bash`、`Write` |
| `tool_input` | 该工具调用的完整参数 |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLAW_INITIATED_BY` | `human` | 会话发起方，设为 `openclaw` 或 `nanobot` 启用递归调用阻断 |
| `CLAW_STATUS_FILE` | `~/.claw-hooks/status.json` | 状态文件的自定义路径 |

---

## 工作原理

hook 监听三个 Claude Code 事件：

| 事件 | 触发条件 | hook 动作 |
|---|---|---|
| `Stop` | Claude 完成本轮回复 | 读取会话记录末尾，写入 `completed` 状态 |
| `PermissionRequest` | Claude 需要工具权限确认 | 写入 `waiting_permission` 状态，不干预权限决策 |
| `PreToolUse` (Bash) | Claude 执行 shell 命令前 | 若为 bot 会话且命令中包含 `claude` 调用，拒绝执行 |

状态文件采用原子写入（先写 `.tmp` 再重命名），OpenClaw 不会读到写入一半的文件。
