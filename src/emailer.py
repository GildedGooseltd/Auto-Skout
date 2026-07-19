"""Send Skout / Auto Skout email alerts via Gmail SMTP."""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()


def email_configured() -> bool:
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))


def send_alert(
    subject: str,
    html_body: str,
    text_body: str,
    *,
    to: Optional[str] = None,
) -> None:
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    dest = to or os.environ.get("ALERT_EMAIL_TO", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = dest
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [dest], msg.as_string())


def send_truck_deal_alerts(
    deals: list[dict[str, Any]],
    *,
    to: Optional[str] = None,
    dashboard_url: str = "",
) -> int:
    """Email one digest for don't-pass-up truck deals. Returns count emailed."""
    if not deals:
        return 0
    if not email_configured():
        print("  email alert skipped — set GMAIL_USER + GMAIL_APP_PASSWORD in .env", flush=True)
        return 0

    n = len(deals)
    top = deals[0]
    subject = (
        f"Auto Skout: don't pass up — {top.get('title', 'truck deal')[:70]}"
        if n == 1
        else f"Auto Skout: {n} trucks you shouldn't pass up"
    )

    lines_txt = []
    cards_html = []
    for d in deals:
        title = d.get("title") or "Listing"
        price = d.get("price") or "?"
        loc = d.get("location") or ""
        url = d.get("url") or ""
        reason = d.get("reason") or ""
        fit = d.get("fit_score", "")
        lines_txt.append(
            f"• {title}\n  {price} · {loc}\n  Fit {fit} — {reason}\n  {url}\n"
        )
        cards_html.append(
            f"""
            <tr>
              <td style="padding:14px 0;border-bottom:1px solid #e7e5e4;">
                <div style="font-size:16px;font-weight:700;color:#1c1917;">
                  <a href="{escape(url)}" style="color:#166534;text-decoration:none;">{escape(title)}</a>
                </div>
                <div style="margin-top:4px;font-size:14px;color:#44403c;">
                  <strong>{escape(str(price))}</strong> · {escape(loc)}
                </div>
                <div style="margin-top:4px;font-size:13px;color:#78716c;">
                  Fit {escape(str(fit))} — {escape(reason)}
                </div>
              </td>
            </tr>"""
        )

    dash = (
        f'\nOpen dashboard: {dashboard_url}\n'
        if dashboard_url
        else ""
    )
    text_body = (
        "Auto Skout found truck(s) that look like a don't-pass-up deal:\n\n"
        + "\n".join(lines_txt)
        + dash
    )
    footer = (
        f'<p style="margin-top:18px;font-size:13px;"><a href="{escape(dashboard_url)}">'
        f"Open Auto Skout dashboard</a></p>"
        if dashboard_url
        else ""
    )
    html_body = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:16px;">
      <h1 style="font-size:18px;color:#14532d;margin:0 0 8px;">Don't pass this up</h1>
      <p style="margin:0 0 16px;color:#57534e;font-size:14px;">
        High-fit truck deal(s) within your Auto Skout rules (≤$20k · Chevy/GMC preferred · HD tow).
      </p>
      <table width="100%" cellpadding="0" cellspacing="0">{''.join(cards_html)}</table>
      {footer}
    </div>
    """
    send_alert(subject, html_body, text_body, to=to)
    return n
