"""The confirm card for a proposed change. It is built from the real arguments, never from the model's
own wording, and every id is shown with the name it belongs to, so the user confirms exactly what runs."""

from sqlalchemy.orm import Session

from app.assistant.tools import Tool
from app.models import (
    Agent,
    Client,
    ClientInvoice,
    CompanyBank,
    PaymentApproval,
    Project,
    Service,
    Vendor,
    VendorInvoice,
)

# Argument names that are ids: the label on the card, the model, and the attribute that names it.
_NAMED_IDS = {
    "client_id": ("Client", Client, "business_name"),
    "vendor_id": ("Vendor", Vendor, "business_name"),
    "project_id": ("Project", Project, "title"),
    "approval_id": ("Payment request", PaymentApproval, "payee_name"),
    "service_id": ("Service", Service, "title"),
    "agent_id": ("Agent", Agent, "business_name"),
    "bank_id": ("Bank account", CompanyBank, "name"),
    "vendor_invoice_id": ("Vendor bill", VendorInvoice, "invoice_number"),
}
MAX_FIELDS = 12
MAX_VALUE = 120


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _value(value) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"


def _named(db: Session, tool: Tool, key: str, value) -> tuple[str, str, str] | None:
    """(card label, card value, plain name) for an id argument, or None for any other argument."""
    if key == "invoice_id":
        on_vendor_bill = tool.path.startswith("/vendors")
        label, model, attr = ("Vendor bill", VendorInvoice, "invoice_number") if on_vendor_bill else ("Invoice", ClientInvoice, "invoice_number")
    elif key in _NAMED_IDS:
        label, model, attr = _NAMED_IDS[key]
    else:
        return None
    row = db.get(model, value) if isinstance(value, int) and not isinstance(value, bool) else None
    if row is None:
        return label, f"#{value} (not found)", f"#{value}"
    name = getattr(row, attr, None) or f"#{value}"
    return label, f"{name} (#{value})", str(name)


def describe(tool: Tool, args: dict, db: Session) -> dict:
    """``{"summary": sentence, "fields": [{"label", "value"}]}`` for the confirm card."""
    flat = {k: v for k, v in args.items() if k != "body"}
    if isinstance(args.get("body"), dict):
        flat.update(args["body"])
    fields: list[dict] = []
    names: dict[str, str] = {}
    for key, value in flat.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        named = _named(db, tool, key, value)
        if named:
            fields.append({"label": named[0], "value": named[1]})
            names[key] = named[2]
        elif not isinstance(value, dict) and not (isinstance(value, list) and value and isinstance(value[0], dict)):
            fields.append({"label": _label(key), "value": _value(value)})
        elif isinstance(value, list):
            fields.append({"label": _label(key), "value": f"{len(value)} item{'' if len(value) == 1 else 's'}"})
    summary = tool.summary
    for key, value in flat.items():
        if "{" + key + "}" in summary:
            shown = names.get(key) or ("" if isinstance(value, (dict, list)) else str(value))
            summary = summary.replace("{" + key + "}", shown)
    return {"summary": summary, "fields": fields[:MAX_FIELDS]}
