"""Wachtl-Clients (Standorte, Probes, Checks, Enrollment-Token, verdichtete Messwerte)

Nur neue Tabellen. netprobe dient als Zentrale für Wachtl-Windows-Probes
(Probe-API v1, docs/wachtl-zentrale.md).

Revision ID: 0014_wachtl_clients
Revises: 0013_network_diagnostics
Create Date: 2026-10-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_wachtl_clients"
down_revision: str | None = "0013_network_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wachtl_sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "wachtl_enroll_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("wachtl_sites.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wachtl_enroll_tokens_site_id", "wachtl_enroll_tokens", ["site_id"])

    op.create_table(
        "wachtl_probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("wachtl_sites.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("hostname", sa.String(64), nullable=False),
        sa.Column("os", sa.String(200), nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("last_ip", sa.String(64), nullable=True),
        sa.Column("buffer_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uptime_s", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("addresses_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("deregistered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("site_id", "hostname", name="uq_wachtl_probes_site_host"),
    )
    op.create_index("ix_wachtl_probes_site_id", "wachtl_probes", ["site_id"])

    op.create_table(
        "wachtl_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="ping"),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="target"),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("wachtl_sites.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wachtl_checks_site_id", "wachtl_checks", ["site_id"])

    op.create_table(
        "wachtl_ingest_batches",
        sa.Column("batch_id", sa.String(160), primary_key=True),
        sa.Column("probe_id", sa.Integer(), sa.ForeignKey("wachtl_probes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wachtl_ingest_batches_probe_id", "wachtl_ingest_batches", ["probe_id"])
    op.create_index("ix_wachtl_ingest_batches_received_at", "wachtl_ingest_batches", ["received_at"])

    op.create_table(
        "wachtl_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("probe_id", sa.Integer(), sa.ForeignKey("wachtl_probes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("wachtl_checks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("bucket_ts", sa.DateTime(), nullable=False),
        sa.Column("step_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rtt_min", sa.Float(), nullable=True),
        sa.Column("rtt_max", sa.Float(), nullable=True),
        sa.Column("rtt_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rtt_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jitter_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("jitter_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ttl", sa.Integer(), nullable=True),
        sa.UniqueConstraint("probe_id", "check_id", "step_s", "bucket_ts", name="uq_wachtl_samples_bucket"),
    )
    op.create_index("ix_wachtl_samples_series", "wachtl_samples", ["probe_id", "check_id", "bucket_ts"])
    op.create_index("ix_wachtl_samples_check_id", "wachtl_samples", ["check_id"])
    op.create_index("ix_wachtl_samples_bucket_ts", "wachtl_samples", ["bucket_ts"])


def downgrade() -> None:
    op.drop_index("ix_wachtl_samples_bucket_ts", table_name="wachtl_samples")
    op.drop_index("ix_wachtl_samples_check_id", table_name="wachtl_samples")
    op.drop_index("ix_wachtl_samples_series", table_name="wachtl_samples")
    op.drop_table("wachtl_samples")
    op.drop_index("ix_wachtl_ingest_batches_received_at", table_name="wachtl_ingest_batches")
    op.drop_index("ix_wachtl_ingest_batches_probe_id", table_name="wachtl_ingest_batches")
    op.drop_table("wachtl_ingest_batches")
    op.drop_index("ix_wachtl_checks_site_id", table_name="wachtl_checks")
    op.drop_table("wachtl_checks")
    op.drop_index("ix_wachtl_probes_site_id", table_name="wachtl_probes")
    op.drop_table("wachtl_probes")
    op.drop_index("ix_wachtl_enroll_tokens_site_id", table_name="wachtl_enroll_tokens")
    op.drop_table("wachtl_enroll_tokens")
    op.drop_table("wachtl_sites")
