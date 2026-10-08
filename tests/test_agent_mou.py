"""Agent MOU: a downloadable PDF setting out the referral agent's commission terms."""

from app.config import settings
from app.models import Agent
from app.services.agents import house_agent
from tests.conftest import auth_header


def make_agent(db) -> Agent:
    agent = Agent(
        business_name="Techcores",
        legal_name="Techcores LLP",
        email="hello@techcores.example",
        pan="AAAFT1234K",
        commission_rate=5,
        bank_name="HDFC Bank",
        account_number="50100012345678",
        ifsc_code="HDFC0001234",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def test_manager_downloads_the_mou_as_a_pdf(client, users, db):
    agent = make_agent(db)
    res = client.get(f"/api/agents/{agent.id}/mou.pdf", headers=auth_header(users["manager"]))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert 'filename="MOU-Techcores.pdf"' in res.headers["content-disposition"]


def test_mou_text_carries_the_agreed_terms(db):
    from app.services.agent_mou import mou_paragraphs

    text = "\n".join(mou_paragraphs(make_agent(db)))
    assert "Techcores LLP" in text
    assert "5%" in text
    assert "taxable value" in text
    assert "194H" in text  # TDS on commission
    assert settings.company_legal_name in text


def test_executive_cannot_download_an_mou(client, users, db):
    agent = make_agent(db)
    assert client.get(f"/api/agents/{agent.id}/mou.pdf", headers=auth_header(users["exec"])).status_code == 403


def test_no_mou_for_the_in_house_agent(client, users, db):
    res = client.get(f"/api/agents/{house_agent(db).id}/mou.pdf", headers=auth_header(users["manager"]))
    assert res.status_code == 400


def test_mou_needs_company_details(client, users, db, monkeypatch):
    agent = make_agent(db)
    monkeypatch.setattr(settings, "company_legal_name", "")
    res = client.get(f"/api/agents/{agent.id}/mou.pdf", headers=auth_header(users["manager"]))
    assert res.status_code == 400
    assert "COMPANY_LEGAL_NAME" in res.json()["detail"]
