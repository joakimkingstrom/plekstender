#!/usr/bin/env bash
# Prints the version of the commit checked out: major.minor from backend/pyproject.toml, then
# the number of commits up to it. Every commit on main gets the next number (0.1.212,
# 0.1.213, ...); change the version in pyproject.toml to move to 0.2 or 1.0.
# Needs the whole history (actions/checkout with fetch-depth: 0).
set -euo pipefail
base=$(sed -nE 's/^version = "([0-9]+)\.([0-9]+)\..*"/\1.\2/p' backend/pyproject.toml | head -1)
[ -n "$base" ] || { echo "no version in backend/pyproject.toml" >&2; exit 1; }
echo "$base.$(git rev-list --count HEAD)"
