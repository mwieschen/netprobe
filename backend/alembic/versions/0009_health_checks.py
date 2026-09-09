"""System-Health-Checks (health_probes, health_check_results)

Revision ID: 0009_health_checks
Revises: 0008_traceroute
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_health_checks"
down_revision: str | None = "0008_traceroute"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("check_type", sa.String(8), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "health_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "probe_id",
            sa.Integer(),
            sa.ForeignKey("health_probes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("response_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.String(255), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_health_check_results_ts", "health_check_results", ["ts"])
    op.create_index("ix_health_check_results_probe_id", "health_check_results", ["probe_id"])


def downgrade() -> None:
    op.drop_index("ix_health_check_results_probe_id", table_name="health_check_results")
    op.drop_index("ix_health_check_results_ts", table_name="health_check_results")
    op.drop_table("health_check_results")
    op.drop_table("health_probes")
