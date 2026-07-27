"""Session-scoped setup for the signal-parity tests: compiles the real
frontend/src/technicals.ts and optionsAnalysis.ts to plain JS once per test
run (via `tsc`, no bundler / no vitest needed) and exposes a `call_ts()`
fixture that invokes a named export of the compiled module from Python via
backend/tests/ts_harness/run.js.

Skips (not fails) the whole parity module if `npx`/`tsc`/`node` aren't on
PATH, so `pytest` still runs the rest of the suite in an environment
without a JS toolchain -- correctness of the ported signals matters, but
it shouldn't be the thing that makes `pytest` unusable for someone without
Node installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = None  # set below


def _find_repo_root() -> "object":
    import pathlib
    p = pathlib.Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "frontend" / "src" / "technicals.ts").exists():
            return parent
    raise RuntimeError("couldn't locate repo root from backend/tests/conftest.py")


@pytest.fixture(scope="session")
def repo_root():
    return _find_repo_root()


@pytest.fixture(scope="session")
def ts_build_dir(tmp_path_factory, repo_root):
    if shutil.which("npx") is None or shutil.which("node") is None:
        pytest.skip("node/npx not on PATH -- skipping TS<->Python signal parity tests")

    build_dir = tmp_path_factory.mktemp("ts_build")
    frontend_src = repo_root / "frontend" / "src"
    result = subprocess.run(
        [
            "npx", "-y", "tsc",
            "--target", "es2020",
            "--module", "commonjs",
            "--moduleResolution", "node",
            "--outDir", str(build_dir),
            "--skipLibCheck",
            "--ignoreConfig",
            str(frontend_src / "technicals.ts"),
            str(frontend_src / "optionsAnalysis.ts"),
        ],
        cwd=str(frontend_src),
        capture_output=True,
        text=True,
        timeout=120,
    )
    # tsc exits 0 even when it only emitted warnings (e.g. the deprecated
    # moduleResolution=node10 notice) -- the real signal is whether the .js
    # files actually landed, not the exit code alone.
    if not (build_dir / "technicals.js").exists() or not (build_dir / "optionsAnalysis.js").exists():
        pytest.fail(f"tsc compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return build_dir


@pytest.fixture(scope="session")
def call_ts(ts_build_dir, repo_root):
    harness = repo_root / "backend" / "tests" / "ts_harness" / "run.js"

    def _call(module: str, fn: str, *args):
        proc = subprocess.run(
            ["node", str(harness)],
            input=json.dumps({"module": module, "fn": fn, "args": list(args)}),
            capture_output=True,
            text=True,
            env={**os.environ, "TS_BUILD_DIR": str(ts_build_dir)},
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"TS harness call {module}.{fn} failed: {proc.stderr}")
        return json.loads(proc.stdout)

    return _call
