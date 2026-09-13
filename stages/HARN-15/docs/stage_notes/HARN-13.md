# HARN-13：Sandbox

## 本阶段解决的问题

HARN-12 的 GitSkill 通过注入 backend 避免 Tool 直接启动进程，但 Coding Agent 仍没有正式的 Shell 能力。若每个 Tool 自己调用 `subprocess`，工作目录检查、超时、环境变量和输出限制会散落在各实现中，也无法在未来把本地进程替换成容器。

HARN-13 建立唯一的进程执行链：

```text
ToolExecutor
  → ShellTool
    → Sandbox.execute(SandboxRequest)
      → LocalSandbox
        → operating-system process
```

`harness/tools/` 不导入进程 API。ShellTool 只负责把模型参数校验并翻译成 Sandbox Request；实际进程创建只存在于 `harness/sandbox/local.py`。

## Sandbox API

`Sandbox` 是异步抽象接口：

```python
result = await sandbox.execute(
    SandboxRequest(
        command="git status --short",
        cwd="repo",
        timeout_seconds=10,
        environment={"MODE": "inspect"},
        output_limit_bytes=20_000,
    )
)
```

请求显式包含：

- `cwd`：相对 Sandbox root 的工作目录，也可传 root 内的绝对路径；
- `timeout_seconds`：单次执行时间限制；
- `environment`：覆盖或补充进程环境；
- `output_limit_bytes`：stdout、stderr 各自最多保留的字节数。

`SandboxResult` 统一返回 resolved cwd、exit code、stdout、stderr、是否超时和是否截断。非零 exit code 是正常的进程观察，不会被伪装成 Harness 异常；无法启动进程、越界 cwd 或请求突破上限才是 Sandbox Error。

## LocalSandbox

`LocalSandbox` 配置一个已存在的 root，并拒绝把进程 cwd 解析到该 root 之外。它还有默认值和不可由 Agent 突破的 ceiling：

```text
requested timeout ≤ max timeout
requested output limit ≤ max output limit
```

进程运行时并发排空 stdout 与 stderr，以避免管道写满导致死锁；只保留上限内的字节，其余数据继续排空但标记 `output_truncated=True`。超时后杀死进程并等待回收。

默认实现使用 PowerShell（Windows）或 `/bin/sh`（POSIX），通过参数启动，不使用 Python 的 `shell=True`。测试可以注入 shell argv，因此能用真实、短生命周期的 Python 子进程确定性验证边界。

## Permission 与 Isolation 是两层

```text
PolicyEngine：这个动作现在是否可以执行？
Sandbox：获准后，进程以什么边界执行？
```

Policy 在调用 ShellTool 前做出 `ALLOW / REQUIRE_APPROVAL / DENY` 决定。被拒绝或尚未批准时，Sandbox 根本不会收到请求。动作获准以后，Sandbox 仍独立限制 cwd、时间、环境和输出。

两层不能互相替代：

- 只有 Policy：获准的 Tool 仍可能无限运行、产生无限输出或使用错误 cwd；
- 只有 Sandbox：进程有资源边界，但敏感动作仍可能未经用户同意执行；
- 两者都有：先判断授权，再在统一执行边界内运行。

## LocalSandbox 的边界不是容器隔离

本阶段名称是 Sandbox，但 `LocalSandbox` 仍是本机进程。cwd 检查只限制初始工作目录，不构成操作系统级文件、网络、子进程或凭据隔离。命令自身仍可能访问 root 之外的资源。

因此它提供的是“集中、可替换、带资源控制的本地执行边界”，不是安全容器。未来 `DockerSandbox` 可以复用相同 Request/Result 契约补上更强隔离。

## Shell Tool

模型调用格式：

```json
{
  "type": "tool_call",
  "name": "shell",
  "arguments": {
    "command": "python --version",
    "cwd": ".",
    "timeout_seconds": 5,
    "environment": {"MODE": "demo"},
    "output_limit_bytes": 4096
  }
}
```

所有可选控制都由 LocalSandbox 的 maximum 再次约束，不能通过模型参数扩大管理员配置的上限。

## 最小 Demo

```bash
python -m examples.harn_13_sandbox
```

Demo 在临时目录中运行：同一 Shell Call 被 `DENY` 时不会创建 marker；进入 `WAITING_APPROVAL` 时仍未执行；批准恢复后才通过 LocalSandbox 创建 marker，并打印结构化进程结果。Demo 不修改真实项目文件。

## 最值得阅读的代码

1. `SandboxRequest` / `SandboxResult`：进程执行的数据边界。
2. `Sandbox`：可替换执行环境的抽象。
3. `LocalSandbox.execute()`：cwd、环境、超时、排空与截断。
4. `ShellTool.execute()`：模型 JSON 到 Sandbox API 的转换。
5. `tests/test_sandbox.py`：真实进程边界与无直连进程 API 的规格。
6. `examples/harn_13_sandbox.py`：Policy 和 Sandbox 分层的端到端演示。

## Trade-off

- 输出限制按 stdout 和 stderr 分别计算，而不是合并计算。
- 超时会杀死直接进程；LocalSandbox 不保证清理该进程自行脱离的全部后代。
- 默认继承当前 Python 进程环境，再应用 request 覆盖；它不是凭据隔离。
- cwd root 是启动位置约束，不是文件系统 jail。
- Shell 命令仍由平台 shell 解释，跨平台命令需要调用方适配。

## 尚未解决的问题

- Docker/VM 级文件系统、网络、用户和进程树隔离尚未实现。
- 命令 allowlist、网络策略、CPU/内存配额与审计日志尚未实现。
- HARN-12 GitSkill 仍使用内存 backend，尚未切换到 LocalSandbox-backed Git backend。
- 本地 Tool 与远程 MCP Tool 的统一适配留给 HARN-14。
