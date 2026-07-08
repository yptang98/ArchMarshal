# ArchMarshal Audit Report Sample

Project: `examples/simple-project`

Date: 2026-07-08

## Summary

This sample report demonstrates the expected shape of future `archmarshal audit` output.

## Findings

### warning: project.context_module_missing_source_files

`context.architecture` should list the source knowledge files or reports it was distilled from.

### info: project.inbox_file_too_old

No old inbox files were detected in this sample.

## Suggested Plan

1. Add `source_files` to `.agent/context-modules/architecture/module.yaml`.
2. Keep historical reports explicit-only.
3. Do not apply changes automatically.
