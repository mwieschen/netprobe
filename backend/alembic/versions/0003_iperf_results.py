"""iperf3-Ergebnisse (iperf_results)

Revision ID: 0003_iperf_results
Revises: 0002_capture_jobs
Create Date: 2026-09-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_iperf_results"
down_revision: str | None = "0002_capture_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "iperf_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="5201"),
        sa.Column("protocol", sa.String(8), nullable=False, server_default="tcp"),
        sa.Column("duration_s", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("parallel", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reverse", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_mbps", sa.Float(), nullable=True),
        sa.Column("recv_mbps", sa.Float(), nullable=True),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("lost_percent", sa.Float(), nullable=True),
        sa.Column("retransmits", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_iperf_results_ts", "iperf_results", ["ts"])


def downgrade() -> None:
    op.drop_table("iperf_results")
