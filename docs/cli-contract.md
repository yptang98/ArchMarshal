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

## Exit Codes

| Code | Meaning | Output |
|---|---|---|
| `0` | The read completed, a preview was produced, or the requested reviewed mutation completed. | JSON on stdout, except help/version text. |
| `1` | Lint found policy violations under the selected strictness. | JSON diagnostics on stdout. |
| `2` | Review is still required, a safe precondition blocked the operation, input usage is invalid, or an expected operational error occurred. | JSON on stdout for reviewed workflow states; JSON on stderr for errors. |

Invalid arguments use `error.code = "cli_usage_error"` and include a compact
`error.details.usage` string. Library callers of `main`, `start_main`, and
`end_main` receive integer `2` for invalid arguments; they are not terminated
by `argparse`. Successful help/version requests retain argparse's normal
zero-status `SystemExit` behavior.

Consumers must branch on `mode`, `error.code`, and documented stage-specific
fields. They must not infer permission to write from exit code `0`; all
mutation-capable workflows still require their explicit reviewed-plan tokens.
