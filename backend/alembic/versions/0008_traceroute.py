"""Traceroute / Pfad-Analyse (traceroute_targets, traceroutes, traceroute_hops, geoip_cache)

Revision ID: 0008_traceroute
Revises: 0007_voip
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_traceroute"
down_revision: str | None = "0007_voip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traceroute_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False, server_default="icmp"),
        sa.Column("tcp_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("max_hops", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("queries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("resolve_geo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "traceroutes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("mode", sa.String(8), nullable=False, server_default="icmp"),
        sa.Column("engine", sa.String(16), nullable=False, server_default="mtr"),
        sa.Column("total_hops", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dest_reached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("total_loss_pct", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_traceroutes_ts", "traceroutes", ["ts"])
    op.create_index("ix_traceroutes_target_id", "traceroutes", ["target_id"])
    op.create_table(
        "traceroute_hops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "traceroute_id",
            sa.Integer(),
            sa.ForeignKey("traceroutes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hop_number", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("latency_min_ms", sa.Float(), nullable=True),
        sa.Column("latency_avg_ms", sa.Float(), nullable=True),
        sa.Column("latency_max_ms", sa.Float(), nullable=True),
        sa.Column("loss_pct", sa.Float(), nullable=True),
        sa.Column("sent", sa.Integer(), nullable=True),
        sa.Column("mtu", sa.Integer(), nullable=True),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("asn", sa.String(32), nullable=True),
        sa.Column("isp", sa.String(128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
    )
    op.create_index("ix_traceroute_hops_traceroute_id", "traceroute_hops", ["traceroute_id"])
    op.create_table(
        "geoip_cache",
        sa.Column("ip", sa.String(64), primary_key=True),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("asn", sa.String(32), nullable=True),
        sa.Column("isp", sa.String(128), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default=""),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_geoip_cache_fetched_at", "geoip_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_geoip_cache_fetched_at", table_name="geoip_cache")
    op.drop_table("geoip_cache")
    op.drop_index("ix_traceroute_hops_traceroute_id", table_name="traceroute_hops")
    op.drop_table("traceroute_hops")
    op.drop_index("ix_traceroutes_target_id", table_name="traceroutes")
    op.drop_index("ix_traceroutes_ts", table_name="traceroutes")
    op.drop_table("traceroutes")
    op.drop_table("traceroute_targets")
