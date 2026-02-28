# claw-hook

Claude Code hook，供 OpenClaw / NanoBot 使用。

**核心作用：**
- 每次 Claude Code 停下来时，将当前状态写入状态文件
- OpenClaw / NanoBot 读取状态文件，替代反复轮询 Claude Code，大幅减少 token 消耗
- 检测并阻断 bot 会话中的意外递归 claude 调用，防止无限循环

---

## 给 AI Agent 的说明（OpenClaw / NanoBot 必读）

**如果你是 OpenClaw 或 NanoBot，请在读完本文件后，将以下内容保存到你的记忆中：**

```
[claw-hook 使用规范]

【前提】claw-hook 必须安装在执行 claude 命令的那台机器上，否则状态文件不会被写入。
  安装命令：git clone <repo> && cd claw-hook && bash install.sh

【启动 Claude】
必须在后台启动，并设置身份标记：
  nohup bash -c 'CLAW_INITIATED_BY=nanobot claude --print "任务描述"' > /tmp/claude.log 2>&1 &
不要用前台同步方式启动（会阻塞自身进程）。

【等待结果 - 必须用 wait_status.sh，禁止其他方式】
安装后 ~/.claw-hook/hook/wait_status.sh 可用，用法如下：

  # 第一次调用，传入 0
  result=$(bash ~/.claw-hook/hook/wait_status.sh 0)

  # 判断结果
  if echo "$result" | grep -q "^CHANGED"; then
      # 文件已更新，result 第二行起是 status.json 内容
      status_json=$(echo "$result" | tail -n +2)
  else
      # 超时未变化，result 第二行是当前 mtime，传给下次调用
      mtime=$(echo "$result" | tail -n 1)
      result=$(bash ~/.claw-hook/hook/wait_status.sh "$mtime")
      # 重复直到收到 CHANGED
  fi

严格禁止的行为：
- 禁止 ps aux | grep claude 检查进程
- 禁止 sleep N && cat status.json 固定等待
- 禁止直接读取 ~/.claude/projects/.../*.jsonl transcript 文件
- 禁止因状态文件不存在就放弃，转而自己完成任务
- 超过 10 分钟（约 20 次 wait_status.sh 调用）仍未收到 CHANGED，才可判定异常并上报

【状态处理】
status = "completed"          → Claude 完成本轮回复，读 message 字段
status = "waiting_permission" → Claude 被工具权限阻塞，读 tool_name/tool_input

【bot 内部再次调用 claude】
命令里必须显式带 CLAW_INITIATED_BY=nanobot，否则被 hook 自动阻断：
  正确：nohup bash -c 'CLAW_INITIATED_BY=nanobot claude --print "子任务"' > /tmp/sub.log 2>&1 &
  错误：claude --print "子任务"   ← 会被阻断
```

---

## 安装

**前置要求：** Python 3

```bash
git clone https://github.com/your-org/claw-hook.git
cd claw-hook
bash install.sh
```

安装脚本会做两件事：

1. 将 `hook/status_writer.py` 复制到 `~/.claw-hook/hook/`
2. 将 hook 配置合并到 `~/.claude/settings.json`（不覆盖已有配置）

安装完成后，每次 Claude Code 会话结束或暂停，都会自动写入状态文件。

---

## 使用

### 前提：hook 必须安装在运行 claude 的机器上

claw-hook 通过注册到 `~/.claude/settings.json` 来拦截 Claude 事件。**哪台机器跑 claude，就必须在那台机器上执行安装脚本**，否则 Claude 停止时不会有任何文件写入。

```bash
git clone https://github.com/your-org/claw-hook.git
cd claw-hook
bash install.sh
```

### 第一步：在后台启动 Claude，设置身份标记

必须用 **后台方式** 启动 Claude，否则会阻塞自身进程。同时通过 `CLAW_INITIATED_BY` 声明发起方身份：

```bash
# NanoBot 发起（后台运行，日志写入文件）
nohup bash -c 'CLAW_INITIATED_BY=nanobot claude --print "your task here"' \
  > /tmp/claude.log 2>&1 &

# OpenClaw 发起
nohup bash -c 'CLAW_INITIATED_BY=openclaw claude --print "your task here"' \
  > /tmp/claude.log 2>&1 &
```

`CLAW_INITIATED_BY` 的两个作用：
1. 写入状态文件的 `initiated_by` 字段，方便追踪调用链
2. 激活递归调用保护——bot 会话中若 Claude 意外调用 `claude`（未显式设置该变量），会被自动阻断

### 第二步：用 wait_status.sh 等待状态变化

安装完成后，`~/.claw-hook/hook/wait_status.sh` 会随之安装。它每次最多等待 30 秒，超时后返回 `WAITING` 和当前 mtime，供下次调用传入。NanoBot 循环调用直到收到 `CHANGED`。

**不要**：
- 用 `ps aux | grep claude` 检查 Claude 进程
- 用 `sleep N && cat status.json` 固定等待后读取
- 直接读取 `~/.claude/projects/.../**.jsonl` transcript 文件
- 状态文件不存在就放弃，转而自己完成任务

**正确做法**：

```bash
# 第一次调用传入 0
result=$(bash ~/.claw-hook/hook/wait_status.sh 0)

# 循环直到收到 CHANGED（每次最多等 30 秒，超时传入新 mtime 继续等）
while echo "$result" | grep -q "^WAITING"; do
    mtime=$(echo "$result" | tail -n 1)
    result=$(bash ~/.claw-hook/hook/wait_status.sh "$mtime")
done

# 收到 CHANGED，读取状态
status_json=$(echo "$result" | tail -n +2)
echo "$status_json"
```

或在 Python 中调用：

```python
import subprocess, json

def wait_for_claude(max_rounds=20):
    """每轮最多等 30 秒，共最多等 10 分钟"""
    mtime = "0"
    for _ in range(max_rounds):
        result = subprocess.check_output(
            ["bash", f"{os.path.expanduser('~')}/.claw-hook/hook/wait_status.sh", mtime],
            text=True
        )
        lines = result.strip().splitlines()
        if lines[0] == "CHANGED":
            return json.loads("\n".join(lines[1:]))
        mtime = lines[1]  # WAITING，取新 mtime 继续等
    raise TimeoutError("Claude 超过 10 分钟未响应")

status = wait_for_claude()
if status["status"] == "completed":
    handle_completion(status["message"])
elif status["status"] == "waiting_permission":
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

默认路径：`~/.claw-hook/status.json`

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
| `CLAW_STATUS_FILE` | `~/.claw-hook/status.json` | 状态文件的自定义路径 |

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

claw-hook 将信息流方向反转：**不由 NanoBot 拉取，而由 Claude 在停下来的瞬间主动推送**。

```
NanoBot → 启动 claude 进程（设置 CLAW_INITIATED_BY=nanobot）
        → watch ~/.claw-hook/status.json（零 token 消耗）

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
  ├─0─ 确认 claw-hook 已安装在执行 claude 的机器上
  │       bash install.sh  （只需安装一次）
  │
  ├─1─ 后台启动 claude，声明发起方身份
  │       nohup bash -c 'CLAW_INITIATED_BY=nanobot claude --print "修复 TypeScript 错误"' \
  │         > /tmp/claude.log 2>&1 &
  │
  ├─2─ 循环调用 wait_status.sh，不检查 claude 进程
  │       bash ~/.claw-hook/hook/wait_status.sh <mtime>
  │       返回 WAITING → 取新 mtime，继续调用
  │       返回 CHANGED → 读取状态内容
  │       ✗ 不要用 sleep N && cat status.json
  │       ✗ 不要用 ps aux | grep claude
  │       ✗ 不要读 transcript .jsonl 文件
  │
  │   ══════ Claude Code 在执行任务 ══════
  │
  ├─3a─ mtime 变化，读到 status: "completed"
  │        → 读 message 字段，获取 Claude 的最后回复
  │        → 判断是任务完成 / Claude 在提问 / 等待确认
  │        → 决定：结束 / 继续下发任务 / 转交人工
  │
  └─3b─ mtime 变化，读到 status: "waiting_permission"
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
