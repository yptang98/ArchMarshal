# CLI Contract

ArchMarshal exposes a versioned JSON contract for automation and agent hosts.
Normal command results are written to stdout. Expected operational and usage
errors are written to stderr. A process must read the stream selected by the
exit code rather than merging both streams.

Every JSON object starts with:

```json
{
  "api_version": "archmarshal-cli-v1"
}
```

When a command payload has its own schema version, ArchMarshal returns it as
`payload_schema_version`; `api_version` always describes the outer CLI
envelope. Help and `--version` deliberately remain plain text.

Parser construction, help, and version use only the lightweight CLI, version,
and error layer. Built-in domains are imported after a command is selected.
This is an implementation/performance boundary, not permission to import or
execute project or user Skill code; ArchMarshal treats those packages as data.

## Exit Codes

| Code | Meaning | Output |
|---|---|---|
| `0` | The read completed, a preview was produced, or the requested reviewed mutation completed. | JSON on stdout, except help/version text. |
| `1` | Lint found policy violations under the selected strictness. | JSON diagnostics on stdout. |
| `2` | Review is still required, a safe precondition blocked the operation, input usage is invalid, or an expected operational error occurred. | JSON on stdout for reviewed workflow states; JSON on stderr for errors. |

`doctor` writes its report to stdout. `healthy`, `warning`, and `absent` states
return `0`; an `error` state returns `2` while retaining the complete report on
stdout. The command accepts an absent root so automation can diagnose setup
without causing directory creation.

Invalid arguments use `error.code = "cli_usage_error"` and include a compact
`error.details.usage` string. Library callers of `main`, `start_main`, and
`end_main` receive integer `2` for invalid arguments; they are not terminated
by `argparse`. Successful help/version requests retain argparse's normal
zero-status `SystemExit` behavior.

Consumers must branch on `mode`, `error.code`, and documented stage-specific
fields. They must not infer permission to write from exit code `0`; all
mutation-capable workflows still require their explicit reviewed-plan tokens.
`candidate-draft --apply` requires the complete saved preview plus its exact
plan and user-store HEAD tokens; a plan fragment is never sufficient.

## Layout fields for `init`, `adopt`, and `start`

The three lifecycle entrypoints accept repeatable
`--save-path kind=project/relative/path`, `--naming-strategy`, `--timezone`,
`--date-partition`, `--timestamp-format`, and `--user-store`. Supported save
roles are `checkpoints`, `reports`, `plans`, `history`, `knowledge`,
`artifacts`, `skills.project`, and `skills.generated`.

Their preview includes:

```json
{
  "layout": {
    "foundation": "confirmed|detected|none",
    "quality": "reasonable|needs_optimization|unsafe",
    "decision": "preserve|suggest_only|initialize",
    "source": "project_config|cli|confirmed_user_profile|detected|archmarshal_default",
    "requires_confirmation": false,
    "effective_profile": {},
    "field_provenance": {},
    "evidence": [],
    "issues": [],
    "recommendations": []
  },
  "human_review": {
    "entrypoint": ".agent/INDEX.md",
    "mapped_paths": [],
    "skill_packages": []
  }
}
```

The complete layout and its evidence are part of the adoption plan digest.
Changing a path, naming rule, user-store HEAD/profile, or detected directory
invalidates the old token. Unsafe destinations return a blocked plan and do not
create `.agent`, `AGENTS.md`, or any scaffold path.
