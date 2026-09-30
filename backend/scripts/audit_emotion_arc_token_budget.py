"""Measure local synthetic Emotion Arc response shapes without calling Vertex AI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

SCENE_COUNTS = (1, 15, 36, 37, 100, 250, 500)
CHARACTER_COUNTS = (1, 3, 6)
OUTPUT_SAFETY_PERCENT = 80
TURNING_POINT_COUNT = 5


def build_synthetic_analysis(scene_count: int, character_count: int) -> dict[str, Any]:
    """Build a schema-shaped response whose arrays match the requested source scenes."""
    character_profiles = [
        {
            "name": f"Character {index}",
            "external_goal": "Achieve the story objective.",
            "internal_need": "Accept the emotional truth.",
            "fear_or_cost": "Lose the relationship.",
            "obstacle": "Conflicting evidence.",
            "choice": "Act despite the risk.",
            "agency": "Initiates a decisive action.",
            "goal_to_outcome": "The goal changes after the climax.",
            "related_turning_points": [1, 3, 5],
            "emotion_arc": [7] * scene_count,
        }
        for index in range(1, character_count + 1)
    ]
    return {
        "metadata": {
            "title": "Synthetic Emotion Arc Audit",
            "logline": "A synthetic fixture measures structured output shape.",
            "synopsis": "This fixture intentionally contains no screenplay content.",
            "theme_setting": "Evidence changes the choice in scenes #1# through #5#.",
        },
        "valence": [7] * scene_count,
        "tension": [3] * scene_count,
        "characters": character_profiles,
        "turning_points": [
            {
                "tp_number": point_number,
                "availability": "identified",
                "scene_number": min(point_number, scene_count),
                "change": "The available information changes the protagonist's choice.",
                "involved_characters": [
                    {
                        "name": "Character 1",
                        "goal": "Reach the objective.",
                        "conflict": "The cost is uncertain.",
                        "choice": "Commit to the next action.",
                        "action": "Acts on the new evidence.",
                        "change": "The relationship is redefined.",
                    }
                ],
                "reason": None,
            }
            for point_number in range(1, TURNING_POINT_COUNT + 1)
        ],
    }


def serialized_json_bytes(scene_count: int, character_count: int) -> int:
    """Return UTF-8 byte size for one synthetic structured-output response."""
    serialized = json.dumps(
        build_synthetic_analysis(scene_count, character_count),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return len(serialized.encode("utf-8"))


def load_audit_configuration(config_path: Path) -> tuple[int, int, int]:
    """Read only the settings that define the audit matrix and output threshold."""
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        max_output_tokens = int(config["vertex_ai"]["max_output_tokens"])
        max_main_characters = int(config["vertex_ai"]["max_main_characters"])
        max_points = int(config["emotion_arc"]["max_points"])
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid audit configuration: {config_path}") from exc
    if max_output_tokens < 1 or max_main_characters < 1 or max_points < 1:
        raise ValueError("audit configuration values must be positive")
    return max_output_tokens, max_main_characters, max_points


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-only Emotion Arc token-budget structure audit")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config.yaml",
        help="path to backend config.yaml",
    )
    args = parser.parse_args()
    try:
        max_output_tokens, max_main_characters, max_points = load_audit_configuration(args.config)
    except ValueError as exc:
        parser.error(str(exc))

    output_safety_limit = max_output_tokens * OUTPUT_SAFETY_PERCENT // 100
    print(
        "local_only=true "
        f"max_output_tokens={max_output_tokens} output_safety_limit={output_safety_limit} "
        f"max_main_characters={max_main_characters} emotion_arc_max_points={max_points}"
    )
    for scene_count in SCENE_COUNTS:
        for character_count in CHARACTER_COUNTS:
            if character_count > max_main_characters:
                continue
            print(
                f"scene_count={scene_count} character_count={character_count} "
                f"serialized_json_bytes={serialized_json_bytes(scene_count, character_count)}"
            )
    print("token_counts=not_measured reason=Vertex_count_tokens_and_generation_require_explicit_external_approval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
