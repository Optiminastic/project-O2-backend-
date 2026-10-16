"""Optiminastic's standard services are added to the catalogue once, with their SAC codes."""

from app.models import Service
from app.services.catalogue_seed import STANDARD_SERVICES, seed_standard_services
from tests.conftest import DEFAULT_SERVICE_TITLE


def titles(db) -> set[str]:
    return {s.title for s in db.query(Service).all()}


def test_standard_services_are_added_with_their_sac_codes(db):
    assert seed_standard_services(db) == len(STANDARD_SERVICES)
    db.commit()
    by_title = {s.title: s for s in db.query(Service).all()}
    media = by_title["Digital Marketing & Advertising Services"]
    assert (media.sac_code, media.gst_rate, media.is_active) == ("998361", 18.0, True)
    assert by_title["Video Production & Content Creation Services"].sac_code == "999613"
    assert DEFAULT_SERVICE_TITLE in by_title  # what was already there stays


def test_it_runs_once_so_a_deleted_service_stays_deleted(db):
    seed_standard_services(db)
    db.commit()
    db.query(Service).filter(Service.title == "Consulting & Strategy Services").delete()
    db.commit()
    assert seed_standard_services(db) == 0
    db.commit()
    assert "Consulting & Strategy Services" not in titles(db)


def test_a_service_already_in_the_catalogue_is_not_added_twice(db):
    db.add(Service(title="consulting & strategy services", sac_code="998313", gst_rate=18))
    db.commit()
    assert seed_standard_services(db) == len(STANDARD_SERVICES) - 1
    db.commit()
