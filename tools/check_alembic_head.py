#!/usr/bin/env python3
"""Verify a target database is at the repository Alembic head."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import NamedTuple, Sequence

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"
DEFAULT_ALEMBIC_CONFIG = BACKEND_DIR / "alembic.ini"
VERSION_QUERY = text("SELECT version_num FROM core.alembic_version")


class HeadCheckError(RuntimeError):
    """Base error for Alembic head guard failures."""


class DatabaseVersionStateError(HeadCheckError):
    """Raised when the database version table cannot produce a single head."""


class HeadMismatchError(HeadCheckError):
    """Raised when the database revision differs from the repository head."""


class HeadCheckResult(NamedTuple):
    database_head: str
    repository_head: str
    target_label: str

    def success_message(self) -> str:
        return (
            f"IN SYNC: {self.target_label} is at Alembic head "
            f"{self.database_head} (repo head {self.repository_head})."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare core.alembic_version.version_num in a target database "
            "against the repository Alembic head."
        )
    )
    parser.add_argument(
        "--dsn",
        help="Target SQLAlchemy DSN. If omitted, resolves from --dsn-env.",
    )
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_URL",
        help="Environment variable that holds the target DSN. Default: DATABASE_URL.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help=f"Optional env file to load before resolving the DSN. Default: {DEFAULT_ENV_FILE}.",
    )
    parser.add_argument(
        "--alembic-config",
        default=str(DEFAULT_ALEMBIC_CONFIG),
        help=f"Path to alembic.ini. Default: {DEFAULT_ALEMBIC_CONFIG}.",
    )
    parser.add_argument(
        "--label",
        default="target database",
        help="Human-readable label used in status and alert messages.",
    )
    parser.add_argument(
        "--alert-email",
        help="Optional email recipient for divergence alerts.",
    )
    parser.add_argument(
        "--alert-email-env",
        default="ALEMBIC_ALERT_EMAIL",
        help=(
            "Environment variable for the alert recipient. "
            "Falls back to MIGADU_ADMIN_EMAIL when unset."
        ),
    )
    return parser


def load_environment(env_file: str) -> None:
    env_path = Path(env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def resolve_target_dsn(args: argparse.Namespace) -> str:
    dsn = args.dsn or os.getenv(args.dsn_env, "")
    if not dsn:
        raise HeadCheckError(
            f"Target DSN is required. Pass --dsn or set {args.dsn_env}."
        )
    return dsn


def resolve_alert_recipient(args: argparse.Namespace) -> str | None:
    return (
        args.alert_email
        or os.getenv(args.alert_email_env)
        or os.getenv("MIGADU_ADMIN_EMAIL")
    )


def mask_dsn(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, remainder = dsn.split("://", 1)
    if "@" not in remainder:
        return dsn
    _, host = remainder.rsplit("@", 1)
    return f"{scheme}://***@{host}"


def get_repository_head(alembic_config_path: str) -> str:
    config_path = Path(alembic_config_path).resolve()
    config = Config(str(config_path))
    script_location = config.get_main_option("script_location")
    if script_location and not Path(script_location).is_absolute():
        config.set_main_option(
            "script_location",
            str((config_path.parent / script_location).resolve()),
        )
    script_dir = ScriptDirectory.from_config(config)
    head = script_dir.get_current_head()
    if not head:
        raise HeadCheckError(
            f"No Alembic head found from config {alembic_config_path}."
        )
    return head


async def fetch_database_heads(dsn: str) -> list[str]:
    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(VERSION_QUERY)
            return [str(value) for value in result.scalars().all()]
    finally:
        await engine.dispose()


def evaluate_heads(
    database_heads: Sequence[str],
    repository_head: str,
    target_label: str,
) -> HeadCheckResult:
    if not database_heads:
        raise DatabaseVersionStateError(
            f"{target_label} has no rows in core.alembic_version."
        )
    if len(database_heads) != 1:
        joined = ", ".join(database_heads)
        raise DatabaseVersionStateError(
            f"{target_label} returned multiple Alembic heads ({joined}); expected exactly one."
        )

    database_head = database_heads[0]
    if database_head != repository_head:
        raise HeadMismatchError(
            f"OUT OF SYNC: {target_label} is at Alembic revision {database_head} "
            f"but the repository head is {repository_head}."
        )

    return HeadCheckResult(
        database_head=database_head,
        repository_head=repository_head,
        target_label=target_label,
    )


def _build_email_html(subject: str, body: str) -> str:
    safe_subject = subject.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return (
        "<html><body style=\"font-family:Arial,sans-serif;line-height:1.6;color:#111827\">"
        f"<h2 style=\"margin-bottom:12px\">{safe_subject}</h2>"
        f"<p>{safe_body}</p>"
        "</body></html>"
    )


def send_alert_email(recipient: str, subject: str, body: str) -> None:
    intercept = os.getenv("TEST_EMAIL_INTERCEPT_ADDRESS")
    if intercept:
        subject = f"[TEST→{recipient}] {subject}"
        recipient = intercept

    provider = (os.getenv("EMAIL_PROVIDER") or "").strip().lower()
    sender_email = os.getenv("SENDER_EMAIL", "noreply@eastgateresidences.com.au")
    sender_name = os.getenv("SENDER_NAME", "StrataOS")
    html_body = _build_email_html(subject, body)

    resend_api_key = os.getenv("RESEND_API_KEY")
    if provider == "resend" and resend_api_key:
        payload = json.dumps(
            {
                "from": f"{sender_name} <{sender_email}>",
                "to": [recipient],
                "subject": subject,
                "html": html_body,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status not in {200, 201, 202}:
                    raise HeadCheckError(
                        f"Resend alert failed with HTTP {response.status}."
                    )
            return
        except urllib.error.URLError as exc:
            raise HeadCheckError(f"Resend alert failed: {exc}") from exc

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_security = os.getenv("SMTP_SECURITY", "tls").lower()
    if not smtp_host:
        raise HeadCheckError(
            "No email provider is configured for Alembic alerts "
            "(set RESEND_API_KEY with EMAIL_PROVIDER=resend or SMTP_HOST)."
        )

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = recipient
    message.attach(MIMEText(body, "plain"))
    message.attach(MIMEText(html_body, "html"))

    if smtp_port == 465 or smtp_security == "ssl":
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if smtp_security == "tls":
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(message)


async def run_check(args: argparse.Namespace) -> HeadCheckResult:
    target_dsn = resolve_target_dsn(args)
    repository_head = get_repository_head(args.alembic_config)
    database_heads = await fetch_database_heads(target_dsn)
    result = evaluate_heads(database_heads, repository_head, args.label)
    print(result.success_message())
    return result


def maybe_send_alert(args: argparse.Namespace, message: str) -> None:
    recipient = resolve_alert_recipient(args)
    if not recipient:
        return

    subject = f"Alembic drift detected — {args.label}"
    try:
        send_alert_email(recipient, subject, message)
    except Exception as exc:  # noqa: BLE001
        print(f"Alert delivery failed: {exc}", file=sys.stderr)
        return
    print(f"Alert sent to {recipient}.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    load_environment(args.env_file)
    try:
        asyncio.run(run_check(args))
        return 0
    except HeadCheckError as exc:
        detail = str(exc)
        masked_target = ""
        try:
            masked_target = mask_dsn(resolve_target_dsn(args))
        except HeadCheckError:
            pass

        if masked_target:
            detail = f"{detail}\nTarget: {masked_target}"

        print(detail, file=sys.stderr)
        maybe_send_alert(args, detail)
        return 2
    except Exception as exc:  # noqa: BLE001
        message = f"Alembic head check failed unexpectedly: {exc}"
        print(message, file=sys.stderr)
        maybe_send_alert(args, message)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
