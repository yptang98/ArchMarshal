# Release Process

ArchMarshal distinguishes a development commit from a published release. The
version string alone is not a release identity; a release is the immutable
combination of a green commit, tag, changelog entry, and built artifacts.

1. Set the same `X.Y.Z` in `pyproject.toml` and
   `src/archmarshal/__init__.py`, and add a dated `CHANGELOG.md` entry.
2. Run `python scripts/check_version_contract.py`, the full branch-coverage
   suite, static checks, wheel/sdist builds, metadata checks, and clean-install
   smoke tests.
3. Push the commit and wait for every Linux/Windows CI job to pass.
4. Create `vX.Y.Z` only from that exact green commit. Never move or reuse a
   release tag.
5. Build release artifacts from the tagged commit and verify all three CLI
   entrypoints report `X.Y.Z` after clean installation.
6. Install by immutable tag or full commit SHA. Record that identity alongside
   any managed-project backup or operational rollout.

If a release is faulty, restore project data only into a new directory using a
reviewed backup-restore plan. Fix the package in a new patch release; do not
rewrite the old tag or artifacts. User-store rollback is forward-only and keeps
the old immutable generations available for audit.

This repository remains alpha until the signing/provenance gates in
`product-readiness.md` are complete.
