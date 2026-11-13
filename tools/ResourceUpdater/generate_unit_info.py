"""Verify pinned local inputs and invoke the canonical C++ unit metadata generator."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def generate(updater: Path, input_dir: Path, output: Path, source: Path) -> None:
    manifest = json.loads(source.read_bytes())
    for name, entry in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Source input names must be filenames")
        actual = hashlib.sha256((input_dir / name).read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(f"Source SHA-256 mismatch: {name}")
    subprocess.run(
        [
            str(updater.resolve()),
            "--unit-info",
            str(input_dir.resolve()),
            str(output.resolve()),
            str(source.resolve()),
        ],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("unit_info_source.json"),
    )
    args = parser.parse_args()
    generate(args.updater, args.input, args.output, args.source)
