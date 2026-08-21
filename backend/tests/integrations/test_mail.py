"""Daily report email rendering and SMTP transport tests."""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import Mock

import pytest
from app.integrations import mail
from app.integrations.mail import SmtpSettings, build_daily_message, deliver_message


def _smtp_settings(*, security: str = "starttls") -> SmtpSettings:
    return SmtpSettings(
        host="smtp.example.com",
        port=587,
        security=security,
        username="mailer",
        password="secret",
        sender="xivpulse@example.com",
        recipients=("reader@example.com",),
        public_url="https://papers.example.com",
    )


def test_daily_message_contains_links_and_escapes_html() -> None:
    message = build_daily_message(
        _smtp_settings(),
        {
            "report_date": "2026-08-21",
            "executive_summary": "Evidence <must> remain text.",
            "coverage": {
                "paper_count": 2,
                "analyzed_paper_count": 1,
                "deep_analysis_count": 1,
            },
            "highlight_papers": [
                {
                    "paper_id": 7,
                    "arxiv_id": "2608.00007",
                    "title": "<script>alert(1)</script>",
                    "score": 91,
                    "summary": "Useful & bounded.",
                }
            ],
        },
        "reader@example.com",
    )

    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert message["Subject"] == "2026-08-21 arXiv 当日研究总结"
    assert "view=digest" in plain
    assert "paper=7" in plain
    assert "摘要粗排 2 篇" in plain
    assert "<script>alert(1)</script>" in plain
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Evidence &lt;must&gt; remain text." in html


@pytest.mark.parametrize(
    ("security", "constructor_name", "uses_starttls"),
    [("starttls", "SMTP", True), ("ssl", "SMTP_SSL", False)],
)
def test_delivery_selects_the_configured_smtp_transport(
    monkeypatch: pytest.MonkeyPatch,
    security: str,
    constructor_name: str,
    uses_starttls: bool,
) -> None:
    context = object()
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    constructor = Mock(return_value=client)
    monkeypatch.setattr(mail.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(mail.smtplib, constructor_name, constructor)
    message = EmailMessage()
    message.set_content("test")

    deliver_message(_smtp_settings(security=security), message)

    if security == "ssl":
        constructor.assert_called_once_with("smtp.example.com", 587, timeout=30, context=context)
    else:
        constructor.assert_called_once_with("smtp.example.com", 587, timeout=30)
    assert client.starttls.call_count == int(uses_starttls)
    if uses_starttls:
        client.starttls.assert_called_once_with(context=context)
        assert client.ehlo.call_count == 2
    client.login.assert_called_once_with("mailer", "secret")
    client.send_message.assert_called_once_with(message)
