#!/usr/bin/env python3
import argparse
import os
import re
import subprocess

version_file = "firestarter/__init__.py"
BETA_VERSION_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+(b|rc)[0-9]+$')


def get_version():

    rxs = r'^__version__ =(.\")(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)(?P<pre>(b|rc)[0-9]+)?'

    txt = [line for line in open(version_file)]

    for line in txt:
        m = re.match(rxs, line)
        if m:
            return (m.group("major"), m.group("minor"), m.group("patch"), m.group("pre"))


def update_version(major, minor, patch, *, version_string=None):
    """Update the version number in the file.
    If version_string is provided (beta path), writes it as a single token;
    otherwise (stable path) composes major.minor.patch as before."""

    rxs = "^(__version__ = )"

    txt = [line for line in open(version_file)]

    fout = open(version_file, "w")

    for line in txt:
        m = re.match(rxs, line)
        if m:
            written = version_string if version_string else f"{major}.{minor}.{patch}"
            line = m.groups(0)[0] + f"\"{written}\"\n"
            fout.write(line)
        else:
            fout.write(line)

    fout.close()
    if version_string:
        print(f"Version file updated: {version_string}")
    else:
        print(f"Version file updated: {major}.{minor}.{patch}")


def is_beta_mode(args) -> bool:
    """Return True when script is invoked in a beta-branch context."""
    if args.beta:
        return True
    if os.environ.get("GITHUB_REF") == "refs/heads/beta":
        return True
    if os.environ.get("BETA_VERSION"):
        return True
    return False


def _git_tag_scan_fallback(base: str) -> str:
    """Scan git tags for highest bN on this base; emit b(N+1) or b1."""
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{base}b*"],
            capture_output=True, text=True, check=True
        )
        tags = result.stdout.strip().splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tags = []
    n_re = re.compile(rf'^{re.escape(base)}b([0-9]+)$')
    nums = []
    for t in tags:
        m = n_re.match(t)
        if m:
            nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"{base}b{n}"


def compute_beta_version(major, minor, patch):
    """Compute beta version string. Raises ValueError on invalid input."""
    explicit = os.environ.get("BETA_VERSION", "").strip()
    if explicit:
        if not BETA_VERSION_RE.match(explicit):
            raise ValueError(
                f"BETA_VERSION '{explicit}' does not match {BETA_VERSION_RE.pattern}"
            )
        return explicit
    # D-08 fallback: git-tag scan
    base = f"{major}.{minor}.{patch}"
    return _git_tag_scan_fallback(base)


def parse_args(argv=None):
    """Parse command-line arguments.

    argv: list of argument strings. When None (default), parses an empty list
    so that parse_args() called without arguments always returns the default
    Namespace (dry_run=False, beta=False, set_version=None). This makes the
    function safe to call from tests and from calculate_version() without
    sys.argv interference.

    The __main__ block passes sys.argv[1:] explicitly so the CLI still
    respects command-line flags when invoked as a script.
    """
    if argv is None:
        argv = []
    p = argparse.ArgumentParser(
        description="Bump the firestarter version (stable patch increment or beta pre-release)."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute version without modifying files or $GITHUB_OUTPUT.",
    )
    p.add_argument(
        "--beta",
        action="store_true",
        help="Force beta mode (overrides GITHUB_REF detection).",
    )
    p.add_argument(
        "--set-version",
        default=None,
        metavar="X.Y.ZbN",
        help="Alias for BETA_VERSION env var; takes precedence if both set.",
    )
    return p.parse_args(argv)


def calculate_version(args=None):
    """Calculate and write the next version.

    args: argparse.Namespace (optional). When None, parse_args([]) is called
    so the function is safe to call from tests without sys.argv interference.
    Pass an explicit Namespace for full control.
    """
    if args is None:
        args = parse_args([])

    if is_beta_mode(args):
        major, minor, patch, _pre = get_version()

        # D-29: --set-version overrides BETA_VERSION env if both are set.
        if args.set_version:
            if not BETA_VERSION_RE.match(args.set_version):
                raise ValueError(
                    f"--set-version '{args.set_version}' does not match {BETA_VERSION_RE.pattern}"
                )
            version_string = args.set_version
        else:
            version_string = compute_beta_version(major, minor, patch)

        if args.dry_run:
            print(f"DRY_RUN: {version_string}")
        else:
            # Parse the version_string to extract major/minor/patch/pre for GITHUB_OUTPUT.
            parts = re.match(r'^(\d+)\.(\d+)\.(\d+)(b\d+|rc\d+)$', version_string)
            b_major = parts.group(1)
            b_minor = parts.group(2)
            b_patch = parts.group(3)
            b_pre = parts.group(4)
            update_version(b_major, b_minor, b_patch, version_string=version_string)
            print(f"New versin created: {version_string}")
            github_output = os.environ.get("GITHUB_OUTPUT")
            if github_output:
                with open(github_output, "a") as fh:
                    print(f"version={version_string}", file=fh)
                    print(f"major={b_major}", file=fh)
                    print(f"minor={b_minor}", file=fh)
                    print(f"patch={b_patch}", file=fh)
                    print(f"pre={b_pre}", file=fh)
    else:
        # STABLE PATH — byte-identical to pre-v1.4 with dry-run gating + GITHUB_OUTPUT guard.
        major, minor, patch, _pre = get_version()

        pattern = re.compile("[0-9]+")
        if pattern.match(patch):
            patch = int(patch) + 1
        else:
            patch = 0

        if args.dry_run:
            print(f"DRY_RUN: {major}.{minor}.{patch}")
        else:
            update_version(major, minor, patch)
            print(f"New versin created: {major}.{minor}.{patch}")
            github_output = os.environ.get("GITHUB_OUTPUT")
            if github_output:
                with open(github_output, "a") as fh:
                    print(f"version={major}.{minor}.{patch}", file=fh)
                    print(f"major={major}", file=fh)
                    print(f"minor={minor}", file=fh)
                    print(f"patch={patch}", file=fh)


if __name__ == "__main__":
    import sys
    args = parse_args(sys.argv[1:])
    calculate_version(args)
