"""SMTP delivery and rendering for persisted daily research reports."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Any
from urllib.parse import urlencode

from ..settings import RuntimeSettings


@dataclass(frozen=True)
class SmtpSettings:
    # SMTP connection and authentication.
    host: str
    port: int
    security: str
    username: str
    password: str

    # Outbound email addresses and links.
    sender: str
    recipients: tuple[str, ...]
    public_url: str

    @property
    def ready(self) -> bool:
        return bool(self.host and self.sender and self.recipients)


def smtp_settings(runtime_settings: RuntimeSettings) -> SmtpSettings:
    return SmtpSettings(
        host=runtime_settings.smtp_host,
        port=runtime_settings.smtp_port,
        security=runtime_settings.smtp_security,
        username=runtime_settings.smtp_username,
        password=runtime_settings.smtp_password,
        sender=runtime_settings.smtp_sender,
        recipients=runtime_settings.email_recipients,
        public_url=runtime_settings.public_url,
    )


def email_enabled(settings: RuntimeSettings) -> bool:
    return settings.email_enabled


def _report_url(settings: SmtpSettings, report_date: str) -> str:
    if not settings.public_url:
        return ""
    return f"{settings.public_url}/?{urlencode({'view': 'digest', 'date': report_date})}"


def _paper_url(settings: SmtpSettings, paper_id: Any) -> str:
    if not settings.public_url:
        return ""
    query = urlencode({"view": "library", "paper": str(paper_id)})
    return f"{settings.public_url}/?{query}"


def build_daily_message(
    settings: SmtpSettings,
    report: Mapping[str, Any],
    recipient: str,
    *,
    incomplete: bool = False,
    failure_count: int = 0,
) -> EmailMessage:
    report_date = str(report.get("report_date") or "")
    prefix = "[初筛未完全完成] " if incomplete else ""
    subject = f"{prefix}{report_date} arXiv 当日研究总结"
    coverage = dict(report.get("coverage") or {})
    situation = dict(report.get("research_situation") or {})
    highlights = list(report.get("highlight_papers") or [])[:10]
    ai_synthesis = report.get("ai_synthesis")
    ai_synthesis = ai_synthesis if isinstance(ai_synthesis, Mapping) else {}
    synthesis = str(
        ai_synthesis.get("overall_assessment")
        if ai_synthesis.get("status") == "ready"
        else situation.get("synthesis") or report.get("executive_summary") or ""
    )
    if len(synthesis) > 1_600:
        synthesis = f"{synthesis[:1599]}…"
    report_url = _report_url(settings, report_date)
    coverage_text = (
        f"候选 {coverage.get('candidate_paper_count', 0)} 篇，"
        f"正式方向相关 {coverage.get('paper_count', 0)} 篇，"
        f"已分析 {coverage.get('analyzed_paper_count', 0)} 篇，"
        f"深读 {coverage.get('deep_analysis_count', 0)} 篇"
    )
    warning = (
        f"注意：仍有 {failure_count} 篇候选初筛失败，本报告不完整，缺失论文未按零相关处理。"
        if incomplete
        else ""
    )
    text_lines = [subject, "", warning, synthesis, "", coverage_text, "", "重点论文"]
    for index, paper in enumerate(highlights, start=1):
        url = _paper_url(settings, paper.get("paper_id"))
        text_lines.extend(
            [
                f"{index}. {paper.get('title', '未命名论文')}",
                f"   推荐分：{paper.get('score', 0)}  arXiv: {paper.get('arxiv_id', '')}",
                f"   {paper.get('summary') or paper.get('why_it_matters') or ''}",
                f"   {url}" if url else "",
            ]
        )
    if report_url:
        text_lines.extend(["", f"完整日报：{report_url}"])

    warning_html = (
        f'<div class="warning">初筛未完全完成：仍有 {failure_count} 篇失败。'
        "缺失论文未按零相关处理。</div>"
        if incomplete
        else ""
    )
    paper_html = "".join(
        (
            '<article class="paper">'
            f'<div class="rank">{index:02d}</div><div>'
            f"<h2>{escape(str(paper.get('title') or '未命名论文'))}</h2>"
            f'<p class="meta">推荐分 {escape(str(paper.get("score", 0)))} · '
            f"arXiv {escape(str(paper.get('arxiv_id') or ''))}</p>"
            f"<p>{escape(str(paper.get('summary') or paper.get('why_it_matters') or ''))}</p>"
            + (
                f'<a href="{escape(_paper_url(settings, paper.get("paper_id")))}">打开论文详情</a>'
                if _paper_url(settings, paper.get("paper_id"))
                else ""
            )
            + "</div></article>"
        )
        for index, paper in enumerate(highlights, start=1)
    )
    full_report_link = (
        f'<a class="report-link" href="{escape(report_url)}">查看完整研究日报</a>'
        if report_url
        else ""
    )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
body{{margin:0;background:#f4f6f8;color:#182026;
font:15px/1.65 system-ui,-apple-system,sans-serif}}
.wrap{{max-width:720px;margin:auto;background:#fff}}
header{{padding:28px 32px;background:#16324a;color:#fff}}
main{{padding:28px 32px}}h1{{font-size:24px;margin:0}}h2{{font-size:16px;margin:0 0 6px}}
.warning{{padding:12px 14px;background:#fff1e8;border-left:4px solid #c44d28;margin-bottom:18px}}
.coverage{{padding:12px 14px;background:#edf3f5;margin:20px 0}}
.paper{{display:grid;grid-template-columns:40px 1fr;gap:12px;padding:18px 0;
border-top:1px solid #dfe5e8}}
.rank{{color:#63737c;font-weight:700}}.meta{{color:#63737c;margin:0 0 7px}}p{{margin:7px 0}}
a{{color:#1261a0}}.report-link{{display:inline-block;margin-top:20px;font-weight:700}}
@media(max-width:560px){{header,main{{padding:22px 18px}}.paper{{grid-template-columns:32px 1fr}}}}
</style></head><body><div class="wrap"><header><h1>{escape(subject)}</h1></header><main>
{warning_html}<p>{escape(synthesis)}</p><div class="coverage">{escape(coverage_text)}</div>
<h2>重点论文</h2>{paper_html}{full_report_link}</main></div></body></html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = recipient
    message.set_content("\n".join(line for line in text_lines if line is not None), charset="utf-8")
    message.add_alternative(html, subtype="html", charset="utf-8")
    return message


def deliver_message(settings: SmtpSettings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.security == "ssl":
        client_context = smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=30, context=context
        )
    else:
        client_context = smtplib.SMTP(settings.host, settings.port, timeout=30)
    with client_context as client:
        if settings.security == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if settings.username:
            client.login(settings.username, settings.password)
        client.send_message(message)


__all__ = ["SmtpSettings", "build_daily_message", "deliver_message", "smtp_settings"]
