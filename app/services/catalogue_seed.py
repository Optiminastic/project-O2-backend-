"""Optiminastic's standard service list, put into the catalogue once.

The marker in ``data_upgrades`` makes this a one-time step: a service the team
later renames or deletes is never brought back. A CEO data erase clears the
marker too, so the list returns on the next start.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DataUpgrade, Service

MARKER = "standard-service-catalogue"
STANDARD_GST_RATE = 18.0

# (title, SAC code), as Optiminastic bills them.
STANDARD_SERVICES: tuple[tuple[str, str], ...] = (
    ("Integrated Marketing & Technology Services for [Month/Period]", "998361"),
    ("Digital Marketing & Advertising Services", "998361"),
    ("Website / Application Development Services", "998314"),
    ("Information Technology Consulting & Support Services", "998313"),
    ("Creative Design & Branding Services", "998391"),
    ("Data Analytics & Reporting Services", "998313"),
    ("Video Production & Content Creation Services", "999613"),
    ("Technology & Development Support", "998314"),
    ("Consulting & Strategy Services", "998313"),
    ("Management Consulting and Management Services", "998311"),
)


def seed_standard_services(db: Session) -> int:
    """Add the standard services not yet in the catalogue, once. Returns how many were added."""
    if db.get(DataUpgrade, MARKER):
        return 0
    existing = {t for (t,) in db.query(func.lower(Service.title)).all()}
    added = 0
    for title, sac_code in STANDARD_SERVICES:
        if title.lower() in existing:
            continue
        db.add(Service(title=title, sac_code=sac_code, gst_rate=STANDARD_GST_RATE))
        added += 1
    db.add(DataUpgrade(name=MARKER))
    return added
