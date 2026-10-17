# 第三个仓库：sqlparse 历史任务

此包接在已经通过 Mac 39 项检查和 Docker smoke 的执行控制修订版之后。OpenHands SDK/tools 仍为 1.53.0。

## 放入主项目

与 repair.py 同级：

- 替换 comparison_agent.py、suite_agent.py。
- 新增 expanded_agent.py、test_expanded_tasks.py、Dockerfile.expanded。
- 把整个 expanded_tasks 文件夹复制进主项目。不要仅把它作为另一个 VS Code workspace root 打开。
- execution_control.py 与 test_execution_control.py 继续使用上一步的修订版。

原有比较和三任务入口继续使用各自任务目录；新入口 expanded_agent.py 使用新的五任务目录。新增目录会传给每一个准备/运行子进程，不会在子进程中切回旧的 dotenv 目录。

## 先验证，不调用模型

在主项目终端执行离线框架检查：

```bash
uv run python -m unittest test_expanded_tasks test_execution_control test_comparison_agent test_suite_agent -v
```

预期：46 tests，OK。测试中的模拟 failed / incomplete 不表示本次框架检查失败，以最后的 OK 为准。

然后构建新增任务镜像并运行 Docker 自检：

```bash
uv run python expanded_agent.py build
uv run python expanded_agent.py smoke --id expanded-smoke-001
```

两行按顺序执行。build 必须成功后再运行 smoke。镜像名为 code-repair-python:expanded-v1；构建上下文只发送 Dockerfile。镜像配方保持 Python 3.13，pytest/freezegun/click/mock 的固定版本与上一轮一致。这些步骤不调用模型。

自检会验证五个已知基线，再将上游参考补丁重放到干净版本，执行公开和私有补充测试，并确认登记的容器已清理。预期终端结尾为 Worker exited: 0、cleanup=confirmed、SMOKE PASSED。每个内部任务的详细输出在 runs/execution-control/expanded-smoke-001/smoke/worker.log。

相同 smoke ID 只运行一次；需要重跑时使用 expanded-smoke-002。

## 新任务的范围

| 任务 | 类型 | 基线失败 / 通过 | 参考补丁通过公开测试 | 补充测试 | 独立验证通过数 | 上游参考修改源码数 |
| --- | --- | --- | --- | --- | --- | --- |
| sqlparse-701 | 窗口函数 OVER 的 token 分组 | 1 / 433 | 434 | 19 | 453 | 2 |
| sqlparse-745 | 嵌套 ASC / DESC 的 token 分组 | 1 / 418 | 419 | 20 | 439 | 1 |
| sqlparse-782 | 分组边界的空白清理 | 1 / 436 | 437 | 15 | 452 | 1 |
| sqlparse-784 | BEGIN 内连续 CASE 的语句拆分 | 1 / 435 | 436 | 18 | 454 | 1 |
| sqlparse-742 | 可选移除语句末尾分号 | 6 / 411 | 417 | 18 | 435 | 4 |

742 是历史功能/兼容性任务；其余四个是缺陷修复任务。后续结论应注明类型，不能统一称为五个既有行为 bug。源码修改数量指上游参考补丁，不强制要求模型采用同样文件数或实现。

这里的“更复杂”指问题类型更丰富，且加入两个上游修复涉及多个文件的任务；模型是否会觉得更难，要由实验决定。

加上先前 3 个 ItsDangerous 和 2 个 python-dotenv 问题，目前准备了 3 个仓库、10 个历史 issue。新增五个尚没有真实 Agent 成功记录；框架测试、参考补丁和负向控制都不计作模型修复成绩。

## 已知排除项与验证规则

每个历史版本的 tests/ 源文件和数据原样保留。公开回归函数从对应上游修复提交逐字提取到单独模块，保留来源与 SHA-256；完整来源在各任务的 task.json 与 upstream-sha256.json。

pytest.ini 仅添加 src/ 导入路径、收集 tests/，排除依赖外部 console-script 安装的 test_cli.py，并通过 -m "not xfail" 排除上游已经标记的 3 个预期失败测试。这不是完整项目测试套件的声明。具体三个节点列在任务的 known_excluded_tests。

这 3 个用例会显示为 deselected，不计入通过数。新 verifier 逐项检查：通过数必须等于任务声明值、失败/错误/跳过必须为 0，deselected 必须恰好是 3；多排除一个测试也不通过。旧任务默认仍要求 0 deselected。CLI 模块未收集，不会进入通过数。

参考补丁与补充测试只供外层评测使用，不会种入 Agent 容器。候选补丁仍只允许修改源码白名单，再重放到新的干净容器验证。

保持已有每文件 30000 字符的读写限制和快照大小限制。部分版本的 keywords.py 超过此限制，因此在相应任务中不提供可编辑权限；本次参考修复不涉及该模块。模型可在容器内导入整个包。One-shot 提供能编辑的源码与选定公开回归上下文，Agent 可自行读取公开文件；这依然是完整工作流比较，不是完全相同输入或总计算量的消融。

## 自检通过后运行模型实验

首次每个新任务只跑一轮两种方法，共 10 次独立运行，不立刻重复三轮：

```bash
uv run python expanded_agent.py compare --prefix expanded-001
```

继续使用本机 .env 中的模型与登录方式。执行控制沿用默认每次 300 秒、SDK 单次请求 60 秒、SDK 最多两次尝试、Agent 默认 20 次迭代；订阅模式没有已确认生效的 8192 输出硬上限。每 15 秒显示当前阶段，详细原始 SDK 输出留在本机 worker.log。

结束后导出结果：

```bash
uv run python expanded_agent.py export --prefix expanded-001
open -R expanded-001-review.zip
```

运行中可以在另一个终端查询已有结果：

```bash
uv run python expanded_agent.py report --prefix expanded-001
```

失败、超时或不完整的已开始尝试仍计入分母，不自动重试一次完整实验。历史公开问题可能被模型记忆，结果只能表述为这个小型任务集上的观察。测试数不能相加成独立任务数。

## 本次验证证据

本环境已在 Python 3.13.16 / pytest 9.1.1 下执行可信上游代码：五个基线都按预期失败，五个参考补丁的公开和补充检查都通过。五个定向错误补丁均被补充检查拒绝，结果见 negative-controls.json。框架 46 项离线检查通过。

本环境没有 Docker daemon，也没有执行新的模型调用；Docker 镜像构建、自检与真实模型实验仍在 Mac 进行。不能将本地可信参考补丁验证描述为 Docker 或 Agent 的成功。

筛选时另一个候选参考补丁未通过新增边界检查，因此未纳入本目录。记录在 SOURCE_VALIDATION.json。所有实际入选任务都有明确基线、参考提交与回归来源。

上游发布记录：https://sqlparse.readthedocs.io/en/stable/changes.html 。每个任务的 issue 和完整提交链接可由 task.json 的 URL / SHA 查看。上游 BSD 许可及源码头部保留在各 fixture 内。
