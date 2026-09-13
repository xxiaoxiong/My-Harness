# Repository stage workflow

This repository is an educational, progressively built Agent Harness. Follow
`docs/agent_harness_progressive_learning_roadmap.md` and implement only the
requested HARN stage.

## Development copy and snapshots

- The repository root is the current development copy.
- `stages/HARN-XX/` is the immutable, independently runnable snapshot captured
  when that stage was completed.
- Start a new stage from the current root implementation. Do not modify older
  snapshots to make later-stage code work.
- After the new stage passes its tests and Demo, commit the root implementation,
  then run
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/create_stage_snapshot.ps1 HARN-XX`
  to archive that exact commit into a new stage directory.
- Never include `.git/`, `.venv/`, caches, build output, or an existing
  `stages/` directory inside a snapshot.

## Snapshot acceptance checks

Every stage snapshot must contain its own:

- `harness/` source package;
- `examples/` Demo for that stage and earlier retained examples;
- `tests/` for that stage and earlier regression tests;
- `pyproject.toml` with that stage's dependencies;
- `README.md` and relevant `docs/stage_notes/` material.

Run the snapshot's tests and Demo from inside its directory. The snapshot must
not rely on the root `harness/` package to import or run.
