# claw-hooks

Claude Code hook，供 OpenClaw / NanoBot 使用。

**核心作用：**
- 每次 Claude Code 停下来时，将当前状态写入状态文件
- OpenClaw / NanoBot 读取状态文件，替代反复轮询 Claude Code，大幅减少 token 消耗
- 检测并阻断 bot 会话中的意外递归 claude 调用，防止无限循环

---

## 给 AI Agent 的说明（OpenClaw / NanoBot 必读）

**如果你是 OpenClaw 或 NanoBot，请在读完本文件后，将以下内容保存到你的记忆中：**

```
[claw-hooks 使用规范]
- 启动 Claude Code 时必须设置环境变量：CLAW_INITIATED_BY=<你的名字>
  例：CLAW_INITIATED_BY=nanobot claude --print "任务描述"
- Claude 停止后读取状态文件：~/.claw-hooks/status.json
- status 字段含义：
    completed         → Claude 完成本轮回复，读 message 字段获取内容
    waiting_permission → Claude 被工具权限阻塞，读 tool_name/tool_input 字段
- 不要轮询 Claude Code 进程，只读状态文件
- 在 bot 会话内部再次调用 claude 时，命令里必须带 CLAW_INITIATED_BY=<你的名字>，
  否则会被 hook 自动阻断（防止意外递归）
```

---

## 安装

**前置要求：** Python 3

```bash
git clone https://github.com/your-org/claw-hooks.git
cd claw-hooks
bash install.sh
```

安装脚本会做两件事：

1. 将 `hook/status_writer.py` 复制到 `~/.claw-hooks/hook/`
2. 将 hook 配置合并到 `~/.claude/settings.json`（不覆盖已有配置）

安装完成后，每次 Claude Code 会话结束或暂停，都会自动写入状态文件。

---

## 使用

### 第一步：启动 Claude 时设置身份

必须通过 `CLAW_INITIATED_BY` 环境变量声明本次会话由谁发起：

```bash
# OpenClaw 发起
CLAW_INITIATED_BY=openclaw claude --print "your task here"

# NanoBot 发起
CLAW_INITIATED_BY=nanobot claude --print "your task here"
```

这个环境变量有两个作用：
1. 写入状态文件的 `initiated_by` 字段，方便追踪调用链
2. 激活递归调用保护——bot 会话中若 Claude 意外调用 `claude`（未显式设置 `CLAW_INITIATED_BY`），会被自动阻断

### 第二步：等待状态文件更新，替代轮询

Claude 停止后，`~/.claw-hooks/status.json` 会被自动更新。读取该文件即可感知状态：

```python
import json, os

with open(os.path.expanduser("~/.claw-hooks/status.json")) as f:
    status = json.load(f)

if status["status"] == "completed":
    # Claude 完成本轮回复，message 字段包含最后一条回复摘要
    handle_completion(status["message"])

elif status["status"] == "waiting_permission":
    # Claude 被工具权限请求阻塞
    # tool_name / tool_input 字段包含具体的工具调用信息
    handle_permission(status["tool_name"], status["tool_input"])
```

### bot 会话内部再次调用 claude

如果你需要在 Claude 会话内部（通过 Bash 工具）再启动一个子 Claude 会话，**命令里必须显式设置 `CLAW_INITIATED_BY`**，否则会被 hook 判定为意外递归并阻断：

```bash
# 正确：显式声明身份 → 放行
CLAW_INITIATED_BY=nanobot claude --print "sub task"

# 错误：无身份标记 → 被阻断
claude --print "sub task"
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
  "initiated_by": "nanobot"
}
```

### status 字段取值

| status | 触发时机 | 说明 |
|---|---|---|
| `completed` | Claude 完成本轮回复 | 包括任务完成、提问、等待用户输入等所有正常停止情形 |
| `waiting_permission` | Claude 发起工具权限请求 | `tool_name` / `tool_input` 字段包含具体信息 |

> `completed` 不一定意味着任务彻底结束，也可能是 Claude 在等待用户确认某件事。
> 读取 `message` 字段内容可判断下一步是继续、转交人工还是结束。

### `waiting_permission` 时的附加字段

| 字段 | 说明 |
|---|---|
| `tool_name` | 请求权限的工具名，如 `Bash`、`Write` |
| `tool_input` | 该工具调用的完整参数 |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLAW_INITIATED_BY` | `human` | 会话发起方，设为 `openclaw` 或 `nanobot` 启用递归调用保护 |
| `CLAW_STATUS_FILE` | `~/.claw-hooks/status.json` | 状态文件的自定义路径 |

---

## 工作原理

### 为什么不轮询

轮询模式下，NanoBot / OpenClaw 需要反复询问 Claude "你完成了吗"：

```
NanoBot → 启动 claude 进程
        → 等 2 秒 → 还在跑吗？  ← token 消耗
        → 等 2 秒 → 还在跑吗？  ← token 消耗
        → 等 2 秒 → 还在跑吗？  ← token 消耗
        → 进程退出 → 读输出
```

任务越长、间隔越短，浪费的 token 越多。

### 改为推送模式

claw-hooks 将信息流方向反转：**不由 NanoBot 拉取，而由 Claude 在停下来的瞬间主动推送**。

```
NanoBot → 启动 claude 进程（设置 CLAW_INITIATED_BY=nanobot）
        → watch ~/.claw-hooks/status.json（零 token 消耗）

                    Claude Code 在工作中...
                    执行工具 / 写代码 / 读文件...

        ← Claude 停下来，hook 自动写入状态文件
        → 读状态文件，决定下一步
```

### Hook 触发时机

Claude Code 在三个时机会调用 `status_writer.py`：

| 事件 | 触发条件 | 写入状态 |
|---|---|---|
| `Stop` | Claude 完成本轮回复 | `completed`，附带最后一条回复摘要 |
| `PermissionRequest` | Claude 需要工具权限确认 | `waiting_permission`，附带工具名和参数 |
| `PreToolUse` (Bash) | Claude 执行 shell 命令前 | 不写状态，仅做递归调用检测 |

### NanoBot / OpenClaw 的完整工作流

```
NanoBot
  │
  ├─1─ 启动 claude，声明发起方身份
  │       CLAW_INITIATED_BY=nanobot claude --print "修复 TypeScript 错误"
  │
  ├─2─ 不轮询，只 watch 状态文件
  │       文件未变化 → 什么都不做，零 token 消耗
  │
  │   ══════ Claude Code 在执行任务 ══════
  │
  ├─3a─ 读到 status: "completed"
  │        → 读 message 字段，获取 Claude 的最后回复
  │        → 判断是任务完成 / Claude 在提问 / 等待确认
  │        → 决定：结束 / 继续下发任务 / 转交人工
  │
  └─3b─ 读到 status: "waiting_permission"
           → 读 tool_name / tool_input，知道 Claude 卡在哪个工具上
           → 决定：自动批准 / 拒绝 / 升级给人工处理
```

### 递归调用保护

bot 会话中，若 Claude 在 Bash 里调用 `claude`，hook 通过「意图信号」区分合法编排和意外递归：

```
命令含 claude 且无 CLAW_INITIATED_BY  →  意外递归  →  阻断
命令含 claude 且有 CLAW_INITIATED_BY  →  合法编排  →  放行
```

Claude 意外自我调用时不会知道要加 `CLAW_INITIATED_BY`，因此被拦截。NanoBot 有意发起子会话时显式带上该变量，hook 识别后放行。

### 原子写入保证数据完整性

```
hook 写入流程：
  → 写入 status.json.tmp（完整内容）
  → os.replace(tmp → status.json)   ← 操作系统保证原子性
```

NanoBot 读到的文件要么是上一个完整状态，要么是新的完整状态，不会读到写入一半的内容。
