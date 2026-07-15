#!/usr/bin/env python3
"""Reproducible, read-only ArchMarshal scale benchmarks on temporary fixtures."""

# ruff: noqa: E402, I001 -- source checkout path must be inserted before imports.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from archmarshal import __version__
from archmarshal.adoption import plan_adoption
from archmarshal.catalog import catalog_projects
from archmarshal.inventory import collect_inventory

BENCHMARK_FORMAT = "archmarshal-scale-benchmark-v1"
MAX_FILES = 50_000
MAX_SKILLS = 1_000
MAX_PROJECTS = 500
MAX_ITERATIONS = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure 10k-file inventory, many-Skill adoption preview, and multi-project "
            "catalog reads without mutating any real project."
        )
    )
    parser.add_argument("--files", type=int, default=10_000)
    parser.add_argument("--skills", type=int, default=100)
    parser.add_argument("--projects", type=int, default=50)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    _bounded("files", args.files, minimum=1, maximum=MAX_FILES)
    _bounded("skills", args.skills, minimum=1, maximum=MAX_SKILLS)
    _bounded("projects", args.projects, minimum=2, maximum=MAX_PROJECTS)
    _bounded("iterations", args.iterations, minimum=1, maximum=MAX_ITERATIONS)
    _bounded("warmups", args.warmups, minimum=0, maximum=MAX_ITERATIONS)

    payload = run_benchmarks(
        file_count=args.files,
        skill_count=args.skills,
        project_count=args.projects,
        iterations=args.iterations,
        warmups=args.warmups,
    )
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0


def run_benchmarks(
    *,
    file_count: int,
    skill_count: int,
    project_count: int,
    iterations: int,
    warmups: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="archmarshal-scale-") as temporary:
        fixture_root = Path(temporary)
        files_root = fixture_root / "files"
        skills_root = fixture_root / "skills"
        catalog_root = fixture_root / "catalog"
        _create_file_fixture(files_root, file_count)
        _create_skill_fixture(skills_root, skill_count)
        catalog_projects_roots = _create_catalog_fixture(catalog_root, project_count)
        fixture_seconds = time.perf_counter() - started
        before = _tree_digest(fixture_root)

        def inventory_action() -> dict[str, Any]:
            inventory = collect_inventory(files_root)
            observed = inventory.directories["code_roots"][0]["file_count"]
            if observed != file_count:
                raise RuntimeError(f"inventory observed {observed} files, expected {file_count}")
            return {"observed_files": observed}

        def skill_action() -> dict[str, Any]:
            preview = plan_adoption(skills_root)
            observed = len(preview["discovered_skills"])
            if observed != skill_count:
                raise RuntimeError(f"adoption observed {observed} Skills, expected {skill_count}")
            return {
                "observed_skills": observed,
                "operation_count": len(preview["operations"]),
            }

        def catalog_action() -> dict[str, Any]:
            result = catalog_projects(catalog_projects_roots)
            observed = result["project_count"]
            if observed != project_count:
                raise RuntimeError(f"catalog observed {observed} projects, expected {project_count}")
            return {"observed_projects": observed}

        scenarios = {
            "inventory_10k_files": _measure(
                inventory_action,
                iterations=iterations,
                warmups=warmups,
            ),
            "adoption_preview_100_skills": _measure(
                skill_action,
                iterations=iterations,
                warmups=warmups,
            ),
            "catalog_multi_project": _measure(
                catalog_action,
                iterations=iterations,
                warmups=warmups,
            ),
        }
        after = _tree_digest(fixture_root)
        unchanged = before == after
        if not unchanged:
            raise RuntimeError("A benchmarked read path modified its temporary fixture")

    return {
        "format": BENCHMARK_FORMAT,
        "archmarshal_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "parameters": {
            "files": file_count,
            "skills": skill_count,
            "projects": project_count,
            "iterations": iterations,
            "warmups": warmups,
        },
        "fixture_build_seconds": round(fixture_seconds, 6),
        "scenarios": scenarios,
        "mutation_check": {
            "unchanged": unchanged,
            "before_sha256": before,
            "after_sha256": after,
        },
        "notes": [
            "Fixture creation and tree-integrity hashing are outside scenario timings.",
            "All fixtures live under a new system temporary directory.",
            "The benchmark calls public read/preview APIs and never applies a plan.",
            "Compare results only on otherwise similar machines and filesystems.",
        ],
    }


def _measure(
    action: Callable[[], dict[str, Any]],
    *,
    iterations: int,
    warmups: int,
) -> dict[str, Any]:
    for _ in range(warmups):
        action()
    durations: list[float] = []
    observation: dict[str, Any] = {}
    for _ in range(iterations):
        tick = time.perf_counter()
        observation = action()
        durations.append(time.perf_counter() - tick)
    ordered = sorted(durations)
    return {
        "iterations": iterations,
        "seconds": [round(value, 6) for value in durations],
        "min_seconds": round(ordered[0], 6),
        "median_seconds": round(_percentile(ordered, 0.5), 6),
        "p95_seconds": round(_percentile(ordered, 0.95), 6),
        "max_seconds": round(ordered[-1], 6),
        "observation": observation,
    }


def _percentile(ordered: list[float], fraction: float) -> float:
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _create_file_fixture(root: Path, count: int) -> None:
    source = root / "src"
    source.mkdir(parents=True)
    for index in range(count):
        shard = source / f"{index // 1000:03d}"
        shard.mkdir(exist_ok=True)
        (shard / f"file-{index:05d}.txt").write_text("x\n", encoding="utf-8")


def _create_skill_fixture(root: Path, count: int) -> None:
    skill_root = root / "skills"
    skill_root.mkdir(parents=True)
    for index in range(count):
        name = f"skill-{index:04d}"
        package = skill_root / name
        package.mkdir()
        (package / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: Use when benchmark task {index:04d} needs a reviewed helper.\n"
            "---\n\n"
            f"# {name}\n",
            encoding="utf-8",
        )


def _create_catalog_fixture(root: Path, count: int) -> list[Path]:
    roots: list[Path] = []
    for index in range(count):
        project = root / f"project-{index:04d}"
        agent = project / ".agent"
        agent.mkdir(parents=True)
        (agent / "workspace.yaml").write_text(
            "workspace:\n"
            f"  name: project-{index:04d}\n"
            f"  created_on: 2026-01-{(index % 28) + 1:02d}\n"
            "  adopted_on: 2026-07-15\n"
            "  tags: [benchmark]\n"
            "  management_mode: native\n"
            "paths:\n"
            "  project_root: .\n"
            "  code_roots: []\n"
            "  agent_root: .agent\n"
            "  global_skills: []\n"
            "  functional_skills: []\n"
            "  common_project_skills: []\n"
            "  project_skills: []\n"
            "  generated_skills: []\n"
            "  knowledge: []\n"
            "  context_modules: []\n"
            "  reports: []\n"
            "  plans: []\n"
            "  history: []\n"
            "  archive: []\n"
            "  cache: []\n"
            "  inbox: []\n",
            encoding="utf-8",
        )
        roots.append(project)
    return roots


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        filenames.sort()
        for name in directories:
            path = current_path / name
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            digest.update(
                f"d\0{relative}\0{stat.S_IMODE(metadata.st_mode)}\0{metadata.st_mtime_ns}\n".encode()
            )
        for name in filenames:
            path = current_path / name
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            digest.update(
                f"f\0{relative}\0{stat.S_IMODE(metadata.st_mode)}\0{metadata.st_mtime_ns}\0".encode()
            )
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            digest.update(b"\n")
    return digest.hexdigest()


def _bounded(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise SystemExit(f"--{name} must be between {minimum} and {maximum}")


if __name__ == "__main__":
    raise SystemExit(main())
