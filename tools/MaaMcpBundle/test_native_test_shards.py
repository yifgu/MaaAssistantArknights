import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import native_runtime
import native_test_shards as shards

TOOLS = Path(__file__).resolve().parent


@pytest.mark.parametrize("count", [4, 5, 133, 134])
def test_sorted_parametrized_items_are_complete_disjoint_and_balanced(count):
    nodeids = [f"tests/test_native.py::test_case[{index}]" for index in range(count)]
    partitions = [shards.partition(nodeids, index) for index in range(4)]
    assert sorted(nodeid for group in partitions for nodeid in group) == sorted(nodeids)
    assert max(map(len, partitions)) - min(map(len, partitions)) <= 1
    for index, group in enumerate(partitions):
        assert group == shards.partition(list(reversed(nodeids)), index)
        assert all(
            not set(group).intersection(other) for other in partitions[index + 1 :]
        )


def test_duplicate_collection_and_invalid_shard_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        shards.partition(["test_a", "test_a"], 0)
    with pytest.raises(ValueError, match="index"):
        shards.partition(["test_a"], 4)


@pytest.fixture
def shard_environment(tmp_path):
    runtime = tmp_path / "runtime"
    install = runtime / "install"
    (install / "resource").mkdir(parents=True)
    platform = "win-x64" if os.name == "nt" else "linux-x64"
    library = install / ("MaaCore.dll" if os.name == "nt" else "libMaaCore.so")
    library.write_bytes(b"synthetic Core for runner tests, never loaded")
    context = native_runtime.test_result(platform, "RelWithDebInfo", "v-test")
    metadata = {
        **context,
        "configuration": "Debug",
        "core_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }
    shards.write_json(runtime / "native.json", metadata)
    test_file = tmp_path / "test_cases.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('case', range(9))\n"
        "def test_case(case):\n"
        "    assert case >= 0\n",
        encoding="utf-8",
    )
    return {
        "runtime": runtime,
        "install": install,
        "library": library,
        "context": context,
        "metadata": metadata,
        "test_file": test_file,
        "root": tmp_path,
    }


def invoke_shard(environment, index=0, flags=("-I", "-B"), extra_tests=(), tests=None):
    reports = environment["root"] / f"reports-{index}"
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "MAA_MCP_NATIVE_CORE": str(environment["install"]),
        "MAA_MCP_RESOURCE_DIR": str(environment["install"]),
    }
    result = subprocess.run(
        [
            sys.executable,
            *flags,
            str(TOOLS / "native_test_shards.py"),
            "--shard-index",
            str(index),
            "--report-dir",
            str(reports),
            "--runtime",
            str(environment["runtime"]),
            "--platform",
            environment["context"]["platform"],
            "--configuration",
            "RelWithDebInfo",
            "--core-version",
            "v-test",
            *map(str, [environment["test_file"]] if tests is None else tests),
            *map(str, extra_tests),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, reports


def invoke_gate(environment, directory, output):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(TOOLS / "native_runtime.py"),
            "--tested-bundle",
            str(output),
            "--shard-results",
            str(directory),
            "--platform",
            environment["context"]["platform"],
            "--configuration",
            "RelWithDebInfo",
            "--core-version",
            "v-test",
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("selection", ["file", "nodeids"])
def test_four_isolated_pytest_processes_authorize_one_platform_receipt(
    shard_environment, selection
):
    directory = shard_environment["root"] / "receipts"
    directory.mkdir()
    tests = (
        [f"{shard_environment['test_file']}::test_case[{index}]" for index in range(9)]
        if selection == "nodeids"
        else None
    )
    for index in range(4):
        result, reports = invoke_shard(shard_environment, index, tests=tests)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "test_case[" in result.stdout
        assert "receipt accepted" in result.stdout
        assert (reports / "native.xml").is_file()
        shutil.copy2(reports / f"shard-{index}.json", directory)
    output = shard_environment["root"] / "tested.json"
    result = invoke_gate(shard_environment, directory, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "9 tests passed" in result.stdout
    assert json.loads(output.read_text()) == shard_environment["context"]
    native_runtime.verify_test_result(
        output, shard_environment["context"]["platform"], "RelWithDebInfo", "v-test"
    )


def test_missing_explicit_nodeid_cannot_authorize_a_receipt(shard_environment):
    test_file = shard_environment["test_file"]
    result, reports = invoke_shard(
        shard_environment,
        tests=[f"{test_file}::test_case[0]", f"{test_file}::test_missing"],
    )
    assert result.returncode != 0
    assert not (reports / "shard-0.json").exists()


def test_platform_receipt_cannot_be_created_without_shard_results(
    tmp_path, monkeypatch
):
    output = tmp_path / "tested.json"
    output.write_text("stale platform success")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "native_runtime.py",
            "--tested-bundle",
            str(output),
            "--platform",
            "linux-x64",
            "--configuration",
            "RelWithDebInfo",
        ],
    )
    with pytest.raises(SystemExit) as result:
        native_runtime.main()
    assert result.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "body",
    [
        "def test_case():\n    assert False\n",
        "import pytest\n@pytest.mark.skip(reason='no Core')\ndef test_case(): pass\n",
        "import pytest\n@pytest.mark.xfail(reason='not tested')\ndef test_case(): assert False\n",
        "import pytest\n@pytest.mark.xfail(reason='unexpected pass')\ndef test_case(): pass\n",
        "import pytest\n@pytest.fixture\ndef broken():\n    yield\n    assert False\n"
        "def test_case(broken): pass\n",
        "import pytest\n@pytest.fixture\ndef broken(): assert False\n"
        "def test_case(broken): pass\n",
        "import missing_native_shard_test_module\n",
        "import pytest\npytest.skip('no tests', allow_module_level=True)\n",
        "import pytest\ndef test_case(): pytest.exit('cancelled', returncode=0)\n",
        "",
    ],
    ids=[
        "failure",
        "skip",
        "xfail",
        "xpass",
        "teardown",
        "setup",
        "collection",
        "module-skip",
        "early-success-exit",
        "empty",
    ],
)
def test_incomplete_or_unsuccessful_pytest_never_leaves_a_receipt(
    shard_environment, body
):
    shard_environment["test_file"].write_text(body, encoding="utf-8")
    reports = shard_environment["root"] / "reports-0"
    reports.mkdir()
    (reports / "shard-0.json").write_text("stale success")
    result, reports = invoke_shard(shard_environment)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not (reports / "shard-0.json").exists()
    assert json.loads((reports / "diagnostics.json").read_text())["complete"] is False


def test_partially_skipped_collection_is_not_success(shard_environment):
    skipped = shard_environment["root"] / "test_skipped.py"
    skipped.write_text(
        "import pytest\npytest.skip('missing dependency', allow_module_level=True)\n"
    )
    result, reports = invoke_shard(shard_environment, extra_tests=[skipped])
    assert result.returncode != 0, result.stdout + result.stderr
    assert not (reports / "shard-0.json").exists()


def test_failure_is_reported_immediately_without_stopping_remaining_cases(
    shard_environment,
):
    shard_environment["test_file"].write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('case', range(9))\n"
        "def test_case(case):\n"
        "    assert case != 0, 'immediate failure details'\n",
        encoding="utf-8",
    )
    result, reports = invoke_shard(shard_environment)
    assert result.returncode != 0
    assert result.stdout.index("IMMEDIATE FAILURE:") < result.stdout.index(
        "::test_case[4]"
    )
    assert "immediate failure details" in result.stdout
    progress = [
        json.loads(line)
        for line in (reports / "progress.jsonl").read_text().splitlines()
    ]
    calls = [event for event in progress if event["phase"] == "call"]
    assert [event["outcome"] for event in calls] == ["failed", "passed", "passed"]
    assert "immediate failure details" in calls[0]["traceback"]
    assert not (reports / "shard-0.json").exists()


def test_failure_journal_survives_abrupt_interruption(shard_environment):
    shard_environment["test_file"].write_text(
        "import os\nimport pytest\n"
        "@pytest.mark.parametrize('case', range(9))\n"
        "def test_case(case):\n"
        "    if case == 4: os._exit(7)\n"
        "    assert case != 0, 'failure before interruption'\n",
        encoding="utf-8",
    )
    result, reports = invoke_shard(shard_environment)
    assert result.returncode == 7
    assert "failure before interruption" in result.stdout
    progress = [
        json.loads(line)
        for line in (reports / "progress.jsonl").read_text().splitlines()
    ]
    assert any(
        "failure before interruption" in event.get("traceback", "")
        for event in progress
    )
    assert any(
        event["nodeid"].endswith("::test_case[4]") and event["phase"] == "start"
        for event in progress
    )
    assert not (reports / "shard-0.json").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "missing-core",
        "release-core",
        "wrong-core-hash",
        "no-isolation",
        "no-bytecode-flag",
    ],
)
def test_runner_rejects_missing_core_wrong_configuration_and_source_import_mode(
    shard_environment, failure
):
    flags = ("-I", "-B")
    if failure == "missing-core":
        shard_environment["library"].unlink()
    elif failure == "release-core":
        metadata = {**shard_environment["metadata"], "configuration": "RelWithDebInfo"}
        shards.write_json(shard_environment["runtime"] / "native.json", metadata)
    elif failure == "wrong-core-hash":
        shard_environment["library"].write_bytes(b"modified Core")
    elif failure == "no-isolation":
        flags = ("-B",)
    else:
        flags = ("-I",)
    result, reports = invoke_shard(shard_environment, flags=flags)
    assert result.returncode != 0
    assert not (reports / "shard-0.json").exists()


def passing_receipts(environment):
    collected = [f"tests/test_native.py::test_case[{index}]" for index in range(9)]
    return [
        {
            "context": dict(environment["context"]),
            "runtime": dict(environment["metadata"]),
            "shard_index": index,
            "shard_count": 4,
            "exit_code": 0,
            "complete": True,
            "collection_errors": [],
            "collected": list(collected),
            "selected": shards.partition(collected, index),
            "passed": shards.partition(collected, index),
        }
        for index in range(4)
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "extra",
        "failed",
        "incomplete",
        "skipped-collection",
        "duplicate",
        "wrong-partition",
        "unrun-case",
        "different-collection",
        "different-core",
        "release-core",
        "different-source",
        "different-platform",
        "empty",
    ],
)
def test_fan_in_rejects_incomplete_mismatched_and_overlapping_shards(
    shard_environment, failure
):
    receipts = passing_receipts(shard_environment)
    target = receipts[0]
    if failure == "missing":
        receipts.pop()
    elif failure == "extra":
        receipts.append(target)
    elif failure == "failed":
        target["exit_code"] = 1
    elif failure == "incomplete":
        target["complete"] = False
    elif failure == "skipped-collection":
        target["collection_errors"] = ["skipped.py"]
    elif failure == "duplicate":
        target["collected"].append(target["collected"][0])
    elif failure == "wrong-partition":
        target["selected"] = receipts[1]["selected"]
    elif failure == "unrun-case":
        target["passed"].pop()
    elif failure == "different-collection":
        target["collected"].append("tests/test_native.py::test_extra")
        target["selected"] = shards.partition(target["collected"], 0)
        target["passed"] = target["selected"]
    elif failure == "different-core":
        target["runtime"]["core_sha256"] = "0" * 64
    elif failure == "release-core":
        for receipt in receipts:
            receipt["runtime"]["configuration"] = "RelWithDebInfo"
    elif failure == "different-source":
        target["context"]["source_commit"] = "different"
    elif failure == "different-platform":
        target["context"]["platform"] = "different"
    elif failure == "empty":
        for receipt in receipts:
            receipt.update(collected=[], selected=[], passed=[])
    directory = shard_environment["root"] / "receipts"
    directory.mkdir()
    for index, receipt in enumerate(receipts):
        shards.write_json(directory / f"shard-{index}.json", receipt)
    output = shard_environment["root"] / "tested.json"
    output.write_text("stale platform success")
    result = invoke_gate(shard_environment, directory, output)
    assert result.returncode != 0
    assert not output.exists()
