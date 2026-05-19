"""clamp old duofeng rolls to new per-tier max

Revision ID: 0009_clamp_duofeng
Revises: 0008_historical_max_infamy
Create Date: 2026-05-19
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0009_clamp_duofeng"
down_revision = "0008_historical_max_infamy"
branch_labels = None
depends_on = None

# New per-tier max values for duofeng
DUOFENG_MAX_BY_TIER: dict[str, dict[str, int]] = {
    "low": {"atk_pct": 8, "agi_pct": 8},
    "mid": {"atk_pct": 11, "agi_pct": 11},
    "high": {"atk_pct": 14, "agi_pct": 14},
    "peak": {"atk_pct": 16, "agi_pct": 16},
    "supreme": {"atk_pct": 18, "agi_pct": 18},
}


def _clamp_duofeng(spirit_json: str) -> str | None:
    """Parse spirit_json, clamp duofeng rolls if present. Returns new JSON or None if unchanged."""
    if not spirit_json:
        return None
    try:
        data = json.loads(spirit_json)
    except (json.JSONDecodeError, TypeError):
        return None

    power = data.get("power")
    if not power or power.get("power_id") != "duofeng":
        return None

    tier = data.get("tier", "")
    caps = DUOFENG_MAX_BY_TIER.get(tier)
    if not caps:
        return None

    rolls = power.get("rolls", {})
    changed = False

    for key in ("atk_pct", "agi_pct"):
        old_val = rolls.get(key, 0)
        cap = caps[key]
        if old_val > cap:
            rolls[key] = cap
            changed = True

    if not changed:
        return None

    data["power"]["rolls"] = rolls
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)  # noqa: F821
    table_names = set(inspector.get_table_names())

    if "artifacts" not in table_names:
        return

    columns = {col["name"] for col in inspector.get_columns("artifacts")}
    if "spirit_json" not in columns:
        return

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, spirit_json FROM artifacts WHERE spirit_json != ''")  # noqa: F821
    ).fetchall()

    updated = 0
    for row in rows:
        new_json = _clamp_duofeng(row.spirit_json)
        if new_json is not None:
            conn.execute(
                sa.text("UPDATE artifacts SET spirit_json = :json WHERE id = :id"),  # noqa: F821
                {"json": new_json, "id": row.id},
            )
            updated += 1

    # Also check spirit_pending_json (器灵炼制中)
    rows2 = conn.execute(
        sa.text("SELECT id, spirit_pending_json FROM artifacts WHERE spirit_pending_json != ''")  # noqa: F821
    ).fetchall()

    for row in rows2:
        new_json = _clamp_duofeng(row.spirit_pending_json)
        if new_json is not None:
            conn.execute(
                sa.text("UPDATE artifacts SET spirit_pending_json = :json WHERE id = :id"),  # noqa: F821
                {"json": new_json, "id": row.id},
            )
            updated += 1

    if updated > 0:
        print(f"[0009] Clamped duofeng rolls for {updated} spirit record(s)")


def downgrade() -> None:
    # Data migration, no structural revert possible.
    # Roll values were capped downward; old higher values are lost by design.
    pass
