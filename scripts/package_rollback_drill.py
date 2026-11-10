"""Install current -> previous -> current wheels and verify every installed package byte."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path


def drill(current: Path, previous: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="am-rollback-") as temporary:
        base = Path(temporary)
        environment = base / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        observations = []
        for index, wheel in enumerate((current, previous, current)):
            args = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                    "--force-reinstall", str(wheel.resolve())]
            if index:
                args.append("--no-deps")
            subprocess.run(args, cwd=base, check=True, capture_output=True, text=True, timeout=180)
            with zipfile.ZipFile(wheel) as archive:
                expected = {name: hashlib.sha256(archive.read(name)).hexdigest()
                            for name in archive.namelist() if name.startswith("archmarshal/") and not name.endswith("/")}
            probe = """import hashlib, importlib.metadata, json, pathlib
distribution = importlib.metadata.distribution('archmarshal')
expected = json.loads(pathlib.Path('expected.json').read_text())
package = pathlib.Path(distribution.locate_file('archmarshal'))
actual = {'archmarshal/' + path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in package.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
assert actual == expected, 'Installed package bytes differ from the reviewed wheel'
print(json.dumps({'version': distribution.version, 'verified_files': len(actual)}))
"""
            (base / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
            result = subprocess.run([str(python), "-c", probe], cwd=base, check=True,
                                    capture_output=True, text=True, timeout=30)
            observation = json.loads(result.stdout)
            for name in ("archmarshal", "archmarshal-start", "archmarshal-end"):
                executable = binary / (name + (".exe" if os.name == "nt" else ""))
                output = subprocess.run([str(executable), "--version"], cwd=base, check=True,
                                        capture_output=True, text=True, timeout=30).stdout
                assert observation["version"] in output
            observation["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
            observations.append(observation)
    if observations[0]["version"] == observations[1]["version"]:
        raise ValueError("Rollback drill requires two different package versions")
    assert observations[0] == observations[2]
    return {"mode": "isolated_venv", "phases": observations, "rollback_verified": True,
            "return_to_current_verified": True, "cli_entrypoints_verified_per_phase": 3}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(drill(args.current, args.previous), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
