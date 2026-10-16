"""The printed proforma / tax invoice: supplier details, place of supply and amount in words."""

from app.config import settings
from app.models import ClientInvoice
from app.schemas.invoice import InvoiceDocumentView, PublicClient, PublicCompany, PublicInvoice
from app.services.identifiers import GSTIN_STATE_CODES

_ONES = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen"
).split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()
# Indian grouping: crore, lakh, thousand, then hundreds.
_GROUPS = ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"), (100, "Hundred"))


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _words(n: int) -> str:
    """A whole number in words, Indian style (1,18,000 -> One Lakh Eighteen Thousand)."""
    if n < 100:
        return _below_hundred(n)
    parts = []
    for size, name in _GROUPS:
        count, n = divmod(n, size)
        if count:
            parts.append(f"{_words(count)} {name}")
    if n:
        parts.append(_below_hundred(n))
    return " ".join(parts)


def rupees_in_words(amount: float) -> str:
    """'One Lakh Eighteen Thousand Only', with paise when present."""
    paise_total = round(amount * 100)
    rupees, paise = divmod(paise_total, 100)
    text = _words(rupees)
    if paise:
        text += f" and {_words(paise)} Paise"
    return f"{text} Only"


def company() -> PublicCompany:
    return PublicCompany(
        legal_name=settings.company_legal_name,
        address=settings.company_address,
        gstin=settings.company_gstin,
        state=settings.company_state,
        pan=settings.company_pan,
        email=settings.company_email,
        bank_name=settings.company_bank_name,
        bank_account=settings.company_bank_account,
        bank_ifsc=settings.company_bank_ifsc,
        bank_branch=settings.company_bank_branch,
    )


def place_of_supply(invoice: ClientInvoice) -> str | None:
    """The client's state from its GSTIN; an intra-state supply is in our own state."""
    gstin = invoice.client.gst_number
    if gstin and gstin[:2] in GSTIN_STATE_CODES:
        return GSTIN_STATE_CODES[gstin[:2]]
    if not invoice.is_interstate:
        return settings.company_state or None
    return None


def document(invoice: ClientInvoice) -> InvoiceDocumentView:
    return InvoiceDocumentView(
        invoice=PublicInvoice.model_validate(invoice),
        client=PublicClient.model_validate(invoice.client),
        company=company(),
        place_of_supply=place_of_supply(invoice),
        amount_in_words=rupees_in_words(invoice.total_amount),
    )
