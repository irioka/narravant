"""GenerateContent APIで共有する4カテゴリの閾値。Voices APIには適用しない。"""

from pathlib import Path

import yaml

DEFAULT_SAFETY_THRESHOLD = "BLOCK_NONE"
SAFETY_THRESHOLDS = frozenset({"OFF", "BLOCK_NONE", "BLOCK_ONLY_HIGH", "BLOCK_MEDIUM_AND_ABOVE", "BLOCK_LOW_AND_ABOVE"})

_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)


def validate_safety_threshold(value: object) -> str:
    """不正な設定をサービスへ送らず、起動時に検出する。"""
    if not isinstance(value, str) or value not in SAFETY_THRESHOLDS:
        raise ValueError("gemini.safety_threshold must be " + ", ".join(sorted(SAFETY_THRESHOLDS)))
    return value


def load_safety_threshold(config_path: Path) -> str:
    """研究runner用。APIキーやclientを使わず共通YAMLの設定だけを読む。"""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("gemini"), dict):
        raise ValueError("gemini.safety_threshold is required")
    return validate_safety_threshold(config["gemini"].get("safety_threshold"))


def gemini_safety_settings(threshold: str = DEFAULT_SAFETY_THRESHOLD) -> list[dict[str, str]]:
    """SDKのsafety_settingsとRESTのsafetySettingsに使う新しい値を返す。"""
    threshold = validate_safety_threshold(threshold)
    return [{"category": category, "threshold": threshold} for category in _CATEGORIES]
