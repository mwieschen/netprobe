"""VoIP-/3CX-Monitoring (voip_servers, voip_tests)

Revision ID: 0007_voip
Revises: 0006_reports_extended
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_voip"
down_revision: str | None = "0006_reports_extended"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voip_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("sip_port", sa.Integer(), nullable=False, server_default="5060"),
        sa.Column("sip_tls_port", sa.Integer(), nullable=False, server_default="5061"),
        sa.Column("webrtc_ports", sa.String(64), nullable=False, server_default="443,5001"),
        sa.Column("sip_transport", sa.String(8), nullable=False, server_default="udp"),
        sa.Column("check_registration", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_s", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "voip_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("voip_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("sip_udp_ok", sa.Boolean(), nullable=True),
        sa.Column("sip_tcp_ok", sa.Boolean(), nullable=True),
        sa.Column("sip_tls_ok", sa.Boolean(), nullable=True),
        sa.Column("webrtc_results_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("sip_response_ms", sa.Float(), nullable=True),
        sa.Column("sip_response_code", sa.Integer(), nullable=True),
        sa.Column("tls_handshake_ms", sa.Float(), nullable=True),
        sa.Column("tls_verified", sa.Boolean(), nullable=True),
        sa.Column("tls_cert_expiry", sa.DateTime(), nullable=True),
        sa.Column("loss_pct", sa.Float(), nullable=True),
        sa.Column("rtt_avg_ms", sa.Float(), nullable=True),
        sa.Column("registration_ok", sa.Boolean(), nullable=True),
        sa.Column("registration_detail", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_voip_tests_ts", "voip_tests", ["ts"])
    op.create_index("ix_voip_tests_server_id", "voip_tests", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_voip_tests_server_id", table_name="voip_tests")
    op.drop_index("ix_voip_tests_ts", table_name="voip_tests")
    op.drop_table("voip_tests")
    op.drop_table("voip_servers")
