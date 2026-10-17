# Autonomous Code Repair Agent — Phase 1

用 OpenHands Software Agent SDK 实现 `issue → 读取代码 → 修复 → 跑测试 → 输出 patch`。
这是完整项目的第一阶段：先建立可重复执行和验证的基线，再扩展任务、sandbox 和评估。

**当前状态：** SDK 配置与外部验证器已验证；尚未在你的模型账户上完成真实 Agent 修复。
`smoke` 使用已知的人工修复来检查验证器，不代表 Agent 修复成功，不是 benchmark 分数。

## 在 Mac 上启动

解压后，把 `code-repair-agent` 文件夹放到你的项目目录，用 VS Code 打开该文件夹。
以下命令在这个文件夹的终端执行。需要 Git、uv 和 Python 3.12 或 3.13。

```bash
uv sync --python 3.13
uv run python repair.py doctor
uv run python repair.py smoke
cp .env.example .env
```

`doctor` 应显示 SDK/tools 都是 `1.53.0`。本项目固定两个包的相同版本，避免不匹配。
`smoke` 应显示 3 个 baseline failures，以及人工已知 patch 的 75 tests passed。

默认 `.env` 使用订阅登录：

```dotenv
REPAIR_AUTH=subscription
OPENHANDS_SUBSCRIPTION_MODEL=gpt-5.2-codex
REPAIR_MAX_ITERATIONS=20
```

这里显式传入模型名称，避免 SDK 的订阅模型参数缺失。如果你已经跑通了别的
订阅模型，就把 `OPENHANDS_SUBSCRIPTION_MODEL` 改为你已验证可用的名称。
第一次运行时 SDK 会启动本机 OAuth 登录；你已登录时可复用 SDK 的本地凭据。
模型可用性以你账户和 SDK 的实际返回为准。

然后跑第一个真实 Agent 任务：

```bash
uv run python repair.py prepare --id run-001
uv run python repair.py run --id run-001
```

`prepare` 的 **3 failed, 3 passed 是预期的 bug 复现**。
`run` 结束后，由验证器输出 `verification_status`。只有 `passed` 才说明 patch
通过本任务的公共测试与补充测试；Agent 自己说“完成”不算验收。

重新尝试请用新 ID，不要覆盖已有运行：

```bash
uv run python repair.py prepare --id run-002
uv run python repair.py run --id run-002
```

如果使用 API，把 `.env` 改为 `REPAIR_AUTH=api`，填写你实际有权限调用的
`LLM_MODEL` 和 `LLM_API_KEY`；`LLM_BASE_URL` 仅在需要时填写。
模型遵循 SDK/LiteLLM 的 provider/model 命名。不要把 `.env` 提交到 GitHub。

## 第一轮实际做什么

任务仓库包含一个发送前分批的函数 `make_batches`。它能处理完整批次，但会漏掉
最后不足一批的记录。Agent 拿到 issue 和 6 个现有测试，自行阅读代码、重现问题、
修改 `batching.py`，再运行测试。项目代码没有写死修复答案；仅 `smoke` 有人工已知
修复，用于检查整个验证流程。

验证器执行：

1. 相对于记录下来的原始 commit 提取 diff。
2. 检查是否只修改了允许的源文件；修改 tests/config 会失败。
3. 将 patch 应用到全新的原始仓库副本。
4. 保留原始公共测试，加上补充 contract 测试，验证边界输入和不丢记录。
5. 保存测试退出码、日志、patch 和判定。

补充测试放在 Agent 工作目录之外，这是代码职责分离，**不是安全隔离，也不是秘密测试**。
Phase 1 仅支持我们提供的可信 Python fixture；本地终端工具有宿主机权限，prompt
里的路径/联网限制不是强制安全边界。Docker sandbox 是下一阶段，完成前不要用来
运行陌生仓库。当前 20 iterations 是循环上限，不是美元花费或总时长上限。

## 文件结构

```text
code-repair-agent/
  repair.py                  # prepare / run / verify / doctor / smoke
  pyproject.toml             # 固定匹配的 SDK 版本
  uv.lock                    # 可复现依赖
  .env.example               # 无凭据的配置样例
  fixtures/batch-service/    # 已知带 bug 的测试仓库
  tasks/                     # issue 和可修改文件清单
  evaluator/                 # 外部补充测试
  tests/                     # 验证器自身的测试
  docs/phase-1.md             # 本阶段学习与验收
  runs/                      # 运行后生成；不进 Git
```

每次运行输出到 `runs/<id>/`：

| 文件 | 用途 |
|---|---|
| `workspace/` | Agent 实际修改的仓库副本 |
| `manifest.json` | 任务、原始 commit、baseline 退出码 |
| `baseline-tests.txt` | 修复前的失败证据 |
| `events.jsonl` | 事件类型与时间；不保存 prompt、工具内容或推理文本 |
| `agent-run.json` | 模型、认证方式、迭代上限、执行状态、Agent 耗时 |
| `patch.diff` | 相对原始 commit 的修复 |
| `verification-tests.txt` | 外部验证的测试结果，实际进入测试时生成 |
| `verification.json` | 验收状态及失败原因 |

SDK 会在终端展示交互；这里的 `events.jsonl` 仅是事件元数据，不是完整 tracing。
Agent 运行出现异常时，`agent-run.json` 会记录异常类型。可以单独运行
`uv run python repair.py verify --id run-001` 检查其留下的 patch。

验证器自身检查：

```bash
uv run python -m pytest -q
```

## 下一阶段

先亲自跑通并解释一个真实修复。随后依次添加 Docker sandbox、多个独立任务、
失败分类与有限重试、完整工具事件与成本统计、固定评测集和 baseline 比较。
有测量证据后再判断是否需要 Planner/Coder/Verifier 多 Agent。
FastAPI、GitHub issue 接入和 UI 放到核心流程稳定之后。

不要把规划中的功能或人工 smoke 结果写成简历上已完成的成果。

## 官方参考

- SDK: https://github.com/OpenHands/software-agent-sdk
- 安装: https://docs.openhands.dev/sdk/getting-started
- 订阅认证: https://docs.openhands.dev/sdk/guides/llm-subscriptions
- Conversation: https://docs.openhands.dev/sdk/api-reference/openhands.sdk.conversation
