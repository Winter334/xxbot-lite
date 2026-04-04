from __future__ import annotations

import math

import discord


RARITY_COLORS = {
    "normal": discord.Color.light_grey(),
    "rare": discord.Color.blue(),
    "epic": discord.Color.gold(),
    "legendary": discord.Color.red(),
}

RARITY_BADGES = {
    "normal": "凡",
    "rare": "灵",
    "epic": "玄",
    "legendary": "圣",
}


def format_big_number(value: int) -> str:
    abs_value = abs(value)
    if abs_value < 10_000:
        return str(value)
    if abs_value < 100_000_000:
        return _format_unit(value, 10_000, "万")
    if abs_value < 10_000_000_000_000:
        return _format_unit(value, 100_000_000, "亿")
    return _format_unit(value, 10_000_000_000, "万亿")


def _format_unit(value: int, base: int, suffix: str) -> str:
    scaled = value / base
    rendered = f"{scaled:.2f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def format_progress(current: int, maximum: int, width: int = 5) -> str:
    if maximum <= 0:
        return "▱" * width
    ratio = max(0.0, min(1.0, current / maximum))
    filled = math.floor(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


def format_qi(current: int, maximum: int) -> str:
    return "●" * current + "○" * max(maximum - current, 0)


def format_duration_minutes(total_minutes: int) -> str:
    if total_minutes < 60:
        return f"{total_minutes} 分钟"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
