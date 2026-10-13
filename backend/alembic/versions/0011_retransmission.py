"""TCP-Retransmission-Monitoring (retransmission_tests/_targets/_timeline)

Fügt ausschließlich neue Tabellen hinzu sowie eine nullable Spalte an der
bestehenden ``reports``-Tabelle (analog zu ``capture_id``/``job_id`` – ein
Report bleibt gültig, auch wenn der zugrunde liegende Test gelöscht wird).
Bestehende Tabellen/Daten bleiben unverändert.

Revision ID: 0011_retransmission
Revises: 0010_voip_analysis
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_retransmission"
down_revision: str | None = "0010_voip_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retransmission_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("interface", sa.String(64), nullable=False),
        sa.Column("bpf", sa.String(512), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("total_packets", sa.Integer(), nullable=True),
        sa.Column("tcp_packets", sa.Integer(), nullable=True),
        sa.Column("total_retransmissions", sa.Integer(), nullable=True),
        sa.Column("retransmission_rate_percent", sa.Float(), nullable=True),
        sa.Column("classification", sa.String(32), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_retransmission_tests_ts", "retransmission_tests", ["ts"])

    op.create_table(
        "retransmission_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("retransmission_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_ip", sa.String(64), nullable=False),
        sa.Column("target_port", sa.Integer(), nullable=True),
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retransmissions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("packets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("direction", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_retransmission_targets_test_id", "retransmission_targets", ["test_id"])

    op.create_table(
        "retransmission_timeline",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("retransmission_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("t_offset_s", sa.Float(), nullable=False),
        sa.Column("retrans_per_s", sa.Float(), nullable=False, server_default="0"),
        sa.Column("packets_per_s", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rate_percent", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_retransmission_timeline_test_id", "retransmission_timeline", ["test_id"])

    op.add_column("reports", sa.Column("retrans_test_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("retrans_test_id")

    op.drop_index("ix_retransmission_timeline_test_id", table_name="retransmission_timeline")
    op.drop_table("retransmission_timeline")

    op.drop_index("ix_retransmission_targets_test_id", table_name="retransmission_targets")
    op.drop_table("retransmission_targets")

    op.drop_index("ix_retransmission_tests_ts", table_name="retransmission_tests")
    op.drop_table("retransmission_tests")
