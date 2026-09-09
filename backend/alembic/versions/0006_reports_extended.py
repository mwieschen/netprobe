"""Reports erweitern: JSON-Snapshot + iperf3-Detailfelder

Revision ID: 0006_reports_extended
Revises: 0005_reports
Create Date: 2026-09-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_reports_extended"
down_revision: str | None = "0005_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column("iperf_results", sa.Column("lost_packets", sa.Integer(), nullable=True))
    op.add_column("iperf_results", sa.Column("packets", sa.Integer(), nullable=True))
    op.add_column("iperf_results", sa.Column("out_of_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("iperf_results") as batch:
        batch.drop_column("out_of_order")
        batch.drop_column("packets")
        batch.drop_column("lost_packets")
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("data_json")
