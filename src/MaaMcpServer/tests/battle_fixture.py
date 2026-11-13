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


MARKERS = {
    "paused": (10, 20, 30),
    "running": (40, 50, 60),
    "unknown": (70, 80, 90),
    "victory": (100, 110, 120),
    "defeat": (130, 140, 150),
    "intro": (140, 150, 160),
    "victory_overlay": (150, 160, 170),
    "conflict": (160, 170, 180),
    "paused_unknown_speed": (170, 180, 190),
    "paused_speed_conflict": (171, 181, 191),
    "paused_localized": (180, 190, 200),
    "pausex_only": (190, 200, 210),
    "misplaced_localized": (200, 210, 220),
    "suffixed_localized": (210, 220, 230),
    "running_localized": (220, 230, 240),
    "victory_localized": (230, 240, 250),
    "paused_english": (15, 25, 35),
    "paused_chinese": (25, 35, 45),
    "paused_bilingual": (35, 45, 55),
}

SPLIT_MODES = (
    "split_victory",
    "split_missing_mission",
    "split_missing_accomplished",
    "split_reversed",
    "split_distant",
    "split_unaligned",
    "split_low_score",
    "split_menu",
    "split_running",
    "split_conflict",
    "split_and_full",
    "split_tutorial",
    "split_results",
)
MARKERS.update({mode: (50 + index, 100, 150) for index, mode in enumerate(SPLIT_MODES)})

SPLIT_PAUSE_MODES = (
    "paused_split",
    "split_pause_prefix",
    "split_pause_suffix",
    "split_pause_reversed",
    "split_pause_distant",
    "split_pause_other_row",
    "split_pause_extra_suffix",
    "running_split",
    "victory_split",
)
MARKERS.update(
    {mode: (80 + index, 110, 160) for index, mode in enumerate(SPLIT_PAUSE_MODES)}
)


def prepare(settings, known):
    summon_variants = {
        "summon",
        "summon_unapproved",
        "summon_duplicate",
        "summon_ambiguous_id",
        "grenade",
        "summon_tl",
    }
    root = settings.user_dir / "fixture"
    templates = root / "resource/template"
    templates.mkdir(parents=True)
    tasks = {}
    patches = []
    for seed, name, x, y in (
        (1, "BattleHpFlag", 500, 20),
        (2, "BattleOfficiallyBegin", 1180, 30),
        (3, "BattleOpersFlag", 100, 590),
        (
            4,
            "BattleOperRoleDrone"
            if known in summon_variants
            else "BattleOperRolePioneer",
            60,
            597,
        ),
        (6, "BattleControlVictory", 400, 300),
        (7, "BattleControlDefeat", 500, 300),
        (8, "BattleControlPaused", 600, 320),
        (9, "BattleControlSpeedOne", 1075, 40),
        (10, "BattleSpeedUpCheck", 1085, 40),
        (11, "BattleControlVictoryMission", 102, 329),
        (12, "BattleControlVictoryAccomplished", 94, 353),
    ):
        width, height = (116, 29) if seed == 11 else (203, 34) if seed == 12 else (8, 8)
        pixels = random.Random(seed).randbytes(width * height * 3)
        if seed == 11:
            # The reported detection boxes overlap by five rows; mask that shared strip.
            pixels = pixels[: -5 * width * 3] + bytes(5 * width * 3)
        (templates / f"{name}.png").write_bytes(png(width, height, pixels))
        tasks[name] = {
            "algorithm": "MatchTemplate",
            "template": f"{name}.png",
            "templThreshold": 0.99,
            "maskRange": [0, 255],
        }
        if name.startswith("BattleControl"):
            tasks[name].update(baseTask="#none", roi=[200, 180, 880, 360])
        if "Speed" in name:
            tasks[name].update(baseTask="#none", roi=[1060, 20, 80, 70])
        if seed in (11, 12):
            tasks[name].update(roi=[0, 0, 1280, 720], templThreshold=0.5)
        if seed == 11:
            tasks[name]["maskRange"] = [1, 255]
        patches.append((name, x, y, width, height, pixels))
    if known == "terminal_ocr":
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.load_default(size=24)
        for name in (
            "BattleControlVictory",
            "BattleControlVictoryMission",
            "BattleControlVictoryAccomplished",
        ):
            del tasks[name]
    if known == "speed_templates":
        from PIL import Image

        patches = [
            patch
            for patch in patches
            if patch[0] not in ("BattleControlSpeedOne", "BattleSpeedUpCheck")
        ]
        del tasks["BattleSpeedUpCheck"]
        (templates / "BattleSpeedUpCheck.png").unlink()
        # Exercise the native template alternative when no required OCR text matches.
        tasks["BattleControlSpeedOne"] = {
            "baseTask": "#none",
            "algorithm": "OcrDetect",
            "text": ["NOT A SPEED LABEL"],
            "fullMatch": True,
            "roi": [1060, 20, 80, 70],
        }
        for name, template in (
            ("BattleControlSpeedOne", "BattleSpeedUp.png"),
            ("BattleSpeedUpCheck", "BattleSpeedUpCheck.png"),
        ):
            with Image.open(
                settings.resource_dir / "resource/template/Battle/BattleFlag" / template
            ) as label:
                patches.append(
                    (
                        name,
                        1075,
                        30,
                        label.width,
                        label.height,
                        label.convert("RGB").tobytes(),
                    )
                )
    avatar = random.Random(5).randbytes(60 * 60 * 3)
    patches.append(("avatar", 62, 628, 60, 60, avatar))
    if known == "summon_duplicate":
        for name, x, y, width, height, pixels in tuple(patches):
            if name in ("BattleOpersFlag", "BattleOperRoleDrone", "avatar"):
                patches.append((name, x + 110, y, width, height, pixels))
    patches.append(("running-brightness", 1200, 40, 16, 16, b"\xff" * 16 * 16 * 3))
    # Synthetic WenQuanYi Zen Hei labels: 64 px English / 32 px Chinese, white on black.
    # Keep production OCR tasks for these frames; template fixtures cannot test text filtering.
    ocr_labels = {
        "paused_english": ("pause",),
        "paused_chinese": ("paused-zh-cn",),
        "paused_bilingual": ("pause", "paused-zh-cn"),
        "paused_localized": ("pausex", "paused-zh-cn"),
        "pausex_only": ("pausex",),
        "misplaced_localized": ("pausex", "paused-zh-cn"),
        "suffixed_localized": ("pausex", "paused-zh-cn-suffix"),
        "running_localized": ("pausex", "paused-zh-cn"),
        "victory_localized": ("pausex", "paused-zh-cn"),
    }
    if known in ("localized_ocr", "localized_split_ocr"):
        from PIL import Image

        del tasks["BattleControlPaused"]
        for name, x, y in (
            ("pause", 440, 280),
            ("pausex", 440, 280),
            ("paused-zh-cn", 590, 365),
            ("paused-zh-cn-suffix", 590, 365),
        ):
            with Image.open(
                Path(__file__).parent / "fixtures" / f"{name}.png"
            ) as label:
                patches.append(
                    (
                        name,
                        x,
                        y,
                        label.width,
                        label.height,
                        label.convert("RGB").tobytes(),
                    )
                )
        if known == "localized_split_ocr":
            with Image.open(
                Path(__file__).parent / "fixtures/paused-zh-cn.png"
            ) as label:
                for name, crop, size, x, y in (
                    ("split-pause-prefix", (0, 0, 68, 40), (68, 40), 590, 360),
                    ("split-pause-suffix", (68, 0, 104, 40), (27, 20), 690, 370),
                    ("split-pause-extra", (68, 0, 104, 40), (27, 20), 690, 370),
                ):
                    fragment = label.crop(crop).resize(size).convert("RGB")
                    patches.append(
                        (
                            name,
                            x,
                            y,
                            fragment.width,
                            fragment.height,
                            fragment.tobytes(),
                        )
                    )
    if known:
        cache = settings.user_dir / "cache/avatars"
        cache.mkdir(parents=True)
        game_id = (
            "trap_341_dsjtzd"
            if known == "grenade"
            else "token_10000_silent_healrb"
            if known in summon_variants
            else "char_123_fang"
        )
        (cache / f"{game_id}.png").write_bytes(png(60, 60, avatar))
    if known == "summon_ambiguous_id":
        data = json.loads(
            (settings.resource_dir / "resource/battle_data.json").read_bytes()
        )
        data["chars"]["token_fixture_duplicate"] = data["chars"][
            "token_10000_silent_healrb"
        ]
        (root / "resource/battle_data.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
    for mode, marker in MARKERS.items():
        if mode in ocr_labels and known not in ("localized_ocr", "localized_split_ocr"):
            continue
        if mode in SPLIT_PAUSE_MODES and known != "localized_split_ocr":
            continue
        if mode in SPLIT_MODES and known not in ("terminal", "terminal_ocr"):
            continue
        if (
            mode in SPLIT_MODES
            and known == "terminal_ocr"
            and mode
            not in (
                "split_victory",
                "split_tutorial",
                "split_results",
            )
        ):
            continue
        if known == "terminal" and mode in ("split_tutorial", "split_results"):
            continue
        for speed in (1, 2):
            image = bytearray(1280 * 720 * 3)
            image[:3] = bytes(marker)
            for name, x, y, width, height, pixels in patches:
                if name == "BattleHpFlag" and mode in (
                    "unknown",
                    "victory",
                    "defeat",
                    "split_menu",
                ):
                    continue
                if name == "BattleControlVictory" and mode not in (
                    "victory",
                    "victory_overlay",
                    "conflict",
                    "victory_localized",
                    "victory_split",
                    "split_and_full",
                ):
                    continue
                if name == "BattleControlDefeat" and mode not in (
                    "defeat",
                    "conflict",
                    "split_conflict",
                ):
                    continue
                if name in (
                    "BattleControlVictoryMission",
                    "BattleControlVictoryAccomplished",
                ):
                    if mode not in SPLIT_MODES or known == "terminal_ocr":
                        continue
                    if (
                        name.endswith("Mission") and mode == "split_missing_mission"
                    ) or (
                        name.endswith("Accomplished")
                        and mode == "split_missing_accomplished"
                    ):
                        continue
                    if mode == "split_reversed":
                        y = 380 if name.endswith("Mission") else 329
                    if name.endswith("Accomplished"):
                        if mode == "split_distant":
                            y = 450
                        elif mode == "split_unaligned":
                            x = 500
                        elif mode == "split_low_score":
                            noise = random.Random(13).randbytes(len(pixels))
                            pixels = bytes(
                                noise[i] if i % 4 == 0 else pixel
                                for i, pixel in enumerate(pixels)
                            )
                if name == "BattleControlPaused" and mode not in (
                    "paused",
                    "paused_unknown_speed",
                    "paused_speed_conflict",
                ):
                    continue
                if name == "BattleControlSpeedOne" and (
                    (speed != 1 and mode != "paused_speed_conflict")
                    or mode == "paused_unknown_speed"
                ):
                    continue
                if name == "BattleSpeedUpCheck" and (
                    (speed != 2 and mode != "paused_speed_conflict")
                    or mode == "paused_unknown_speed"
                ):
                    continue
                if name in (
                    "BattleOfficiallyBegin",
                    "running-brightness",
                ) and mode not in (
                    "running",
                    "running_localized",
                    "running_split",
                    "split_running",
                ):
                    continue
                if name.startswith("split-pause-"):
                    if mode not in SPLIT_PAUSE_MODES:
                        continue
                    if name.endswith("prefix"):
                        if mode == "split_pause_suffix":
                            continue
                        if mode == "split_pause_reversed":
                            x = 647
                        elif mode == "split_pause_distant":
                            x = 570
                    else:
                        if mode == "split_pause_prefix":
                            continue
                        if name.endswith("extra"):
                            if mode != "split_pause_extra_suffix":
                                continue
                        elif mode == "split_pause_reversed":
                            x = 590
                        elif mode == "split_pause_extra_suffix":
                            x = 655
                        elif mode == "split_pause_other_row":
                            y = 400
                if name in ("pause", "pausex", "paused-zh-cn", "paused-zh-cn-suffix"):
                    if name not in ocr_labels.get(mode, ()):
                        continue
                    if name == "paused-zh-cn" and mode == "misplaced_localized":
                        x, y = 200, 450
                for row in range(height):
                    start = ((y + row) * 1280 + x) * 3
                    image[start : start + width * 3] = pixels[
                        row * width * 3 : (row + 1) * width * 3
                    ]
            if known == "terminal_ocr" and mode in SPLIT_MODES:
                rendered = Image.frombytes("RGB", (1280, 720), bytes(image))
                draw = ImageDraw.Draw(rendered)
                mission = (
                    "MISSION OBJECTIVES" if mode == "split_tutorial" else "MISSION"
                )
                accomplished = (
                    "ACCOMPLISHED TASKS"
                    if mode == "split_tutorial"
                    else "RESULTS"
                    if mode == "split_results"
                    else "ACCOMPLISHED"
                )
                if mode != "split_missing_mission":
                    draw.text((102, 329), mission, font=font, fill="white")
                if mode != "split_missing_accomplished":
                    draw.text((94, 359), accomplished, font=font, fill="white")
                if mode == "split_results":
                    draw.text((541, 17), "95/95", font=font, fill="white")
                image = rendered.tobytes()
            (root / f"{mode}-{speed}x.png").write_bytes(png(1280, 720, image))
    tasks["BattleOperAvailable"] = {"specialParams": [0]}
    tasks["BattleOperRole"] = {"templThreshold": 0.99}
    if known == "ocr_pause":
        tasks["BattleControlPaused"] = {
            "baseTask": "#none",
            "algorithm": "OcrDetect",
            "text": ["NOT A PAUSE LABEL"],
            "fullMatch": True,
            "roi": [200, 180, 880, 360],
        }
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
    (root / "mode").write_text(
        "paused_split"
        if known == "localized_split_ocr"
        else "paused_localized"
        if known == "localized_ocr"
        else "paused",
        encoding="utf-8",
    )
    (root / "speed").write_text("1")
    if known == "localized_split_ocr":
        (root / "pause-target").write_text("paused_split")
    if isinstance(known, str) and known.startswith("preparation:"):
        from preparation_fixture import prepare as prepare_preparation

        prepare_preparation(root, tasks, known.split(":", 1)[1])
        (task_dir / "fixture.json").write_text(json.dumps(tasks), encoding="utf-8")
    return root


def device(root, action, args):
    mode = (root / "mode").read_text(encoding="utf-8")
    if (root / "preparation_variant").exists() and action in ("click", "swipe"):
        from preparation_fixture import navigate

        next_mode = navigate(root, action, args, mode)
        (root / "mode").write_text(next_mode)
    transition = root / "transition.json"
    if action == "capture" and mode == "running" and transition.exists():
        state = json.loads(transition.read_text())
        state["remaining"] -= 1
        if state["remaining"] <= 0:
            mode = state["target"]
            (root / "mode").write_text(mode)
        transition.write_text(json.dumps(state))
    sequence = root / "capture-sequence.json"
    if action == "capture" and sequence.exists():
        remaining = json.loads(sequence.read_text())
        if remaining:
            mode = remaining.pop(0)
            sequence.write_text(json.dumps(remaining))
    event = {
        "action": action,
        "args": args,
        "mode": mode,
        "at_ms": time.time_ns() // 10**6,
    }
    if action == "click" and 1170 <= int(args[0]) <= 1230 and 20 <= int(args[1]) <= 80:
        paused = mode in (
            "paused",
            "paused_localized",
            "paused_english",
            "paused_chinese",
            "paused_bilingual",
            "paused_split",
        )
        event["action"] = "resume" if paused else "pause"
        target = root / "pause-target"
        mode = (
            "running" if paused else target.read_text() if target.exists() else "paused"
        )
        (root / "mode").write_text(mode, encoding="utf-8")
        if event["action"] == "pause" and (root / "pause-sequence.json").exists():
            sequence.write_text((root / "pause-sequence.json").read_text())
    elif (
        action == "click" and 1070 <= int(args[0]) <= 1130 and 20 <= int(args[1]) <= 80
    ):
        event["action"] = "speed"
        if not (root / "ignore-speed").exists():
            (root / "speed").write_text(
                "2" if (root / "speed").read_text() == "1" else "1"
            )
        if (root / "speed-resumes").exists():
            (root / "mode").write_text("running")
    elif action == "click" and (root / "field-click-target").exists():
        (root / "mode").write_text((root / "field-click-target").read_text())
    with (root / "events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(event) + "\n")
    if action == "capture":
        if (root / "capture-delay").exists():
            delay = json.loads((root / "capture-delay").read_text())
            time.sleep(delay.get(mode, 0) if isinstance(delay, dict) else delay)
        speed = (root / "speed").read_text()
        sys.stdout.buffer.write((root / f"{mode}-{speed}x.png").read_bytes())
        if mode == "unknown" and (root / "return-after-unknown").exists():
            (root / "mode").write_text("paused")
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
