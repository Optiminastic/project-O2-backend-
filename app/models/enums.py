from enum import Enum


class UserRole(str, Enum):
    ADMIN_CEO = "ADMIN_CEO"
    CFO = "CFO"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    FINANCE_EXECUTIVE = "FINANCE_EXECUTIVE"


class InvitationStatus(str, Enum):
    PENDING = "Pending"
    ACCEPTED = "Accepted"
    REVOKED = "Revoked"


class InvoiceStatus(str, Enum):
    # Proforma stages. DRAFT is the editable proforma; no GST liability exists yet.
    DRAFT = "Draft"
    AWAITING_CLIENT = "Awaiting Client"
    CHANGES_REQUESTED = "Changes Requested"
    CLIENT_APPROVED = "Client Approved"
    # Issued tax invoice stages. SENT is the status at the moment of issue.
    SENT = "Sent"
    PENDING = "Pending"
    PARTIALLY_PAID = "Partially Paid"
    FULLY_PAID = "Fully Paid"
    OVERDUE = "Overdue"
    CANCELLED = "Cancelled"
    DISPUTED = "Disputed"
    RECONCILED = "Reconciled"


PROFORMA_STAGES = frozenset(
    {
        InvoiceStatus.DRAFT,
        InvoiceStatus.AWAITING_CLIENT,
        InvoiceStatus.CHANGES_REQUESTED,
        InvoiceStatus.CLIENT_APPROVED,
    }
)


class InvoiceLinkPurpose(str, Enum):
    APPROVE = "approve"  # client reviews a proforma and approves or asks for changes
    VIEW = "view"  # client views an issued tax invoice


class ClientApprovalMethod(str, Enum):
    LINK = "link"
    MANUAL = "manual"


class PaymentMode(str, Enum):
    # Client payments are always received by bank. The other modes remain only
    # so payments recorded before this rule still load.
    BANK = "Bank"
    NEFT = "NEFT"
    RTGS = "RTGS"
    IMPS = "IMPS"
    UPI = "UPI"
    CHEQUE = "Cheque"
    CASH = "Cash"
    OTHER = "Other"


class GstStatus(str, Enum):
    PENDING_COLLECTION = "GST Pending Collection"
    COLLECTED = "GST Collected"
    PAYMENT_PENDING = "GST Payment Pending"
    PAID = "GST Paid"
    RECONCILED = "GST Reconciled"


class VendorApprovalStatus(str, Enum):
    PENDING = "Pending"
    UNDER_REVIEW = "Under Review"
    VERIFIED = "Verified"
    REJECTED = "Rejected"


class AllocationStatus(str, Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    REPORT_DUE = "Report Due"
    COMPLETED = "Completed"
    ON_HOLD = "On Hold"


class ReportReviewStatus(str, Enum):
    SUBMITTED = "Submitted"
    UNDER_REVIEW = "Under Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SENT_TO_CLIENT = "Sent to Client"


class ApprovalStatus(str, Enum):
    DRAFT = "Draft"
    SUBMITTED_CFO = "Submitted for CFO Approval"
    CFO_APPROVED = "CFO Approved"
    CFO_REJECTED = "CFO Rejected"
    SUBMITTED_CEO = "Submitted for CEO Approval"
    CEO_APPROVED = "CEO Approved"
    CEO_REJECTED = "CEO Rejected"
    PAYMENT_READY = "Payment Ready"
    PAYMENT_RELEASED = "Payment Released"
    PAYMENT_VERIFIED = "Payment Verified"


class VerificationStatus(str, Enum):
    NOT_VERIFIED = "Not Verified"
    STATEMENT_UPLOADED = "Statement Uploaded"
    AUTO_MATCHED = "Auto-Matched"
    MANUALLY_MATCHED = "Manually Matched"
    MISMATCH = "Mismatch Found"
    RECONCILED = "Reconciled"
