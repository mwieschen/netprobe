"""Wachtl-Clients Phase 2: Störungsmeldungen und Client-Hinweise

Revision ID: 0015_wachtl_reports_findings
Revises: 0014_wachtl_clients
Create Date: 2026-10-23
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_wachtl_reports_findings"
down_revision: str | None = "0014_wachtl_clients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wachtl_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("probe_id", sa.Integer(), sa.ForeignKey("wachtl_probes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("wachtl_sites.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("report_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("duration", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reported_user", sa.String(128), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("done_by", sa.String(64), nullable=True),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("diagnostic_run_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("probe_id", "report_id", name="uq_wachtl_reports_probe_report"),
    )
    op.create_index("ix_wachtl_reports_probe_id", "wachtl_reports", ["probe_id"])
    op.create_index("ix_wachtl_reports_site_id", "wachtl_reports", ["site_id"])
    op.create_index("ix_wachtl_reports_occurred_at", "wachtl_reports", ["occurred_at"])

    op.create_table(
        "wachtl_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("probe_id", sa.Integer(), sa.ForeignKey("wachtl_probes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("wachtl_sites.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("finding_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("adapter", sa.String(255), nullable=True),
        sa.Column("adapter_description", sa.String(255), nullable=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("solution", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("probe_id", "finding_id", name="uq_wachtl_findings_probe_finding"),
    )
    op.create_index("ix_wachtl_findings_probe_id", "wachtl_findings", ["probe_id"])
    op.create_index("ix_wachtl_findings_site_id", "wachtl_findings", ["site_id"])
    op.create_index("ix_wachtl_findings_detected_at", "wachtl_findings", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_wachtl_findings_detected_at", table_name="wachtl_findings")
    op.drop_index("ix_wachtl_findings_site_id", table_name="wachtl_findings")
    op.drop_index("ix_wachtl_findings_probe_id", table_name="wachtl_findings")
    op.drop_table("wachtl_findings")
    op.drop_index("ix_wachtl_reports_occurred_at", table_name="wachtl_reports")
    op.drop_index("ix_wachtl_reports_site_id", table_name="wachtl_reports")
    op.drop_index("ix_wachtl_reports_probe_id", table_name="wachtl_reports")
    op.drop_table("wachtl_reports")
