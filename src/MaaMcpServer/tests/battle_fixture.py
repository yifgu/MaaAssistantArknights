"""Synthetic screenshots and local-only input transport for actual Debug Core tests."""

import json
import random
import struct
import sys
import time
import zlib
from pathlib import Path


def png(width, height, pixels):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(
        b"\0" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def first_pixel(image):
    """The first RGB pixel has a zero predictor for all five PNG row filters."""
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    assert image[24:26] == b"\x08\x02"
    compressed = bytearray()
    offset = 8
    while offset < len(image):
        length = struct.unpack_from(">I", image, offset)[0]
        if image[offset + 4 : offset + 8] == b"IDAT":
            compressed.extend(image[offset + 8 : offset + 8 + length])
        offset += length + 12
    return tuple(zlib.decompress(compressed)[1:4])


MARKERS = {"paused": (10, 20, 30), "running": (40, 50, 60), "unknown": (70, 80, 90)}


def prepare(settings, known):
    root = settings.user_dir / "fixture"
    templates = root / "resource/template"
    templates.mkdir(parents=True)
    tasks = {}
    patches = []
    for seed, name, x, y in (
        (1, "BattleHpFlag", 500, 20),
        (2, "BattleOfficiallyBegin", 1180, 30),
        (3, "BattleOpersFlag", 100, 590),
        (4, "BattleOperRolePioneer", 60, 597),
    ):
        pixels = random.Random(seed).randbytes(8 * 8 * 3)
        (templates / f"{name}.png").write_bytes(png(8, 8, pixels))
        tasks[name] = {"templThreshold": 0.99, "maskRange": [0, 255]}
        patches.append((name, x, y, 8, 8, pixels))
    avatar = random.Random(5).randbytes(60 * 60 * 3)
    patches.append(("avatar", 62, 628, 60, 60, avatar))
    patches.append(("running-brightness", 1200, 40, 16, 16, b"\xff" * 16 * 16 * 3))
    if known:
        cache = settings.user_dir / "cache/avatars"
        cache.mkdir(parents=True)
        (cache / "char_123_fang.png").write_bytes(png(60, 60, avatar))
    for mode, marker in MARKERS.items():
        image = bytearray(1280 * 720 * 3)
        image[:3] = bytes(marker)
        for name, x, y, width, height, pixels in patches:
            if name == "BattleHpFlag" and mode == "unknown":
                continue
            if (
                name in ("BattleOfficiallyBegin", "running-brightness")
                and mode != "running"
            ):
                continue
            for row in range(height):
                start = ((y + row) * 1280 + x) * 3
                image[start : start + width * 3] = pixels[
                    row * width * 3 : (row + 1) * width * 3
                ]
        (root / f"{mode}.png").write_bytes(png(1280, 720, image))
    tasks["BattleOperAvailable"] = {"specialParams": [0]}
    tasks["BattleOperRole"] = {"templThreshold": 0.99}
    task_dir = root / "resource/tasks"
    task_dir.mkdir()
    (task_dir / "fixture.json").write_text(json.dumps(tasks), encoding="utf-8")
    config = json.loads(
        (settings.resource_dir / "resource/config.json").read_text(encoding="utf-8")
    )
    # Never invoke adb: every connection command runs this local fixture.
    command = f'[Adb] "{Path(__file__).resolve()}" device "{root}"'
    config["connection"] = [
        {
            "configName": "BattleFixture",
            "devices": f"{command} devices",
            "addressRegex": "(.+)\tdevice",
            "connect": f"{command} connect",
            "uuid": f"{command} uuid",
            "version": f"{command} version",
            "display": f"{command} display",
            "screencapEncode": f"{command} capture",
            "click": f"{command} click [x] [y]",
            "swipe": f"{command} swipe [x1] [y1] [x2] [y2] [duration]",
        }
    ]
    (root / "resource/config.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "mode").write_text("paused", encoding="utf-8")
    return root


def device(root, action, args):
    mode = (root / "mode").read_text(encoding="utf-8")
    event = {
        "action": action,
        "args": args,
        "mode": mode,
        "at_ms": time.time_ns() // 10**6,
    }
    if action == "click" and 1170 <= int(args[0]) <= 1230 and 20 <= int(args[1]) <= 80:
        event["action"] = "resume" if mode == "paused" else "pause"
        mode = "running" if mode == "paused" else "paused"
        (root / "mode").write_text(mode, encoding="utf-8")
    with (root / "events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\n")
    if action == "capture":
        sys.stdout.buffer.write((root / f"{mode}.png").read_bytes())
    elif action in ("click", "swipe"):
        # Make the interval between boundary acquisition and later analysis observable.
        time.sleep(0.25)
        print("OK")
    else:
        print(
            {
                "devices": "fixture\tdevice",
                "connect": "connected",
                "uuid": "0123456789abcdef",
                "version": "12",
                "display": "1280 720",
            }[action]
        )


if __name__ == "__main__":
    if sys.argv[1] == "device":
        device(Path(sys.argv[2]), sys.argv[3], sys.argv[4:])
    elif sys.argv[1] == "worker":
        from maa_battle_mcp import native

        class FixtureCore(native.NativeCore):
            def __init__(self, settings, progress):
                super().__init__(settings, progress)
                self._require(
                    self.lib.AsstLoadResource(
                        str(settings.user_dir / "fixture").encode("utf-8")
                    ),
                    "AsstLoadResource(synthetic fixture)",
                )

        native.NativeCore = FixtureCore
        native._worker()
    else:
        raise ValueError(f"Unknown fixture mode: {sys.argv[1]}")
