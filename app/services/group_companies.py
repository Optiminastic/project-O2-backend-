"""Group companies: vendors that are the firm's own sister companies.

Only the CFO and the CEO see or manage them. For everyone else they are left
out of vendor lists, their bills and payments are hidden, and on a project they
show as a restricted row with the cost but no name. The statutory tax
registers stay complete, because GST and TDS returns must include every bill.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Query

from app.models import User, UserRole, Vendor

GROUP_ROLES = frozenset({UserRole.ADMIN_CEO, UserRole.CFO})
RESTRICTED_NAME = "Group company (restricted)"


def sees_group_companies(user: User) -> bool:
    return user.role in GROUP_ROLES


def visible_vendors(q: Query, user: User) -> Query:
    """Filter a query over ``Vendor`` to the vendors this user may see."""
    return q if sees_group_companies(user) else q.filter(Vendor.is_group_company.is_(False))


def assert_visible(vendor: Vendor | None, user: User) -> Vendor:
    """The vendor, or 404 when it does not exist or is a group company this user may not see."""
    if vendor is None or (vendor.is_group_company and not sees_group_companies(user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
    return vendor


def assert_may_manage(user: User, is_group_company: bool) -> None:
    """Only the CFO and CEO create, edit or mark a group company."""
    if is_group_company and not sees_group_companies(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the CFO or CEO can manage group company vendors")


def masked_name(vendor: Vendor, user: User) -> str:
    return RESTRICTED_NAME if vendor.is_group_company and not sees_group_companies(user) else vendor.business_name
