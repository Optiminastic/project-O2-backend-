"""projects: client projects with several vendors, budgets and work orders

Revision ID: d8a2f6c1e4b7
Revises: c5f1a8e3d7b9
Create Date: 2026-10-08 18:00:00.000000

Idempotent on purpose: production applies the same statements at startup
(app/schema_upgrades.py), so this revision may run against a schema that
already has them.
"""
from typing import Sequence, Union

from alembic import op

from app.schema_upgrades import PROJECTS

revision: str = 'd8a2f6c1e4b7'
down_revision: Union[str, None] = 'c5f1a8e3d7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            code VARCHAR(40) NOT NULL,
            title VARCHAR(200) NOT NULL,
            brand VARCHAR(160),
            description TEXT,
            client_id INTEGER REFERENCES clients(id),
            budget DOUBLE PRECISION NOT NULL,
            start_date DATE,
            end_date DATE,
            expected_report_date DATE,
            status allocationstatus NOT NULL,
            created_by_id INTEGER REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_code_lower ON projects (lower(code))")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_title ON projects (title)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_client_id ON projects (client_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_vendors (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            vendor_id INTEGER NOT NULL REFERENCES vendors(id),
            work_percent DOUBLE PRECISION NOT NULL,
            agreed_cost DOUBLE PRECISION NOT NULL,
            work_order_number VARCHAR(40) UNIQUE,
            work_order_date DATE,
            work_order_sent_at TIMESTAMPTZ,
            work_order_outdated BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_project_vendors_project_vendor UNIQUE (project_id, vendor_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_vendors_project_id ON project_vendors (project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_vendors_vendor_id ON project_vendors (vendor_id)")
    for stmt in PROJECTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE vendor_invoices DROP COLUMN IF EXISTS project_id")
    op.execute("DROP TABLE IF EXISTS project_vendors")
    op.execute("DROP TABLE IF EXISTS projects")
