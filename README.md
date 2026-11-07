# project-O2-backend

FastAPI + SQLAlchemy + PostgreSQL API for the Project O2 finance platform.
Setup and the run commands are in the repository root `README.md`.

## Client invoice workflow

Every client invoice starts as a proforma and becomes a GST tax invoice only when a manager issues it.

1. **Proforma draft** (`Draft`): editable, numbered `PI/<FY>/<NNNN>`. Its date can be backdated to the 1st of the current month, never into the future. No GST liability yet.
2. **Awaiting Client**: a Finance Manager, CFO or CEO approves the proforma (`send-to-client`), which sends the client a secure link (`/i/<token>`) to approve or request changes. They can also record an offline approval with a note. Executives cannot do either.
3. **Client Approved**: frozen.
4. **Issued** (`Sent`): a Finance Manager, CFO or CEO who did not create the invoice issues it. It gets the next `INV/<FY>/<NNNN>` number, is dated the proforma date when that is in the month of issue (else today, India time), is due by the 15th of the next month, is locked, and the client gets a view link.

- Numbers come from `document_sequences`, are consecutive per Indian financial year, and are never reused (the CEO data erase keeps that table).
- Issuing is refused if `COMPANY_LEGAL_NAME` or `COMPANY_GSTIN` is blank. Optiminastic's registered details and bank account are built-in defaults in `app/config.py`; env vars override them (see `.env.example`).
- The printed document follows Optiminastic's paper template (branded header, ruled grid, CGST/SGST or IGST, amount in words, bank note, seal, signature, footer). `GET /api/invoices/{id}/document` (any signed-in user) and the client link return the same client-safe view, including `place_of_supply` (the client GSTIN's state, or ours for intra-state) and `amount_in_words` (Indian lakh/crore wording). Built in `app/services/invoice_document.py`; drawn by the frontend's `components/invoice/InvoiceDocument.tsx` at `/portal/invoices/<id>/print`.
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

- TDS always applies to client invoices (no on/off switch). The default rate is 2% (section 194C, or 194J for technical services); it can be changed per invoice.
- Each referral agent's page has **Download MOU (PDF)**: a one-page Memorandum of Understanding with the agent's commission %, when commission is earned and paid, TDS on commission (section 194H, 2%), bank details and signature blocks. Not offered for the in-house agent. Needs `COMPANY_LEGAL_NAME`. Code: `app/services/agent_mou.py` (reportlab).

## Client receipts

Client receipts (stored as `payments`, `POST /api/invoices/{id}/payments`) are always received by bank: the mode is recorded as `Bank` and the bank name (the account the money came into) is required, with the UTR as reference. Payments recorded earlier keep their old mode (NEFT, UPI, etc.).

`tds_deducted` is optional. Left out, the invoice's remaining expected TDS is counted as deducted once the cash clears everything else (clients pay net of TDS); a part payment that does not clear it counts none.
The bank is chosen from our own accounts (`bank_account_id`), listed on the **Banks** page: `GET/POST /api/banks`, `PATCH/DELETE /api/banks/{id}`. A bank is kept by name and the last four digits of the account only; the receipt saves its label ("IndusInd Bank ··2018") in `bank_name`. Managers and above add, edit and deactivate banks; every role lists the active ones. A bank with receipts cannot be deleted, only deactivated. A typed `bank_name` is still accepted from API callers.

## Input rules

Every request schema uses the shared finance field types in `app/schemas/fields.py`; the identifier formats live in `app/services/identifiers.py`.
A bad value returns 422 with a plain message per field.

- GSTIN: 15 characters, a real state code and a matching check character. PAN: 10 characters with a valid holder type; when a GSTIN is also given, the PAN must be the one inside it.
- IFSC (4 letters, 0, 6 characters), account number (9 to 18 digits), Udyam number, CIN or LLPIN, SAC (4 to 8 digits).
- Phone: Indian mobile, landline with STD code, or +country code. Spaces and dashes are removed before saving.
- Vendor bill numbers follow GST rule 46: up to 16 letters, digits, `-` or `/`. Bank references (UTR, RRN, cheque) are 6 to 22 letters or digits; challan and Form 16A numbers 5 to 20.
- Amounts are rupees with at most 2 decimals, never negative, at most ₹10,000 crore; invoice values, receipts and payment requests must be above zero.
- GST rate must be a slab (0, 5, 12, 18, 28, 40). TDS rate is 0 to 20%.
- Dates: receipts, vendor bills, certificates and deposits cannot be in the future or before 1 July 2017; a receipt cannot predate its invoice; a new invoice's due date cannot be in the past; end dates cannot precede start dates.
- Rejecting a payment needs a reason.
- Identifiers are trimmed and upper-cased. Response schemas stay lenient, so records saved before these rules still load; they must be corrected when next edited.

The portal mirrors these rules on its inputs (`frontend/lib/validation.ts`) so typos are caught while typing.

## Month filter

Taxation (CEO/CFO/Finance Manager, `?month=`): `GET /api/taxation/summary`, `GET /api/taxation/gst/sales`, `GET /api/taxation/gst/purchases`, `GET /api/taxation/tds/clients`, `GET /api/taxation/tds/vendors`, `PATCH /api/taxation/client-tds/{payment_id}/certificate`, `PATCH /api/taxation/vendor-tds/{vendor_invoice_id}/deposit`. Vendor bills take `gst_rate` (0/5/12/18/28/40), `is_interstate` and `tds_section`.

Projects: `GET/POST /api/projects` (`?month=` shows projects running that month), `GET/PATCH/DELETE /api/projects/{id}` (delete refused once vendors have invoiced it), `GET /api/projects/{id}/vendors/{vendor_id}/work-order.pdf` (first download assigns the WO number), `POST .../work-order/send` (emails the PDF; `emailed: false` when SMTP is not set up). Vendor invoices take an optional `project_id`.

Vendors: `POST /api/vendors` requires `business_name`, `email`, `gst_number`, `address`, `msme` (true/false) and at least one `bank_accounts` entry; `msme_number` (dropped when `msme` is false), `coi`, `phone` and `pan` are optional, and the PAN is taken from the GSTIN when left out. `PATCH /api/vendors/{id}` sends only what changes but cannot blank a required field or leave no bank account.

Bulk invoice upload: `GET /api/invoices/bulk/template` (xlsx), `POST /api/invoices/bulk/preview` (checks a file, creates nothing), `POST /api/invoices/bulk` (creates all rows as draft proformas in one transaction, or returns 422 and creates none).

List endpoints accept `?month=YYYY-MM` (omit it for all time): invoices, dashboard summary, taxation summary/receipts/GST pending, approvals, reports, bank statements, audit trail and vendor invoices.

- Months are Indian calendar months, matching GST and TDS periods. Dates compare directly; timestamps are converted to India time first (`app/core/period.py`).
- Invoices use the tax invoice date, or the proforma date before issue. Payments use the payment date.
- The portal shows one month picker on each of these pages; it opens on the current month and the choice carries across pages for the session.

## Service catalogue and project-based invoicing

The catalogue (`services`) is the one-time list of what Optiminastic sells: title and SAC code (4 to 8 digits). The portal no longer asks for a description or GST rate; the rate stays 18% on the record, and older services keep their description.
On first start the catalogue gets Optiminastic's standard list of 10 services with their SAC codes (`app/services/catalogue_seed.py`), once: a `data_upgrades` marker stops it running again, so a service later renamed or deleted is not brought back. A CEO data erase clears the marker, so the list returns on the next start. `SEED_SERVICE_CATALOGUE=false` turns it off.
Each project bills one or more services (`project_services`), and every new invoice is raised against a project, with one line (`invoice_lines`) per service billed.

- A new project needs a client and at least one active service (`service_ids`; a single `service_id` is still accepted), plus an optional `contract_value`: the fee agreed with the client, before GST. The code is generated (`PRJ-0001`) when left out, a start date is kept as the 1st of its month, and the report is due on the 25th of the next month. The existing `budget` stays the vendor cost budget.
- A new invoice needs `project_id` and either `lines` (`service_id`, `taxable_value`, optional `description`, one per project service) or a single `taxable_value` billed on the project's first service. Each line copies its service's title, SAC code and GST rate; the header totals are the sum of the lines and its service fields mirror the first line, so registers and reports read as before.
- IGST is decided by the client's GSTIN state against ours; `is_interstate` counts only for a client without a GSTIN.
- Moving a proforma to another project re-copies those details. Once the tax invoice is issued it keeps its copy: later project or catalogue edits never rewrite it, and `project_id` is locked like the client. Invoices raised before lines existed read as one line from their header.
- Project figures: `billed` (taxable value of its invoices that are not Cancelled, proformas included), `left_to_bill` (contract value minus billed; null when no contract value is set), `over_contract`, and `margin` (billed minus vendor invoices). Billing past the contract value warns in the invoice form but is allowed.
- A project with client invoices cannot be deleted. A service used on any invoice or project cannot be deleted, only deactivated.
- Bulk upload rows name a project code (column `Project`); the `Service` column names which of its services the row bills when the project has several.
- Finance Managers, the CFO and the CEO maintain the catalogue and projects; every role can raise invoices against a project. "+ New project" in the invoice form is shown only to roles that can create projects.
- Invoices raised before projects have no project; projects made before services were required must be given one before they can be invoiced.
- Clients carry `msme` (yes/no) and an optional Udyam `msme_number`, kept only while `msme` is true.
- Code: `app/services/service_catalog.py` (`project_invoice_fields`), `app/services/projects.py`, `app/routers/projects.py`, `app/routers/invoices.py`, `app/services/bulk_invoices.py`.

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

## Locked out

Nobody can sign in? Set a new password from the server; nothing else changes:

```
docker compose exec api python -m app.reset_password
```

It lists the CEO accounts, asks for the email and the new password (typed twice, never shown), and reactivates the account.
