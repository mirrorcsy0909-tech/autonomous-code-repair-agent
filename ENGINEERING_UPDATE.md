# 安装工程收尾更新

前提：已安装上一版可靠性更新，67 项检查与 reliability smoke 已通过。

## 文件放置

将本包内容合并到现有主项目根目录：

- 新增 project.py、test_project.py、docs/、examples/、THIRD_PARTY_NOTICES.md。
- 用新版 README.md 替换旧说明，可先留一份旧说明备份。
- 合并 .github/workflows/ci.yml；其他已有 workflow 不用删除。
- 更新 .gitignore 和 .env.example。保留你实际使用的 .env，不用重新登录或修改模型。
- 本包不含替换版 pyproject.toml/uv.lock；继续保留主项目已有的这两个文件。

Finder 中 .github、.gitignore、.env.example 默认隐藏，可按 Command + Shift + . 显示隐藏文件，再一起复制。在 VS Code 中也可将这些文件/文件夹合并到主项目。不要把本包的整个外层文件夹当成第二个独立项目运行；project.py 应与 expanded_agent.py 同级。

此更新不移动旧源码、不删除运行记录，不重建或更换 Docker 镜像，不调用模型，也不自动提交到 GitHub。examples/ 是可随项目保存的精选报告；原始 runs/ 继续留在本地。

## 验证命令

在主项目终端执行：

```bash
uv run python project.py doctor --catalog all --offline
uv run python project.py list --catalog all
uv run python project.py check
```

doctor 应全部 OK，并明确跳过 Docker；list 显示三个仓库的十个任务，其中 sqlparse-742 为 feature_compatibility；check 预期 78 项检查通过。doctor 不读取 .env 内容或调用模型。

接着验证统一入口仍能使用真实 Docker：

```bash
uv run python project.py smoke --catalog sqlparse --id project-smoke-001
```

预期退出码 0、SMOKE PASSED、cleanup=confirmed。如已使用过该 ID，请换成 project-smoke-002。此 smoke 仍不调用模型。

查看旧报告可改用：

```bash
uv run python project.py report --catalog sqlparse --prefix expanded-002
```

结果仍应为两种方法各 5/5，701 两份补丁显示 review_required。旧命令 uv run python expanded_agent.py report --prefix expanded-002 也继续有效。

暂时不需要重新运行真实模型实验。GitHub CI 文件已准备好，但只有上传仓库并实际执行 Actions 后才有远端 CI 结果；当前不能声称远端 CI 已通过。
