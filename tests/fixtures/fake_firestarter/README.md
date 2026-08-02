# `fake_firestarter/` — a deliberately incomplete fake firmware sibling

This directory is a committed stand-in for the firmware sibling repo
(`../firestarter/` relative to `firestarter_app/`), used by
`tests/fw_presence.py` and `tests/test_fw_presence.py` (Phase 123 Plan 07,
BASE-02/BASE-08/D-12).

## What is committed (the violation itself)

This tree is planted with a **deliberate omission**, and the omission is
what is under test — not an implementation detail to work around:

- **Present** (tiny stub content, never real firmware source):
  - `include/firestarter.h`
  - `doc/PROTOCOLS.md`
- **Deliberately ABSENT**: `src/proms/eeprom_28c.cpp` — a path that
  `tests/test_check_no_log_in_sdp_window.py` and `tests/test_sdp_table_parity.py`
  both resolve against the real sibling repo. Its absence here, under a
  repo that otherwise reports "present", is exactly the condition BASE-02
  turns into a hard failure (`MissingScanTargetError`) rather than a skip.

Both present files carry a header comment marking them as fixture input so
nobody mistakes them for real firmware source. Neither is byte-identical to
any file in `/workspaces/firestarter`.

This incompleteness is committed and reviewable in the diff like any other
test fixture. **What is NOT and CANNOT be committed is the `.git` marker.**

## What is synthesised at runtime, and why

Git refuses to store any path component named `.git` — file or directory —
and, measured this session: `git add fake_firestarter/.git`, and even
`git add -f fake_firestarter/.git`, both **report exit 0 while staging
nothing**. `git update-index --add` prints "Ignoring path ... " and also
stages nothing. There is no git-index-based way to commit a `.git` path
component at all, so any acceptance criterion for this fixture must be
verified with `git ls-files`, **never** with `git add`'s exit code — an
`add` that silently no-ops on this exact file is indistinguishable, from
the exit code alone, from an `add` that worked.

The CONTEXT-suggested workaround (committing a plain file *named* `.git`
containing a `gitdir:` pointer) was tried and **measured not to work** —
the write itself is refused the same way. So the marker is not committed
here at all. Instead, `tests/test_fw_presence.py`'s
`_materialise_fake_sibling(tmp_path)` helper:

1. Copies this directory (`tests/fixtures/fake_firestarter/`) into a pytest
   `tmp_path`.
2. Writes a one-line `.git` gitfile (`gitdir: /nonexistent\n`) into the
   *copy*, at test time, only in `tmp_path` — never here, never committed.

The production code (`tests/fw_presence.py`) only ever calls `.exists()` on
the marker — it never shells out to `git` — so a one-line file pointing at
a nonexistent gitdir is entirely sufficient to make the copy read as
"repo present"; no real git repository is needed anywhere in this fixture.

## What this is NOT

This is **not** a `tmp_path`-only fixture of the kind D-12 rejects. The
violation — the incomplete file set, the deliberately-missing scan target —
is committed, reviewable in a diff, and identical on every clone. Only the
single byte-level `.git` marker, which carries no test semantics beyond "a
repo is here", is synthesised, and only because git itself refuses to let
it be committed.

## Verification rule

Fixture presence is confirmed with:

```
git ls-files tests/fixtures/fake_firestarter/
```

which must list `README.md`, `include/firestarter.h` and
`doc/PROTOCOLS.md`, and must **not** list `src/proms/eeprom_28c.cpp` (it
does not exist in the working tree either). Never trust `git add`'s exit
code for anything under this directory — see the measured git behaviour
above.
