"""Network Diagnostics (diagnostic_presets, diagnostic_runs)

Nur neue Tabellen, keine Änderung an bestehenden. Die Sub-Tests (Ping/WAN,
Retransmission, MTU, Traceroute, Speedtest) laufen weiterhin über ihre eigenen
Services/Tabellen; hier wird nur die Sammel-Ansicht + Historie gespeichert.

Revision ID: 0013_network_diagnostics
Revises: 0012_retransmission_active
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_network_diagnostics"
down_revision: str | None = "0012_retransmission_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_presets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("targets_json", sa.Text(), nullable=False),
        sa.Column("tests_json", sa.Text(), nullable=False),
        sa.Column("interface", sa.String(64), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "diagnostic_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("interface", sa.String(64), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("targets_json", sa.Text(), nullable=False),
        sa.Column("tests_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("overall_status", sa.String(16), nullable=True),
        sa.Column("results_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_diagnostic_runs_ts", "diagnostic_runs", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_diagnostic_runs_ts", table_name="diagnostic_runs")
    op.drop_table("diagnostic_runs")
    op.drop_table("diagnostic_presets")
