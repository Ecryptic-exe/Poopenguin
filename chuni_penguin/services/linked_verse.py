"""Original Linked VERSE condition text with explicit icon settings."""

from discord.utils import format_dt

from chuni_penguin.config import IconsConfig
from chuni_penguin.database import LinkedGateCondition
from chuni_penguin.types import Difficulty, LinkLevel


def format_gate_condition(condition: LinkedGateCondition, icons: IconsConfig) -> str:
    deduction = f"{Difficulty(condition.difficulty)}, LIFE {condition.life}, JUSTICE -{condition.damage_justice}, ATTACK -{condition.damage_attack}, MISS -{condition.damage_miss}"

    if condition.recovery_life > 0:
        deduction += (
            f", +{condition.recovery_life}/100 {condition.recovery_life_combo_type}"
        )

    level = LinkLevel(condition.level)
    result = f"{icons.icon(f'link_level_{level.name}', str(level))} ({deduction})"
    extra_condition_parts: list[str] = []

    if condition.is_local_matching_required:
        extra_condition_parts.append("Local matching only")

    if condition.survivors_required is not None and condition.survivors_required > 1:
        extra_condition_parts.append(
            f"{condition.survivors_required} players need to complete with more than 0 LIFE remaining"
        )

    if len(extra_condition_parts) > 0:
        result += f"\n{', '.join(extra_condition_parts)}."

    if condition.end_date is not None:
        result += f"\nLink LEVEL decreases at {format_dt(condition.end_date, 'd')} ({format_dt(condition.end_date, 'R')})"

    return result
