# project-O2-backend

FastAPI + SQLAlchemy + PostgreSQL API for the Project O2 finance platform.
Setup and the run commands are in the repository root `README.md`.

## Client invoice workflow

Every client invoice starts as a proforma and becomes a GST tax invoice only when a manager issues it.

1. **Proforma draft** (`Draft`): editable, numbered `PI/<FY>/<NNNN>`. No GST liability yet.
2. **Awaiting Client**: the client gets a secure link (`/i/<token>`) to approve or request changes. The team can also record an offline approval with a note.
3. **Client Approved**: frozen.
4. **Issued** (`Sent`): a Finance Manager, CFO or CEO who did not create the invoice issues it. It gets the next `INV/<FY>/<NNNN>` number and today's date (India time), is locked, and the client gets a view link.

- Numbers come from `document_sequences`, are consecutive per Indian financial year, and are never reused (the CEO data erase keeps that table).
- Issuing is refused until `COMPANY_LEGAL_NAME` and `COMPANY_GSTIN` are set (see `.env.example`).
- Code: `app/services/invoice_workflow.py`, `app/services/numbering.py`, `app/services/invoice_links.py`, `app/routers/public_invoices.py`.
- Design: `docs/superpowers/specs/2026-10-06-invoice-approval-workflow-design.md` in the workspace root.

## Agents are mandatory

Every client and every invoice is credited to an agent.

- The in-house agent **Opti** (`agents.is_house`) is the default for clients who came in directly. It always earns 0% commission, stays active and cannot be deleted. It is created at startup and recreated after a CEO data erase.
- A new invoice credits its client's agent unless another is chosen. The agent can change while the invoice is a proforma and is locked once the tax invoice is issued.
- Deleting an agent moves its clients and proformas to Opti. An agent credited on an issued tax invoice cannot be deleted; deactivate it instead.
- `GET /api/agents/options` gives every finance role a names-only list for pickers (no commission or bank details).
- Code: `app/services/agents.py`.

## TDS and agent MOUs

- TDS always applies to client invoices (no on/off switch). The default rate is 10%, what clients deduct on professional fees under section 194J; it can be changed per invoice.
- Each referral agent's page has **Download MOU (PDF)**: a one-page Memorandum of Understanding with the agent's commission %, when commission is earned and paid, TDS on commission (section 194H, 2%), bank details and signature blocks. Not offered for the in-house agent. Needs `COMPANY_LEGAL_NAME`. Code: `app/services/agent_mou.py` (reportlab).

## Client payments

Client payments are always received by bank: the mode is recorded as `Bank` and the bank name (the account the money came into) is required, with the UTR as reference. Payments recorded earlier keep their old mode (NEFT, UPI, etc.).

## Month filter

Taxation (CEO/CFO/Finance Manager, `?month=`): `GET /api/taxation/summary`, `GET /api/taxation/gst/sales`, `GET /api/taxation/gst/purchases`, `GET /api/taxation/tds/clients`, `GET /api/taxation/tds/vendors`, `PATCH /api/taxation/client-tds/{payment_id}/certificate`, `PATCH /api/taxation/vendor-tds/{vendor_invoice_id}/deposit`. Vendor bills take `gst_rate` (0/5/12/18/28), `is_interstate` and `tds_section`.

Projects: `GET/POST /api/projects` (`?month=` shows projects running that month), `GET/PATCH/DELETE /api/projects/{id}` (delete refused once vendors have invoiced it), `GET /api/projects/{id}/vendors/{vendor_id}/work-order.pdf` (first download assigns the WO number), `POST .../work-order/send` (emails the PDF; `emailed: false` when SMTP is not set up). Vendor invoices take an optional `project_id`.

Bulk invoice upload: `GET /api/invoices/bulk/template` (xlsx), `POST /api/invoices/bulk/preview` (checks a file, creates nothing), `POST /api/invoices/bulk` (creates all rows as draft proformas in one transaction, or returns 422 and creates none).

List endpoints accept `?month=YYYY-MM` (omit it for all time): invoices, dashboard summary, taxation summary/receipts/GST pending, approvals, reports, bank statements, audit trail and vendor invoices.

- Months are Indian calendar months, matching GST and TDS periods. Dates compare directly; timestamps are converted to India time first (`app/core/period.py`).
- Invoices use the tax invoice date, or the proforma date before issue. Payments use the payment date.
- The portal shows one month picker on each of these pages; it opens on the current month and the choice carries across pages for the session.

## Service catalogue

Every new invoice is for one service from the catalogue (`services`): title, description, SAC code and GST rate.

- Choosing a service copies those details onto the invoice; anything typed on the invoice wins. Later catalogue edits never rewrite invoices already raised.
- Finance Managers, the CFO and the CEO maintain the catalogue (`/api/services`); every role lists active services for the invoice dropdown.
- The service is locked once the tax invoice is issued. A service used on any invoice cannot be deleted, only deactivated.
- Invoices raised before the catalogue existed have no service.
- Code: `app/routers/services.py`, `app/services/service_catalog.py`.

## Schema changes

Production does not run Alembic on deploy.
Each schema change ships as an Alembic revision and as idempotent statements in `app/schema_upgrades.py`, which run at startup.

```bash
alembic upgrade head   # local, when you want the revision history in step
```

## Tests

Tests run against a separate `projecto2_test` database in the same Postgres container and refuse any database whose name does not end in `_test`.

```bash
docker exec projecto2-db psql -U o2_admin -d postgres -c "create database projecto2_test"   # once
pip install -r requirements-dev.txt
python -m pytest
```
