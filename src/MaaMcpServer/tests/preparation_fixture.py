"""Synthetic formation/selection pages; all OCR and matching still run in Core."""

import json
import random

from PIL import Image, ImageDraw, ImageFont


def prepare(root, tasks, variant):
    templates = root / "resource/template"
    font = ImageFont.load_default(size=24)
    small = ImageFont.load_default(size=20)
    patterns = {}
    for seed, name in enumerate(
        (
            "BattleStartNormal",
            "BattlePreparationNameFlags",
            "BattlePreparationDetail",
            "BattlePreparationReturn",
            "BattleQuickFormationSkillPage",
            "BattlePreparationSkillAnchors",
            "BattlePreparationSkillSelected",
            "BattleQuickFormation-Elite1",
            "BattleQuickFormation-Elite2",
        ),
        100,
    ):
        pattern = Image.frombytes("RGB", (10, 10), random.Random(seed).randbytes(300))
        patterns[name] = pattern
        (templates / f"{name}.png").write_bytes(_png(pattern))
        tasks[name] = {
            "baseTask": "#none",
            "algorithm": "MatchTemplate",
            "template": f"{name}.png",
            "templThreshold": 0.99,
            "roi": [0, 0, 1280, 720],
        }
    tasks["BattlePreparationDetail"].update(roi=[900, 600, 300, 100])
    tasks["BattleFormationOCRNameFlag"] = {
        **tasks["BattlePreparationNameFlags"],
        "roi": [88, 264, 873, 354],
    }
    tasks["BattlePreparationReturn"].update(roi=[0, 0, 100, 80])
    tasks["BattleQuickFormationSkillPage"].update(roi=[0, 320, 180, 40])
    tasks["BattlePreparationElite"] = {
        "baseTask": "#none",
        "algorithm": "MatchTemplate",
        "template": [
            "BattleQuickFormation-Elite1.png",
            "BattleQuickFormation-Elite2.png",
        ],
        "templThreshold": 0.99,
    }
    tasks["BattleStartAll"] = {"next": ["BattleStartNormal"]}
    tasks["BattleFormationOperNames"] = {
        "baseTask": "CharsNameOcrReplace",
        "roi": [-18, 34, 122, 30],
    }
    tasks["BattleFormationOperNamesOldVersion"] = tasks["BattleFormationOperNames"]
    tasks["BattlePreparationLevel"] = {
        "baseTask": "NumberOcrReplace",
        "rectMove": [14, -2, 45, 28],
    }
    skill_count = {"one_skill": 1, "two_skills": 2}.get(variant, 3)
    skills = ["fixture_s1", "fixture_s2", "fixture_s3"][:skill_count]
    names = (
        [
            "Fang",
            "Texas",
            "Alpha",
            "Bravo",
            "Delta",
            "Echo",
            "Foxtrot",
            "Golf",
            "Hotel",
            "India",
            "Juliet",
            "Kilo",
        ]
        if variant == "full_roster"
        else ["Fang", "Texas"]
    )
    chars = {
        game_id: {
            "name": name,
            "profession": "PIONEER",
            "subProfessionId": "pioneer",
            "rarity": 6,
            "position": "MELEE",
            "rangeId": ["0-1"] * 3,
            "skillIds": skills,
        }
        for game_id, name in ((f"char_fixture_{name.lower()}", name) for name in names)
    }
    if variant == "ambiguous":
        chars["char_fixture_duplicate"] = chars["char_fixture_fang"]
    (root / "resource/battle_data.json").write_text(
        json.dumps({"chars": chars, "ranges": {}}), encoding="utf-8"
    )
    formation = Image.new("RGB", (1280, 720))
    draw = ImageDraw.Draw(formation)
    formation.paste(patterns["BattleStartNormal"], (1170, 620))
    slots = (
        [(180 + i % 6 * 130, 280 + i // 6 * 280, name) for i, name in enumerate(names)]
        if variant == "full_roster"
        else []
        if variant == "empty"
        else [(180, 280, "Fang"), (440, 280, "Texas")]
    )
    if variant == "support":
        slots.append((1100, 280, "Fang"))
    (root / "preparation_slots.json").write_text(json.dumps(slots))
    for x, y, name in slots:
        formation.paste(patterns["BattlePreparationNameFlags"], (x, y))
        draw.text(
            (x - 18, y + 34),
            "????" if variant == "unreadable" and name == "Fang" else name,
            font=small,
            fill="white",
        )
        if variant != "unknown_level":
            draw.text((x + 14, y - 2), "50", font=small, fill="white")
        if variant != "unknown_elite":
            formation.paste(patterns["BattleQuickFormation-Elite2"], (x - 30, y - 140))
    _save(root, "formation", formation)
    changed = formation.copy()
    ImageDraw.Draw(changed).rectangle((420, 312, 550, 344), fill="black")
    _save(root, "formation_changed", changed)
    for name in names:
        height = 355 if skill_count < 3 else 525
        canvas = Image.frombytes(
            "RGB", (360, height), random.Random(120).randbytes(360 * height * 3)
        )
        for y in (100, 275, 450)[:skill_count]:
            canvas.paste(patterns["BattlePreparationSkillAnchors"], (75, y))
        if variant != "no_marker":
            selected = {"s1": 1, "s2": 2, "one_skill": 1, "two_skills": 2}.get(
                variant, 3
            )
            canvas.paste(
                patterns["BattlePreparationSkillSelected"],
                (300, 42 + (selected - 1) * 175),
            )
        if variant == "multiple":
            canvas.paste(patterns["BattlePreparationSkillSelected"], (300, 42))
        for offset in (0,) if skill_count < 3 else (0, 100, 170):
            detail = Image.new("RGB", (1280, 720))
            detail.paste(patterns["BattlePreparationDetail"], (1000, 650))
            detail.paste(patterns["BattlePreparationReturn"], (40, 20))
            detail.paste(patterns["BattleQuickFormationSkillPage"], (50, 330))
            ImageDraw.Draw(detail).text(
                (40, 200),
                "Other" if variant == "detail_mismatch" else name,
                font=font,
                fill="white",
            )
            detail.paste(canvas.crop((0, offset, 360, offset + 355)), (0, 365))
            _save(root, f"detail-{name}-{offset}", detail)
    (root / "preparation_variant").write_text(variant)
    (root / "mode").write_text("formation")


def _png(image):
    from io import BytesIO

    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def _save(root, mode, image):
    for speed in (1, 2):
        image.save(root / f"{mode}-{speed}x.png")


def navigate(root, action, args, mode):
    variant = (root / "preparation_variant").read_text()
    if action == "click":
        x, y = map(int, args)
        if mode == "formation" and y < 600 and x < 950:
            slots = json.loads((root / "preparation_slots.json").read_text())
            target = min(
                slots, key=lambda slot: (slot[0] - x) ** 2 + (slot[1] - 134 - y) ** 2
            )
            return f"detail-{target[2]}-0"
        if mode.startswith("detail-") and x < 100 and y < 80:
            return (
                "unknown"
                if variant == "lost_page"
                else "formation_changed"
                if variant == "changed"
                else "formation"
            )
    if action == "swipe" and mode.startswith("detail-"):
        _, name, offset = mode.split("-")
        offset = int(offset)
        if variant != "stuck":
            maximum = 0 if variant in ("one_skill", "two_skills") else 170
            offset = (
                max(0, offset - 100)
                if int(args[3]) > int(args[1])
                else min(maximum, offset + 100)
            )
        return f"detail-{name}-{offset}"
    return mode
