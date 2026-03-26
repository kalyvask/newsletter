"""Email newsletter module for AI Deployment Research Monitor."""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

import markdown

from .config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    REPORT_EMAIL_TO,
    REPORT_EMAIL_FROM,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML Email Template
# ---------------------------------------------------------------------------

EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{subject}</title>
<style>
  body {{
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background-color: #f4f4f7;
    color: #1a1a2e;
    line-height: 1.6;
  }}
  .wrapper {{
    max-width: 680px;
    margin: 0 auto;
    background: #ffffff;
  }}
  .header {{
    background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    color: #ffffff;
    padding: 32px 40px;
  }}
  .header h1 {{
    margin: 0 0 6px 0;
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.3px;
  }}
  .header .subtitle {{
    font-size: 14px;
    color: #a5b4fc;
    margin: 0;
  }}
  .content {{
    padding: 32px 40px;
  }}
  .content h2 {{
    color: #302b63;
    font-size: 20px;
    margin: 28px 0 12px 0;
    padding-bottom: 6px;
    border-bottom: 2px solid #e8e8f0;
  }}
  .content h3 {{
    color: #4a4580;
    font-size: 16px;
    margin: 20px 0 8px 0;
  }}
  .content p {{
    margin: 0 0 14px 0;
    font-size: 15px;
  }}
  .content ul, .content ol {{
    padding-left: 24px;
    margin: 0 0 14px 0;
  }}
  .content li {{
    margin-bottom: 6px;
    font-size: 15px;
  }}
  .content a {{
    color: #4f46e5;
    text-decoration: none;
  }}
  .content a:hover {{
    text-decoration: underline;
  }}
  .content strong {{
    color: #1a1a2e;
  }}
  .content blockquote {{
    margin: 12px 0;
    padding: 10px 16px;
    border-left: 3px solid #a5b4fc;
    background: #f8f7ff;
    font-style: italic;
    color: #555;
  }}
  .content table {{
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 14px;
  }}
  .content th {{
    background: #f0eef8;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 2px solid #d0cde8;
  }}
  .content td {{
    padding: 8px 12px;
    border-bottom: 1px solid #eee;
  }}
  .content hr {{
    border: none;
    border-top: 1px solid #e8e8f0;
    margin: 24px 0;
  }}
  .content code {{
    background: #f0eef8;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 13px;
  }}
  .footer {{
    background: #f8f7ff;
    padding: 24px 40px;
    text-align: center;
    font-size: 12px;
    color: #888;
    border-top: 1px solid #e8e8f0;
  }}
  .footer a {{
    color: #4f46e5;
    text-decoration: none;
  }}
  @media (max-width: 600px) {{
    .header, .content, .footer {{
      padding-left: 20px;
      padding-right: 20px;
    }}
    .header h1 {{ font-size: 20px; }}
  }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>AI Deployment Intelligence</h1>
    <p class="subtitle">{subtitle}</p>
  </div>
  <div class="content">
    {body}
  </div>
  <div class="footer">
    <p>You're receiving this because you subscribed to AI Deployment Monitor newsletters.</p>
    <p>Powered by <a href="https://github.com/kalyvask/newsletter">AI Deployment Monitor</a></p>
  </div>
</div>
</body>
</html>
"""


def markdown_to_html(md_content: str) -> str:
    """Convert a Markdown report to styled HTML suitable for email."""
    # Strip the top-level title (it goes in the header instead)
    lines = md_content.split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    md_body = "\n".join(lines)

    html_body = markdown.markdown(
        md_body,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    return html_body


def build_newsletter_email(
    report_content: str,
    report_type: str = "weekly",
) -> tuple[str, str, str]:
    """Build a newsletter email from a Markdown report.

    Returns (subject, plain_text, html) tuple.
    """
    today = datetime.utcnow().strftime("%B %d, %Y")
    type_label = report_type.replace("_", " ").title()

    subject = f"AI Deployment Intelligence — {type_label} | {today}"
    subtitle = f"{type_label} Briefing — {today}"

    html_body = markdown_to_html(report_content)
    html = EMAIL_TEMPLATE.format(
        subject=subject,
        subtitle=subtitle,
        body=html_body,
    )

    return subject, report_content, html


def send_newsletter(
    report_content: str,
    report_type: str = "weekly",
    recipient: Optional[str] = None,
) -> bool:
    """Send a newsletter email with the given report content.

    Args:
        report_content: Markdown report text.
        report_type: Type of report (weekly, daily, executive).
        recipient: Override recipient email. Falls back to REPORT_EMAIL_TO.

    Returns:
        True if the email was sent successfully.
    """
    to_addr = recipient or REPORT_EMAIL_TO
    if not to_addr:
        logger.error("No recipient email configured. Set REPORT_EMAIL_TO in .env")
        return False

    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP credentials not configured. Set SMTP_USER and SMTP_PASSWORD in .env")
        return False

    subject, plain_text, html = build_newsletter_email(report_content, report_type)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = REPORT_EMAIL_FROM or SMTP_USER
    msg["To"] = to_addr

    # Attach plain text and HTML versions
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()

        if int(SMTP_PORT) == 465:
            # SSL connection
            with smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), context=context) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, to_addr.split(","), msg.as_string())
        else:
            # STARTTLS connection (port 587 or others)
            with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT)) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, to_addr.split(","), msg.as_string())

        logger.info(f"Newsletter sent to {to_addr}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed. For Gmail, use an App Password: "
            "https://myaccount.google.com/apppasswords"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
