# Branches, versions and releases

`openqasm` implements several major versions of the OpenQASM language. Each
one has its own long-lived branch, and the package's major version matches
the language version it implements:

| Branch   | Language     | PyPI releases  | Status                          |
|----------|--------------|----------------|---------------------------------|
| `master` | OpenQASM 3.x | `openqasm` 3.x | development of the latest version |
| `v3`     | OpenQASM 3.x | `openqasm` 3.x | mirror of `master`, kept in sync automatically |
| `v2`     | OpenQASM 2.0 | `openqasm` 2.x | maintenance: bug fixes only     |

`master` always holds the newest language version. Users who need OpenQASM 2
pin `openqasm<3`.

## Day-to-day work

- New features and fixes for the latest version: open a pull request against
  `master`. On every push to `master`, the `sync-version-branch` workflow
  fast-forwards the matching `vN` branch (`v3` today), so nobody commits to
  `v3` directly. If someone does, the fast-forward fails and the workflow
  reports it.
- Fixes for an older version: open a pull request against its branch (`v2`).
  The two implementations share no code, so fixes are made on the branch that
  needs them. If a bug affects both, fix it in both pull requests.
- When a change on `master` also applies to an older branch (shared docs or
  CI, say), cherry-pick it: `git cherry-pick -x <sha>` on a branch created
  from `v2`, then open a pull request against `v2`.

## Releasing

Releases are tags on the branch being released: `v2.0.1` on `v2`, `v3.1.0` on
`master`. Pushing the tag runs the `release` workflow, which builds the
package, checks that the tag matches the version in `pyproject.toml`, and
publishes to PyPI through trusted publishing.

PyPI orders releases by version, not by date, so publishing a `2.x` fix after
a `3.x` release leaves `pip install openqasm` on the latest 3.x.

## Starting a new major version

When OpenQASM 4 work starts:

1. Make sure `v3` is up to date with `master` (the sync workflow guarantees this).
2. Bump `master` to `4.0.0.dev0`. The sync workflow then targets `v4`, and `v3`
   becomes a maintenance branch like `v2`.
3. Update the table above and the `branches` filters in the CI workflow.
