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
5. Use the successful tagged `Build provenance` run's exact wheel, sdist,
   `SHA256SUMS`, and `provenance.sigstore.json` as release inputs. Verify all three
   CLI entrypoints after clean installation; do not rebuild or replace attested bytes.
6. Install by immutable tag or full commit SHA. Record that identity alongside
   any managed-project backup or operational rollout.

If a release is faulty, restore project data only into a new directory using a
reviewed backup-restore plan. Fix the package in a new patch release; do not
rewrite the old tag or artifacts. User-store rollback is forward-only and keeps
the old immutable generations available for audit.

The `Build provenance` workflow uses pinned GitHub actions, GitHub OIDC and
Sigstore to attest wheel/sdist/checksums. It verifies the repository, source ref,
source commit and signer workflow before retaining the artifacts. Development
branch builds have development refs; they are not tagged releases. The workflow
does not publish a release or upload to PyPI. See GitHub's
[artifact attestation guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
and [verification options](https://cli.github.com/manual/gh_attestation_verify).

Consumers should verify each artifact with the expected immutable release ref
and commit, not just a repository name:

```sh
gh attestation verify ARTIFACT --repo yptang98/ArchMarshal \
  --source-ref refs/tags/vX.Y.Z --source-digest FULL_COMMIT_SHA \
  --signer-workflow yptang98/ArchMarshal/.github/workflows/provenance.yml
```

The retained Sigstore bundle supports verification using `--bundle`; the GitHub
API path also checks the repository's current identity. Existing 0.18.0 artifacts
were published before this workflow and are not retroactively attested.

Linux and Windows CI build the current wheel and the 0.18.0 baseline, then run
`scripts/package_rollback_drill.py` in a disposable virtual environment. Each phase
(current, baseline, current) verifies every installed package file against its
wheel hash and all three CLI entrypoint versions. This tests package rollback;
it does not downgrade or rewrite project data formats.

The dedicated filesystem CI job uses an unprivileged process and a 16 MiB tmpfs
for real permission denial and disk exhaustion, both before and after recovery
staging. It checks that all four cases ran and that exact-plan retry/recovery
succeeds once capacity or permissions are restored. It never fills the runner's
main disk. Windows ACL-specific integration remains outside this claim.

This repository remains alpha; consult `product-readiness.md` for remaining
platform, host-integration and durability limits.
