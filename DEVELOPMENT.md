<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter/main/images/branding/firestarter_logo_horizontal.png" alt="Firestarter EPROM Programmer" width="400"></p>

# Development

This document tells you how to work on the Firestarter CLI. To use the CLI, refer to the
[Firestarter wiki](https://github.com/henols/firestarter/wiki).

## Set up

The CLI needs Python 3.11 or later. CI uses Python 3.11.

```bash
git clone https://github.com/henols/firestarter_app.git
cd firestarter_app
pip install -e '.[test]'
```

## Run the checks

CI runs these commands:

```bash
ruff check firestarter/ tests/
ruff format --check firestarter/ tests/
FIRESTARTER_DEV_TOOLS=1 pytest tests/
```

Set `FIRESTARTER_DEV_TOOLS=1` for the tests. Many tests use the `dev` commands, and a stable
version shows only `dev read` and `dev test` without it.

A later Python can give results that are different from CI. Run the tests on Python 3.11 before
you trust them.

## The chip database

`firestarter/data/chip_database.json` is generated. Do not edit it.

```bash
python tools/build_db.py
```

`tools/build_db.py` downloads a pinned version of the minipro `infoic.xml` and writes the
database. To add a chip that `infoic.xml` does not have, edit `tools/extra_chips.json`. To correct
a generated value, add an entry with a datasheet citation to `tools/datasheet_overrides.json`.
Commit the regenerated database together with the change.

[tools/DECODE-NOTES.md](tools/DECODE-NOTES.md) tells you how the generator decodes each field.

## Releases

A push to `beta` publishes a pre-release to GitHub and PyPI. A push to `main` publishes a stable
release when the version in `firestarter/__init__.py` has no tag yet. The
[release runbook](https://github.com/henols/firestarter/blob/main/RELEASING.md) has the details.

## Pull requests

Send pull requests to this repository. Report problems in the
[project issue tracker](https://github.com/henols/firestarter/issues). The wiki
[Contributing](https://github.com/henols/firestarter/wiki/Contributing) page has the rules.
