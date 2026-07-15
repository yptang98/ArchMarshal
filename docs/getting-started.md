# Getting Started

ArchMarshal is used from inside Codex. Ordinary users should not install a
separate app or run the Python CLI by hand.

## 1. Install from a Codex prompt

Paste the complete prompt from [INSTALL_PROMPT.md](../INSTALL_PROMPT.md) into a
Codex task. The prompt performs a first install or safe update, pins a verified
full Git commit SHA, protects any existing installation, and validates the
plugin without touching the current project.

The prompt resolves the immutable ref and then uses Codex's official plugin
marketplace/add commands. It also handles existing marketplaces, backup,
rollback, identity checks, and an isolated runtime when necessary. The guide
does not publish a fake SHA placeholder that looks copyable but cannot run.

Start a new Codex task after installation so the plugin Skill is loaded.

## 2. Start with natural language

For a new project:

```text
用 ArchMarshal 初始化并管理这个新项目，标签是 research、python。先预览。
```

For an existing project and existing Skills:

```text
用 ArchMarshal 安全接管这个已有项目和它的 Skills。先只诊断，确认完整备份范围和冲突，再给我精确计划。
```

For an already managed project:

```text
用 ArchMarshal 开始本次工作，任务是准备发布。先检查健康状态和 Skill 漂移。
```

Codex invokes the plugin wrapper internally. New-project initialization creates
only missing control-plane/scaffold paths. Existing-project adoption leaves all
source files in place and puts routing metadata under `.agent/skill-overlays/`.
Nonstandard project-relative Skill roots can be included explicitly; they add
to rather than replace normal roots.

Imported Skills begin quarantined. Approval is bound to the exact package,
routing metadata, and immutable Skill-index `HEAD`. Global/highest policy needs
a separate explicit confirmation.

## 3. Work normally

After the start check, continue giving ordinary project instructions. The
project remains a normal, human-readable repository. ArchMarshal's summaries
are indexes into preserved source material, not replacements for reports,
plans, notes, checkpoints, or history.

Project files should remain grouped by purpose and lifecycle:

```text
.agent/
├─ INDEX.md          # human map
├─ registry.yaml     # machine ledger
├─ inbox/            # new non-source artifacts awaiting classification
├─ reports/          # explicit-read reports
├─ history/YYYY/MM/DD/
├─ archive/
└─ cache/
```

## 4. Close out at the right depth

Quick closeout:

```text
用 ArchMarshal 粗略整理本次项目：记录结果和必要的轻量证据。
```

Standard closeout:

```text
用 ArchMarshal 认真整理本次项目：记录有序步骤、关键脚本和哈希。
```

Reproducible closeout:

```text
用 ArchMarshal 做可复现级整理：保存环境与依赖指纹、精确命令、关键脚本快照和参考运行脚本，并把未实际验证的部分明确标出来。
```

All modes preview first. Applied sessions use a new date-organized directory
and write `COMMITTED.json` last. Reproducible evidence does not imply that
ArchMarshal executed or validated the commands; `execution_validated` remains
a separate fact.

## 5. Learn without bloating global Skills

After repeated committed sessions:

```text
用 ArchMarshal 从这些项目中提炼重复出现的 Skill 和我的项目偏好，只生成候选，不要自动启用或修改全局 Skills。
```

Learning, candidate decision, draft creation, and promotion are separate review
boundaries. Common-Skill drafts contain `SKILL.md.draft` until a human completes
the package and explicitly activates it. Promoted copies live in an isolated
user store with immutable generations and forward rollback.

## Maintainer notes

The Python CLI exists for CI, automation, and reproducible diagnosis. Its JSON
envelope, streams, exit codes, and exact-plan requirements are defined in the
[CLI Contract](cli-contract.md). The plugin normally handles these details.

When an apply flow needs a complete preview JSON, save it in a system temporary
directory or a user-approved path outside the project. Do not redirect
`skill-review`, `learn`, store-init, draft, or promotion plans into the current
project merely to drive apply.

Read these before maintenance or release work:

- [Filesystem Safety Contract](filesystem-safety.md)
- [Product Readiness](product-readiness.md)
- [Release Process](release-process.md)
