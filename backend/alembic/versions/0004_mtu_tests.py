"""MTU-Tests (mtu_tests)

Revision ID: 0004_mtu_tests
Revises: 0003_iperf_results
Create Date: 2026-09-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_mtu_tests"
down_revision: str | None = "0003_iperf_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mtu_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("path_mtu", sa.Integer(), nullable=True),
        sa.Column("min_mtu", sa.Integer(), nullable=False, server_default="68"),
        sa.Column("max_mtu", sa.Integer(), nullable=False, server_default="1500"),
        sa.Column("probe_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_mtu_tests_ts", "mtu_tests", ["ts"])


def downgrade() -> None:
    op.drop_table("mtu_tests")
