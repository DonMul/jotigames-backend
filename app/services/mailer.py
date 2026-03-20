import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader

from app.config import get_settings
from app.services.i18n import translate_value


class MailerConfigurationError(RuntimeError):
    pass


_templates_root = Path(__file__).resolve().parents[1] / "templates" / "emails"
_template_env = Environment(loader=FileSystemLoader(str(_templates_root)), autoescape=True)


def _render_email_template(template_name: str, *, locale: Optional[str], username: str, action_url: str) -> str:
    title_key = "mailer.verify.title" if "verify" in template_name else "mailer.reset.title"
    greeting_key = "mailer.verify.greeting" if "verify" in template_name else "mailer.reset.greeting"
    intro_key = "mailer.verify.intro" if "verify" in template_name else "mailer.reset.intro"
    cta_key = "mailer.verify.cta" if "verify" in template_name else "mailer.reset.cta"
    outro_key = "mailer.verify.outro" if "verify" in template_name else "mailer.reset.outro"

    context = {
        "title": translate_value(title_key, locale),
        "greeting": translate_value(greeting_key, locale, {"%username%": username}),
        "intro": translate_value(intro_key, locale),
        "cta": translate_value(cta_key, locale),
        "outro": translate_value(outro_key, locale),
        "action_url": action_url,
    }

    template = _template_env.get_template(template_name)
    return template.render(**context)


def _smtp_client_from_dsn():
    settings = get_settings()
    if not settings.mailer_dsn:
        raise MailerConfigurationError("auth.register.mailerNotConfigured")

    parsed = urlparse(settings.mailer_dsn)
    host = parsed.hostname
    port = parsed.port
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    scheme = parsed.scheme.lower()

    if not host or not scheme:
        raise MailerConfigurationError("auth.register.mailerNotConfigured")

    if scheme == "smtps":
        client = smtplib.SMTP_SSL(host=host, port=port or 465, timeout=10)
    elif scheme == "smtp":
        client = smtplib.SMTP(host=host, port=port or 587, timeout=10)
        client.starttls()
    else:
        raise MailerConfigurationError("auth.register.mailerUnsupportedScheme")

    if username:
        client.login(username, password)

    return client


def send_verification_email(*, to_email: str, username: str, verify_url: str, locale: Optional[str] = None) -> None:
    settings = get_settings()
    if not settings.mailer_from:
        raise MailerConfigurationError("auth.register.mailerFromMissing")

    subject = translate_value("mailer.verify.subject", locale)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.mailer_from
    message["To"] = to_email

    text_content = _render_email_template(
        "verify_email.txt.twig",
        locale=locale,
        username=username,
        action_url=verify_url,
    )
    html_content = _render_email_template(
        "verify_email.html.twig",
        locale=locale,
        username=username,
        action_url=verify_url,
    )

    message.set_content(text_content)
    message.add_alternative(html_content, subtype="html")

    with _smtp_client_from_dsn() as client:
        client.send_message(message)


def send_password_reset_email(*, to_email: str, username: str, reset_url: str, locale: Optional[str] = None) -> None:
    settings = get_settings()
    if not settings.mailer_from:
        raise MailerConfigurationError("auth.register.mailerFromMissing")

    subject = translate_value("mailer.reset.subject", locale)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.mailer_from
    message["To"] = to_email

    text_content = _render_email_template(
        "reset_password_email.txt.twig",
        locale=locale,
        username=username,
        action_url=reset_url,
    )
    html_content = _render_email_template(
        "reset_password_email.html.twig",
        locale=locale,
        username=username,
        action_url=reset_url,
    )

    message.set_content(text_content)
    message.add_alternative(html_content, subtype="html")

    with _smtp_client_from_dsn() as client:
        client.send_message(message)
