# Install or update ArchMarshal in Codex

Copy the complete prompt below into Codex. Codex resolves and verifies the
immutable commit itself; the prompt contains no commit value for you to fill in.

<!-- BEGIN INSTALL PROMPT -->
```text
请在当前 Codex 环境中安装或安全更新 ArchMarshal 管理插件，来源只能是 https://github.com/yptang98/ArchMarshal 。

这是一次 Codex 插件安装任务，不是项目整理任务。安装期间不要运行 ArchMarshal 管理当前项目，不要在当前项目中 clone、创建虚拟环境、写计划文件或修改任何项目/Skill 文件。只允许修改 Codex 自己管理的 marketplace、plugin cache，以及 CODEX_HOME 下专用于 ArchMarshal 的备份或隔离运行时目录。

请按以下安全约束执行，不要把步骤转交给我手工完成：

1. 先确认本机存在可用的 `codex plugin`、Git，以及 Python 3.10–3.13。复用本机已有 Git/GitHub 认证；绝不索取、打印、复制或写入 token、密码、cookie、SSH 私钥和完整 Codex 配置。
2. 从远端默认分支解析当前提交为完整 40 位 SHA（例如使用 `git ls-remote`），并确认该 SHA 的 GitHub Actions CI 已成功。只安装这个完整 SHA，不安装未固定的 `main`，也不要使用文档中的占位符。
3. 在安装前读取 `codex plugin marketplace list --json` 和 `codex plugin list --available --json`。目标 marketplace 必须唯一命名为 `archmarshal`，目标插件必须是 `archmarshal@archmarshal`。如果同名项指向其他仓库、来源不明或存在多个候选，立即停止且不要改动。
4. 如果这是首次安装，执行 `codex plugin marketplace add yptang98/ArchMarshal --ref`，并把第 2 步实际解析、核验过的完整 SHA 作为紧随 `--ref` 的参数；然后执行：
   `codex plugin add archmarshal@archmarshal`
5. 如果已安装且就是同一 SHA/版本，不做重复变更。如果需要更新：先识别它是用户本地 checkout 还是 Codex 管理的 Git 快照。不要删除、移动或改写用户本地 checkout；这种情况只验证并报告。对于 Codex 管理的快照，必须先把旧的仓库身份、完整 ref、版本和相关路径记录到 CODEX_HOME 下带 UTC 时间戳的 `backups/archmarshal/` 目录，并备份现有 ArchMarshal marketplace 快照与插件缓存。不要备份整个 Codex 配置或任何凭据。只有在旧版本可恢复、备份校验通过时，才通过 `codex plugin` 命令移除旧插件/marketplace，再用新的完整 SHA 重新添加。任一步失败都停止并恢复旧的已知良好版本，不留下“半安装”状态。
6. 安装后再次查询插件列表，要求 `archmarshal@archmarshal` 同时为 installed 和 enabled。找到已安装插件内的 `scripts/run_archmarshal.py`，用合适的 Python 运行 `--bootstrap-status`；只有输出同时满足 `mode=ready`、`verified=true`、`marketplace=archmarshal`、`dependency_imported=false`、插件与 engine 版本一致，才算身份校验成功。
7. 再使用同一个 launcher 对一个位于系统临时目录、尚不存在的探测路径运行只读 `doctor`。不得指向当前项目。若缺少 Python 依赖，不要污染系统 Python；在 CODEX_HOME 的 `runtimes/archmarshal/` 下以实际完整 commit SHA 为目录名创建隔离虚拟环境（要求复制解释器而不是创建解释器符号链接）。只读取已固定 SHA 中 `pyproject.toml` 声明的依赖，只安装它们的 wheel 依赖闭包，不把 ArchMarshal engine 另装成环境包，不接受额外包、任意 URL 或源码构建；保留 pip 安装报告并运行 `pip check`。然后用该解释器重新验证。验证成功后，以原子替换方式写入 `CODEX_HOME/runtimes/archmarshal/current.json`。该 JSON 只能包含 `format`、`commit`、`engine_version`、`python` 四个字段：`format` 固定为 `archmarshal-runtime-v1`，其余字段分别使用本次实际完整 SHA、实际 engine 版本和隔离环境解释器的绝对路径。launcher 会校验版本、SHA 和解释器边界后供后续调用。若无法安全建立隔离运行时，就明确报告未完成，不要声称安装成功。
8. 最后报告：安装或更新结果、完整 commit SHA、插件/engine 版本、是否创建备份及其路径、bootstrap 与只读 doctor 的验证结果。不要在安装任务中接管或整理当前项目。提醒我新建一个 Codex 任务后直接用自然语言调用 ArchMarshal。
```
<!-- END INSTALL PROMPT -->
