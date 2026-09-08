"""Initiales Schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default="null"),
    )

    op.create_table(
        "speedtest_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_mbps", sa.Float(), nullable=True),
        sa.Column("upload_mbps", sa.Float(), nullable=True),
        sa.Column("ping_ms", sa.Float(), nullable=True),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("packet_loss", sa.Float(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("server_name", sa.String(255), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_speedtest_results_ts", "speedtest_results", ["ts"])

    op.create_table(
        "wan_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "wan_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("wan_targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("loss_pct", sa.Float(), nullable=True),
        sa.Column("rtt_min", sa.Float(), nullable=True),
        sa.Column("rtt_avg", sa.Float(), nullable=True),
        sa.Column("rtt_max", sa.Float(), nullable=True),
        sa.Column("rtt_stddev", sa.Float(), nullable=True),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_wan_results_target_id", "wan_results", ["target_id"])
    op.create_index("ix_wan_results_ts", "wan_results", ["ts"])

    op.create_table(
        "ping_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "ping_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("ping_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("rtt_ms", sa.Float(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_ping_samples_job_id", "ping_samples", ["job_id"])
    op.create_index("ix_ping_samples_ts", "ping_samples", ["ts"])

    op.create_table(
        "ping_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("ping_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket_ts", sa.DateTime(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rtt_min", sa.Float(), nullable=True),
        sa.Column("rtt_avg", sa.Float(), nullable=True),
        sa.Column("rtt_max", sa.Float(), nullable=True),
    )
    op.create_index("ix_ping_aggregates_job_id", "ping_aggregates", ["job_id"])
    op.create_index("ix_ping_aggregates_bucket_ts", "ping_aggregates", ["bucket_ts"])

    op.create_table(
        "captures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("iface", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("max_packets", sa.Integer(), nullable=True),
        sa.Column("max_filesize_mb", sa.Integer(), nullable=True),
        sa.Column("ringbuffer", sa.Integer(), nullable=True),
        sa.Column("bpf", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("packet_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_captures_ts", "captures", ["ts"])


def downgrade() -> None:
    for table in (
        "captures",
        "ping_aggregates",
        "ping_samples",
        "ping_jobs",
        "wan_results",
        "wan_targets",
        "speedtest_results",
        "app_settings",
        "audit_log",
        "users",
    ):
        op.drop_table(table)
