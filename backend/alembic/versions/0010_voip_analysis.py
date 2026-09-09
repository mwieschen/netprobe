"""VoIP-Tiefenanalyse: OPTIONS-/TLS-Details + PCAP-Analyse-Felder

Revision ID: 0010_voip_analysis
Revises: 0009_health_checks
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_voip_analysis"
down_revision: str | None = "0009_health_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STR_COLS = {
    "server_header": 255,
    "supported_methods": 255,
    "tls_version": 16,
    "tls_cipher": 64,
    "cert_subject": 255,
    "cert_issuer": 255,
    "capture_path": 512,
}
_JSON_COLS = ("sip_analysis_json", "codec_analysis_json", "rtp_analysis_json")


def upgrade() -> None:
    for name, length in _STR_COLS.items():
        op.add_column("voip_tests", sa.Column(name, sa.String(length), nullable=True))
    for name in _JSON_COLS:
        op.add_column(
            "voip_tests", sa.Column(name, sa.Text(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("voip_tests") as batch:
        for name in _JSON_COLS:
            batch.drop_column(name)
        for name in _STR_COLS:
            batch.drop_column(name)
