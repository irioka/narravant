"""Import中だけ保持する、提示済み根拠と恒常的な人物情報。"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from narravant.core.fountain import normalize_speaker_name
from narravant.services.narrative_adaptation import SourceAnchor, SourceCharacterCard, SourceContext


class GroundedVoiceTrait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: SourceAnchor
    conflicting: bool = False


class SourceVoiceTraitsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    traits: list[GroundedVoiceTrait]


def validate_and_merge_traits(
    traits: Sequence[GroundedVoiceTrait],
    source: SourceContext,
    speakers: Sequence[str],
) -> tuple[SourceCharacterCard, ...]:
    offered = {unit.source_anchor.model_dump_json() for unit in source.source_units}
    merged: dict[str, list[str]] = {}
    for trait in traits:
        try:
            aliases = [
                name
                for name in speakers
                if normalize_speaker_name(name) == normalize_speaker_name(trait.character_name)
            ]
            name = (
                trait.character_name
                if trait.character_name in speakers
                else (aliases[0] if len(aliases) == 1 else None)
            )
            if (
                name is None
                or not trait.description.strip()
                or trait.conflicting
                or trait.evidence.model_dump_json() not in offered
                or not source.resolve_anchor(trait.evidence)
            ):
                raise ValueError
        except Exception:
            raise ValueError("source_voice_trait_evidence_invalid") from None
        descriptions = merged.setdefault(name, [])
        if trait.description.strip() not in descriptions:
            descriptions.append(trait.description.strip())
    return tuple(SourceCharacterCard(name, tuple(descriptions)) for name, descriptions in merged.items())
