"""What the assistant can do: one entry per O2 API action. Each tool's input schema is read from the
app's own OpenAPI document, so a tool always matches the endpoint it calls.

Adding a tool is one line in ``TOOLS``. Reads run at once; writes become a confirm card; downloads
show a button. Nothing under /auth, /public or /admin is ever a tool.
"""

from dataclasses import dataclass
from typing import Literal

Kind = Literal["read", "write", "download"]
MAX_REF_DEPTH = 8


@dataclass(frozen=True)
class Tool:
    name: str
    kind: Kind
    method: str
    path: str  # under the API prefix, e.g. "/projects/{project_id}"
    description: str
    # Write tools: the confirm card's sentence. {name} fills from the arguments (path, query or body).
    summary: str = ""
    # Upload endpoints: the multipart field the attached file goes into.
    file_field: str = ""


def _r(name: str, path: str, description: str) -> Tool:
    return Tool(name, "read", "GET", path, description)


def _w(name: str, method: str, path: str, description: str, summary: str, file_field: str = "") -> Tool:
    return Tool(name, "write", method, path, description, summary, file_field)


def _d(name: str, path: str, description: str) -> Tool:
    return Tool(name, "download", "GET", path, description)


TOOLS: tuple[Tool, ...] = (
    # Lookups
    _r("dashboard_summary", "/dashboard/summary", "Headline numbers: receivable, payable, pending approvals, GST to pay."),
    _r("list_projects", "/projects", "List projects."),
    _r("get_project", "/projects/{project_id}", "One project with its services, vendors, UPS codes, billing and margins."),
    _r("list_clients", "/clients", "List clients."),
    _r("get_client", "/clients/{client_id}", "One client with its details."),
    _r("list_vendors", "/vendors", "List vendors with their services and bank accounts."),
    _r("get_vendor", "/vendors/{vendor_id}", "One vendor."),
    _r("vendor_payables", "/vendors/{vendor_id}/payables", "A vendor's bills and what is still due on each."),
    _r("vendor_assignments", "/vendors/{vendor_id}/assignments", "A vendor's jobs (UPS codes) with agreed cost, invoiced and paid."),
    _r("list_vendor_invoices", "/vendors/invoices/all", "All vendor bills."),
    _r("list_services", "/services", "The service catalogue: ids, titles, SAC codes."),
    _r("list_invoices", "/invoices", "List proforma and tax invoices."),
    _r("get_invoice", "/invoices/{invoice_id}", "One invoice with its lines and status."),
    _r("invoice_payments", "/invoices/{invoice_id}/payments", "Payments recorded against an invoice."),
    _r("invoice_timeline", "/invoices/{invoice_id}/timeline", "An invoice's history."),
    _r("list_approvals", "/approvals", "Payment requests and their status."),
    _r("get_approval", "/approvals/{approval_id}", "One payment request."),
    _r("lookup_gstin", "/gst/{gstin}", "Check a GSTIN: registered name, PAN and state."),
    # Projects
    _w("create_project", "POST", "/projects", "Create a project.", "Create project {title}"),
    _w("update_project", "PATCH", "/projects/{project_id}",
       "Edit a project. Sending vendors replaces all its vendor assignments.", "Update project {project_id}"),
    _w("delete_project", "DELETE", "/projects/{project_id}", "Delete a project.", "Delete project {project_id}"),
    _w("send_work_order", "POST", "/projects/{project_id}/vendors/{vendor_id}/work-order/send",
       "Email a vendor its work order.", "Email the work order for {project_id} to {vendor_id}"),
    # Clients
    _w("create_client", "POST", "/clients", "Create a client.", "Create client {business_name}"),
    _w("update_client", "PATCH", "/clients/{client_id}", "Edit a client.", "Update client {client_id}"),
    _w("delete_client", "DELETE", "/clients/{client_id}", "Delete a client.", "Delete client {client_id}"),
    # Vendors
    _w("create_vendor", "POST", "/vendors", "Onboard a vendor.", "Onboard vendor {business_name}"),
    _w("update_vendor", "PATCH", "/vendors/{vendor_id}", "Edit a vendor.", "Update vendor {vendor_id}"),
    _w("verify_vendor", "POST", "/vendors/{vendor_id}/verify", "Mark a vendor verified.", "Verify vendor {vendor_id}"),
    _w("set_vendor_services", "PUT", "/vendors/{vendor_id}/services",
       "Set the services a vendor provides.", "Set the services {vendor_id} provides"),
    _w("create_vendor_invoice", "POST", "/vendors/invoices",
       "Record a vendor's bill against its UPS codes.", "Record vendor bill {invoice_number}"),
    _w("attach_vendor_bill", "POST", "/vendors/invoices/{invoice_id}/document",
       "Attach the vendor's own bill file to a recorded vendor bill. Needs file_id.",
       "Attach the file to vendor bill {invoice_id}", file_field="file"),
    # Invoices
    _w("create_invoice", "POST", "/invoices", "Raise a proforma invoice.", "Raise a proforma for {client_id}"),
    _w("update_invoice", "PATCH", "/invoices/{invoice_id}", "Edit an invoice.", "Update invoice {invoice_id}"),
    _w("delete_invoice", "DELETE", "/invoices/{invoice_id}", "Delete an invoice.", "Delete invoice {invoice_id}"),
    _w("duplicate_invoice", "POST", "/invoices/{invoice_id}/duplicate",
       "Copy an invoice into a new proforma.", "Duplicate invoice {invoice_id}"),
    _w("send_invoice_to_client", "POST", "/invoices/{invoice_id}/send-to-client",
       "Approve a proforma and send it to the client.", "Send invoice {invoice_id} to the client"),
    _w("record_client_approval", "POST", "/invoices/{invoice_id}/client-approval",
       "Record that the client approved the proforma.", "Record the client's approval of {invoice_id}"),
    _w("issue_invoice", "POST", "/invoices/{invoice_id}/issue",
       "Issue the tax invoice from an approved proforma.", "Issue the tax invoice for {invoice_id}"),
    _w("return_invoice", "POST", "/invoices/{invoice_id}/return",
       "Send an invoice back for changes.", "Return invoice {invoice_id} for changes"),
    _w("record_invoice_payment", "POST", "/invoices/{invoice_id}/payments",
       "Record a client payment against one invoice.", "Record a payment on invoice {invoice_id}"),
    _w("record_receipt", "POST", "/invoices/receipts",
       "Record one client receipt across several invoices.", "Record a client receipt"),
    # Payments
    _w("create_payment_request", "POST", "/approvals",
       "Request a payment to a payee that is not a vendor bill, such as rent.", "Request a payment to {payee_name}"),
    _w("request_vendor_payout", "POST", "/approvals/vendor-payout",
       "Request a payout to a vendor against its bills.", "Request a payout to {vendor_id}"),
    _w("submit_approval", "POST", "/approvals/{approval_id}/submit",
       "Submit a payment request for CEO approval.", "Submit payment request {approval_id} for approval"),
    _w("ceo_decide_approval", "POST", "/approvals/{approval_id}/ceo",
       "Approve or reject a payment request as CEO.", "CEO decision on payment request {approval_id}"),
    _w("release_payment", "POST", "/approvals/{approval_id}/release",
       "Release an approved payment with its bank reference (UTR).", "Release payment {approval_id}"),
    # Documents
    _d("invoice_document", "/invoices/{invoice_id}/document", "Download an invoice PDF."),
    _d("work_order_pdf", "/projects/{project_id}/vendors/{vendor_id}/work-order.pdf", "Download a vendor's work order PDF."),
    _d("vendor_bill_file", "/vendors/invoices/{invoice_id}/document", "Download a vendor's bill file."),
    # ---- Phase 2 ----
    # More lookups
    _r("dashboard_revenue", "/dashboard/revenue", "Revenue over time."),
    _r("list_vendor_allocations", "/vendors/allocations/all", "Vendor allocations."),
    _r("taxation_summary", "/taxation/summary", "GST and TDS position for a period."),
    _r("gst_sales_register", "/taxation/gst/sales", "GST on sales (output tax) register."),
    _r("gst_purchases_register", "/taxation/gst/purchases", "GST on purchases (input credit) register."),
    _r("tds_clients_register", "/taxation/tds/clients", "TDS that clients deducted from our receipts."),
    _r("tds_vendors_register", "/taxation/tds/vendors", "TDS we deducted from vendor bills."),
    _r("taxation_receipts", "/taxation/receipts", "Client receipts, for taxation."),
    _r("gst_calculator", "/taxation/gst/preview", "Work out GST on an amount."),
    _r("tds_calculator", "/taxation/tds/preview", "Work out TDS on an amount."),
    _r("list_reports", "/reports", "Vendor reports and their review status."),
    _r("list_bank_statements", "/verification/statements", "Uploaded bank statements."),
    _r("get_bank_statement", "/verification/statements/{statement_id}", "One bank statement with its transactions and matches."),
    _r("list_banks", "/banks", "Optiminastic's own bank accounts."),
    _r("list_agents", "/agents", "Sales agents."),
    _r("get_agent", "/agents/{agent_id}", "One sales agent."),
    _r("agent_options", "/agents/options", "Agents to choose from when creating a client (ids and names)."),
    _r("list_team_members", "/team/members", "People with an O2 account and their roles."),
    _r("list_invitations", "/team/invitations", "Team invitations and their status."),
    _r("audit_trail", "/audit", "The audit trail: who did what, and when."),
    Tool("preview_bulk_invoices", "read", "POST", "/invoices/bulk/preview",
         "Check an attached invoice sheet row by row without creating anything. Needs file_id.", file_field="file"),
    Tool("preview_bulk_services", "read", "POST", "/services/bulk/preview",
         "Check an attached service sheet row by row without creating anything. Needs file_id.", file_field="file"),
    # Projects: one vendor at a time
    _w("set_project_vendor", "PUT", "/projects/{project_id}/vendors/{vendor_id}",
       "Add or change one vendor on a project with its services and costs.", "Set {vendor_id} on project {project_id}"),
    _w("remove_project_vendor", "DELETE", "/projects/{project_id}/vendors/{vendor_id}",
       "Take a vendor off a project.", "Remove {vendor_id} from project {project_id}"),
    _w("create_vendor_allocation", "POST", "/vendors/allocations", "Record a vendor allocation.",
       "Record a vendor allocation for {vendor_id}"),
    # Bulk uploads
    _w("import_bulk_invoices", "POST", "/invoices/bulk",
       "Create proformas from an attached sheet (all rows or none). Preview it first. Needs file_id.",
       "Create proformas from the attached sheet", file_field="file"),
    _w("import_bulk_services", "POST", "/services/bulk",
       "Add services from an attached sheet. Preview it first. Needs file_id.",
       "Add services from the attached sheet", file_field="file"),
    # Services
    _w("create_service", "POST", "/services", "Add a service to the catalogue.", "Add service {title}"),
    _w("update_service", "PATCH", "/services/{service_id}", "Edit a service.", "Update service {service_id}"),
    _w("delete_service", "DELETE", "/services/{service_id}", "Delete or deactivate a service.", "Delete service {service_id}"),
    # Taxation
    _w("record_client_tds_certificate", "PATCH", "/taxation/client-tds/{payment_id}/certificate",
       "Record the TDS certificate a client gave for a payment.", "Record the TDS certificate for payment #{payment_id}"),
    _w("record_vendor_tds_deposit", "PATCH", "/taxation/vendor-tds/{vendor_invoice_id}/deposit",
       "Record that the TDS on a vendor bill was deposited.", "Record the TDS deposit for vendor bill {vendor_invoice_id}"),
    # Reports
    _w("create_report", "POST", "/reports", "Record a vendor report.", "Record report {project_name}"),
    _w("review_report", "POST", "/reports/{report_id}/review", "Approve or reject a vendor report.", "Review report #{report_id}"),
    _w("email_report", "POST", "/reports/{report_id}/email", "Email a report.", "Email report #{report_id}"),
    # Bank statements
    _w("upload_bank_statement", "POST", "/verification/statements",
       "Upload an attached bank statement to match against invoices. Needs file_id.",
       "Upload the attached bank statement", file_field="file"),
    _w("match_transaction", "POST", "/verification/transactions/{txn_id}/match",
       "Match a bank transaction to a record.", "Match bank transaction #{txn_id}"),
    _w("reconcile_transaction", "POST", "/verification/transactions/{txn_id}/reconcile",
       "Mark a bank transaction reconciled.", "Reconcile bank transaction #{txn_id}"),
    # Banks
    _w("create_bank", "POST", "/banks", "Add an Optiminastic bank account.", "Add bank account {name}"),
    _w("update_bank", "PATCH", "/banks/{bank_id}", "Edit an Optiminastic bank account.", "Update bank account {bank_id}"),
    _w("delete_bank", "DELETE", "/banks/{bank_id}", "Remove an Optiminastic bank account.", "Remove bank account {bank_id}"),
    # Agents
    _w("create_agent", "POST", "/agents", "Add a sales agent.", "Add agent {business_name}"),
    _w("update_agent", "PATCH", "/agents/{agent_id}", "Edit a sales agent.", "Update agent {agent_id}"),
    _w("delete_agent", "DELETE", "/agents/{agent_id}", "Remove a sales agent.", "Remove agent {agent_id}"),
    # Team
    _w("invite_team_member", "POST", "/team/invitations", "Invite someone to O2 with a role.", "Invite {email} to O2"),
    _w("resend_invitation", "POST", "/team/invitations/{invite_id}/resend", "Send an invitation again.",
       "Resend invitation #{invite_id}"),
    _w("revoke_invitation", "POST", "/team/invitations/{invite_id}/revoke", "Cancel an invitation.",
       "Revoke invitation #{invite_id}"),
    # More documents
    _d("bank_statement_file", "/verification/statements/{statement_id}/download", "Download a bank statement file."),
    _d("agent_mou_pdf", "/agents/{agent_id}/mou.pdf", "Download a sales agent's MOU."),
    _d("invoice_bulk_template", "/invoices/bulk/template", "Download the bulk invoice sheet template."),
    _d("service_bulk_template", "/services/bulk/template", "Download the bulk service sheet template."),
)

_BY_NAME = {t.name: t for t in TOOLS}


def tool_by_name(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def path_params(tool: Tool) -> set[str]:
    return {part[1:-1] for part in tool.path.split("/") if part.startswith("{")}


def _resolve(schema, components: dict, refs: int = 0):
    """Inline $refs and drop title annotations so the model sees one plain schema.

    ``refs`` counts followed references only, which stops a self-referencing model from looping;
    ordinary nesting is kept in full. A property that happens to be called "title" is kept: only a
    string-valued "title" is an annotation.
    """
    if isinstance(schema, list):
        return [_resolve(s, components, refs) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        if refs >= MAX_REF_DEPTH:
            return {"type": "object"}
        return _resolve(components.get(schema["$ref"].rsplit("/", 1)[-1], {}), components, refs + 1)
    return {
        k: _resolve(v, components, refs)
        for k, v in schema.items()
        if not (k == "title" and isinstance(v, str))
    }


def _parameters(tool: Tool, openapi: dict) -> dict:
    op = openapi["paths"]["/api" + tool.path][tool.method.lower()]
    components = openapi.get("components", {}).get("schemas", {})
    properties: dict = {}
    required: list[str] = []
    for param in op.get("parameters", []):
        if param.get("in") not in ("path", "query"):
            continue
        properties[param["name"]] = _resolve(param.get("schema", {}), components)
        if param.get("required"):
            required.append(param["name"])
    content = op.get("requestBody", {}).get("content", {})
    if tool.file_field:
        properties["file_id"] = {"type": "integer", "description": "Id of a file the user attached to this chat."}
        required.append("file_id")
        properties.update(_form_properties(op, components))
    elif "application/json" in content:
        properties["body"] = _resolve(content["application/json"]["schema"], components)
        if op["requestBody"].get("required"):
            required.append("body")
    return {"type": "object", "properties": properties, "required": required}


def _form_properties(op: dict, components: dict) -> dict:
    """The plain fields sent alongside an uploaded file: everything in the form except the file."""
    form = op.get("requestBody", {}).get("content", {}).get("multipart/form-data", {})
    schema = _resolve(form.get("schema", {}), components)
    return {
        k: v for k, v in schema.get("properties", {}).items()
        if v.get("format") != "binary" and "contentMediaType" not in v
    }


def form_fields(tool: Tool, openapi: dict) -> set[str]:
    """Names of the plain form fields an upload tool sends with its file."""
    if not tool.file_field:
        return set()
    op = openapi["paths"]["/api" + tool.path][tool.method.lower()]
    return set(_form_properties(op, openapi.get("components", {}).get("schemas", {})))


def tool_schemas(openapi: dict) -> list[dict]:
    """The tools as the chat API takes them."""
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": _parameters(t, openapi)}}
        for t in TOOLS
    ]


def split_arguments(tool: Tool, args: dict) -> tuple[dict, dict, dict | list | None, int | None]:
    """Path params, query params, JSON body and file id, from one tool call's arguments."""
    names = path_params(tool)
    path = {k: args[k] for k in names if k in args}
    body = args.get("body") if isinstance(args.get("body"), (dict, list)) else None
    file_id = args.get("file_id") if tool.file_field else None
    query = {k: v for k, v in args.items() if k not in names and k not in ("body", "file_id") and v is not None}
    return path, query, body, file_id
