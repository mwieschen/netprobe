"""Aktive Retransmission-Tests: Ziel-Presets + Modus-/Verbindungsfelder

Fügt eine neue Tabelle (gespeicherte Ziel-Presets) sowie nullable/defaulted
Spalten an den bestehenden Retransmission-Tabellen hinzu. Bestehende Zeilen
erhalten test_mode="passive" (Serverdefault), alle anderen neuen Spalten
bleiben NULL. Keine bestehenden Daten werden verändert oder gelöscht.

Revision ID: 0012_retransmission_active
Revises: 0011_retransmission
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_retransmission_active"
down_revision: str | None = "0011_retransmission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retransmission_tests",
        sa.Column("test_mode", sa.String(16), nullable=False, server_default="passive"),
    )
    op.add_column("retransmission_tests", sa.Column("target_ips", sa.Text(), nullable=True))
    op.add_column(
        "retransmission_tests",
        sa.Column("concurrent", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column("retransmission_targets", sa.Column("connection_attempts", sa.Integer(), nullable=True))
    op.add_column("retransmission_targets", sa.Column("connection_errors", sa.Integer(), nullable=True))
    op.add_column(
        "retransmission_targets", sa.Column("avg_connect_latency_ms", sa.Float(), nullable=True)
    )

    op.create_table(
        "retransmission_target_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(8), nullable=False, server_default="tcp"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used", sa.DateTime(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("retransmission_target_configs")

    with op.batch_alter_table("retransmission_targets") as batch:
        batch.drop_column("avg_connect_latency_ms")
        batch.drop_column("connection_errors")
        batch.drop_column("connection_attempts")

    with op.batch_alter_table("retransmission_tests") as batch:
        batch.drop_column("concurrent")
        batch.drop_column("target_ips")
        batch.drop_column("test_mode")
