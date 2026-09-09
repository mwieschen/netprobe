"""Langzeit-Captures (capture_jobs)

Revision ID: 0002_capture_jobs
Revises: 0001_initial
Create Date: 2026-09-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_capture_jobs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capture_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("iface", sa.String(64), nullable=False),
        sa.Column("bpf", sa.Text(), nullable=True),
        sa.Column("max_file_size_mb", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_files", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_capture_jobs_created_at", "capture_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_table("capture_jobs")
