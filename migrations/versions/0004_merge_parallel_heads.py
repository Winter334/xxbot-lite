"""merge parallel 0003 heads

Revision ID: 0004_merge_parallel_heads
Revises: 0003_artifact_affixes, 0003_travel_selected_duration
Create Date: 2026-04-15 16:45:00
"""

from __future__ import annotations


revision = "0004_merge_parallel_heads"
down_revision = ("0003_artifact_affixes", "0003_travel_selected_duration")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
