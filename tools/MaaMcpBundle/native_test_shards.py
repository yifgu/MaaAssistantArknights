"""Run four isolated installed-wheel pytest shards and validate their complete fan-in.

Sorted pytest node IDs are assigned round-robin, including parametrized cases.
Only a completed, entirely passing shard writes a receipt. Failures are printed
immediately, and a flushed per-case journal survives interruptions and timeouts.
Selectors can be pytest files or explicit node IDs; CI passes both complete files.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

SHARD_COUNT = 4


def partition(nodeids: list[str], index: int) -> list[str]:
    if not 0 <= index < SHARD_COUNT:
        raise ValueError(f"Shard index must be between 0 and {SHARD_COUNT - 1}")
    if len(set(nodeids)) != len(nodeids):
        raise ValueError("Duplicate pytest node IDs")
    return sorted(nodeids)[index::SHARD_COUNT]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def verify_shard_results(directory: Path, context: dict) -> int:
    expected_names = {f"shard-{index}.json" for index in range(SHARD_COUNT)}
    if {path.name for path in directory.glob("*.json")} != expected_names:
        raise ValueError("Expected exactly four successful native shard receipts")
    inventory = None
    runtime = None
    passed = []
    for index in range(SHARD_COUNT):
        receipt = json.loads(
            (directory / f"shard-{index}.json").read_text(encoding="utf-8")
        )
        collected = receipt["collected"]
        selected = partition(collected, index)
        if (
            receipt["context"] != context
            or receipt["shard_index"] != index
            or receipt["shard_count"] != SHARD_COUNT
            or receipt["exit_code"] != 0
            or receipt["complete"] is not True
            or receipt["collection_errors"]
            or not selected
            or receipt["selected"] != selected
            or receipt["passed"] != selected
        ):
            raise ValueError(f"Unsuccessful or mismatched native shard {index}")
        if inventory is None:
            inventory = collected
            runtime = receipt["runtime"]
            if (
                runtime["configuration"] != "Debug"
                or runtime["platform"] != context["platform"]
                or runtime["source_commit"] != context["source_commit"]
                or runtime["core_version"] != context["core_version"]
                or len(runtime["core_sha256"]) != 64
            ):
                raise ValueError("Native shards did not use the expected Debug Core")
        if collected != inventory or receipt["runtime"] != runtime:
            raise ValueError("Native shards disagree on collection or Debug Core")
        passed.extend(receipt["passed"])
    if sorted(passed) != sorted(inventory):
        raise ValueError("Native shards did not pass the complete test collection")
    return len(passed)


def run_shard(args: argparse.Namespace) -> int:
    receipt = args.report_dir / f"shard-{args.shard_index}.json"
    receipt.unlink(missing_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    progress = args.report_dir / "progress.jsonl"
    progress.unlink(missing_ok=True)
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError("Native shards must run with Python -I -B")
    package = importlib.util.find_spec("maa_battle_mcp")
    if (
        package is None
        or package.origin is None
        or not Path(package.origin)
        .resolve()
        .is_relative_to(Path(sysconfig.get_path("purelib")).resolve())
    ):
        raise ValueError("Native shards require the installed MCP wheel")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    context = {
        "source_commit": source,
        "platform": args.platform,
        "configuration": args.configuration,
        "core_version": args.core_version or source,
    }
    runtime = json.loads((args.runtime / "native.json").read_text(encoding="utf-8"))
    install = (args.runtime / "install").resolve()
    library = install / (
        "MaaCore.dll" if args.platform.startswith("win-") else "libMaaCore.so"
    )
    if (
        runtime["configuration"] != "Debug"
        or runtime["source_commit"] != source
        or runtime["platform"] != args.platform
        or runtime["core_version"] != context["core_version"]
        or runtime["core_sha256"] != hashlib.sha256(library.read_bytes()).hexdigest()
        or Path(os.environ.get("MAA_MCP_NATIVE_CORE", "")).resolve() != install
        or Path(os.environ.get("MAA_MCP_RESOURCE_DIR", "")).resolve() != install
        or not (install / "resource").is_dir()
    ):
        raise ValueError("Native shard requires the verified actual Debug Core")

    import pytest

    class Shard:
        def __init__(self):
            self.collected = []
            self.collected_files = set()
            self.selected = []
            self.reports = {}
            self.collection_errors = []

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, config, items):
            self.collected = sorted(item.nodeid for item in items)
            self.collected_files = {item.path.resolve() for item in items}
            self.selected = partition(self.collected, args.shard_index)
            by_id = {item.nodeid: item for item in items}
            config.hook.pytest_deselected(
                items=[item for item in items if item.nodeid not in self.selected]
            )
            items[:] = [by_id[nodeid] for nodeid in self.selected]
            write_json(
                args.report_dir / "collection.json",
                {"collected": self.collected, "selected": self.selected},
            )
            print(
                f"\nNative shard {args.shard_index + 1}/{SHARD_COUNT}: "
                f"{len(self.selected)}/{len(self.collected)} cases; "
                f"Debug Core {runtime['core_sha256']}; wheel {package.origin}",
                flush=True,
            )

        def pytest_collectreport(self, report):
            if not report.passed:
                self.collection_errors.append(report.nodeid)

        def record(self, event):
            with progress.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")

        def pytest_runtest_logstart(self, nodeid):
            self.record({"nodeid": nodeid, "phase": "start"})

        def pytest_runtest_logreport(self, report):
            self.reports.setdefault(report.nodeid, []).append(
                (report.when, report.outcome, hasattr(report, "wasxfail"))
            )
            event = {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "duration": report.duration,
            }
            if report.failed:
                event["traceback"] = str(report.longrepr)
                event["sections"] = report.sections
                print(
                    f"\nIMMEDIATE FAILURE: {report.nodeid} [{report.when}]\n"
                    f"{report.longrepr}",
                    flush=True,
                )
                for name, content in report.sections:
                    print(f"--- {name} ---\n{content}", flush=True)
            self.record(event)

    plugin = Shard()
    exit_code = int(
        pytest.main(
            [
                *map(str, args.tests),
                "-vv",
                "-ra",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "--durations=25",
                f"--basetemp={args.report_dir.parent / f'pytest-tmp-{args.shard_index}'}",
                "-o",
                "faulthandler_timeout=300",
                f"--junitxml={args.report_dir / 'native.xml'}",
            ],
            plugins=[plugin],
        )
    )
    passed = sorted(
        nodeid
        for nodeid, reports in plugin.reports.items()
        if reports
        == [
            ("setup", "passed", False),
            ("call", "passed", False),
            ("teardown", "passed", False),
        ]
    )
    requested_files = {
        Path(selector.partition("::")[0]).resolve() for selector in args.tests
    }
    checks = {
        "pytest exited unsuccessfully": exit_code == 0,
        "collection failed or skipped": not plugin.collection_errors,
        "shard selected no tests": bool(plugin.selected),
        "not all selected cases fully passed": passed == plugin.selected
        and set(plugin.reports) == set(plugin.selected),
        "requested test files were not completely collected": plugin.collected_files
        == requested_files,
    }
    refusal_reasons = [reason for reason, accepted in checks.items() if not accepted]
    complete = not refusal_reasons
    result = {
        "context": context,
        "shard_index": args.shard_index,
        "shard_count": SHARD_COUNT,
        "runtime": runtime,
        "collected": plugin.collected,
        "selected": plugin.selected,
        "passed": passed,
        "exit_code": exit_code,
        "complete": complete,
        "refusal_reasons": refusal_reasons,
        "collection_errors": plugin.collection_errors,
    }
    write_json(args.report_dir / "diagnostics.json", result)
    print(
        f"Native shard {args.shard_index + 1}/{SHARD_COUNT}: "
        f"{len(passed)}/{len(plugin.selected)} fully passed; "
        f"pytest exit {exit_code}; receipt {'accepted' if complete else 'refused'}",
        flush=True,
    )
    if not complete:
        print("Receipt refused: " + "; ".join(refusal_reasons), flush=True)
        return exit_code or 1
    write_json(receipt, result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard-index", type=int, choices=range(SHARD_COUNT), required=True
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--platform", choices=["win-x64", "linux-x64"], required=True)
    parser.add_argument(
        "--configuration", choices=["Debug", "RelWithDebInfo"], required=True
    )
    parser.add_argument("--core-version", default="")
    parser.add_argument(
        "tests", nargs="+", help="Pytest file paths or explicit node IDs"
    )
    return run_shard(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
