"""Reports (reports)

Revision ID: 0005_reports
Revises: 0004_mtu_tests
Create Date: 2026-09-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_reports"
down_revision: str | None = "0004_mtu_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("capture_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("customer", sa.String(200), nullable=False, server_default=""),
        sa.Column("location", sa.String(200), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sections", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("has_pdf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_reports_ts", "reports", ["ts"])


def downgrade() -> None:
    op.drop_table("reports")
