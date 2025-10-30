"""Email sending utilities for Case Organizer."""

from __future__ import annotations

import logging
import smtplib
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage
from threading import RLock
from typing import Callable, Iterable, Optional, Union

from services.settings import settings_manager


class EmailConfigError(RuntimeError):
    """Raised when the SMTP configuration is incomplete."""


@dataclass
class _SMTPConfig:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    use_tls: bool
    from_email: str
    timeout_seconds: float


_CACHE_TTL_SECONDS = 300
_config_lock = RLock()
_cached_config: Optional[_SMTPConfig] = None
_cached_loaded_at: float = 0.0
_EMAIL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="caseorg-email")


def clear_email_cache() -> None:
    """Clear cached SMTP configuration so future sends reload from disk."""
    global _cached_config, _cached_loaded_at
    with _config_lock:
        _cached_config = None
        _cached_loaded_at = 0.0


def _load_smtp_config(
    force: bool = False,
    timing_hook: Optional[Callable[[str], None]] = None,
) -> _SMTPConfig:
    """Load SMTP configuration, caching values to avoid repeated heavy work."""
    global _cached_config, _cached_loaded_at
    now = time.monotonic()
    with _config_lock:
        if not force and _cached_config is not None and (now - _cached_loaded_at) < _CACHE_TTL_SECONDS:
            if timing_hook:
                timing_hook("config_cache_hit")
            return _cached_config

        if timing_hook:
            timing_hook("config_refresh_start")

        host = settings_manager.get("smtp_host")
        port_raw = settings_manager.get("smtp_port", 587)
        username = settings_manager.get("smtp_username")
        use_tls = bool(settings_manager.get("smtp_use_tls", True))
        from_email = settings_manager.get("smtp_from_email")
        timeout_raw = settings_manager.get("smtp_timeout_seconds", 10)

        if not host or not from_email:
            raise EmailConfigError("SMTP host and from-address must be configured before sending email.")

        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise EmailConfigError(f"Invalid SMTP port value: {port_raw!r}") from None
        if port <= 0:
            raise EmailConfigError("SMTP port must be a positive integer.")

        password: Optional[str] = None
        secret_error: Optional[Exception] = None
        try:
            password = settings_manager.get_secret("smtp_password", None)
            if timing_hook:
                timing_hook("password_loaded")
        except RuntimeError as exc:
            secret_error = exc
            if timing_hook:
                timing_hook("password_error")

        if not password:
            plain_password = settings_manager.get("smtp_password")
            if plain_password:
                password = plain_password
                if timing_hook:
                    timing_hook("password_plain_loaded")

        if username and not password:
            if secret_error:
                raise EmailConfigError("SMTP password is not available. Re-run setup to configure email.") from secret_error
            raise EmailConfigError("SMTP password is not available. Re-run setup to configure email.")

        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = 10.0
        if timeout_seconds <= 0:
            timeout_seconds = 10.0

        _cached_config = _SMTPConfig(
            host=host,
            port=port,
            username=username or None,
            password=password or None,
            use_tls=use_tls,
            from_email=from_email,
            timeout_seconds=timeout_seconds,
        )
        _cached_loaded_at = time.monotonic()
        if timing_hook:
            timing_hook("config_refresh_done")
        return _cached_config


def _as_list(recipient: Union[str, Iterable[str]]) -> list[str]:
    if isinstance(recipient, str):
        return [recipient]
    return list(recipient)


def send_email(
    recipient: Union[str, Iterable[str]],
    subject: str,
    body: str,
    _config: Optional[_SMTPConfig] = None,
) -> None:
    logger = logging.getLogger("caseorg.email")
    timing_enabled = bool(settings_manager.get("email_debug_timing", False))
    timing_marks: list[tuple[str, float]] = []

    def mark(stage: str) -> None:
        if timing_enabled:
            timing_marks.append((stage, time.perf_counter()))

    if timing_enabled:
        timing_marks.append(("start", time.perf_counter()))

    if _config is None:
        mark("before_config")
        config = _load_smtp_config(timing_hook=mark)
        mark("config_ready")
    else:
        config = _config
        mark("config_preloaded")

    recipients = _as_list(recipient)
    if not recipients:
        raise ValueError("At least one recipient must be provided")

    if config.username and not config.password:
        raise EmailConfigError("SMTP password is not available. Re-run setup to configure email.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.from_email
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds) as smtp:
            mark("connected")
            if config.use_tls:
                smtp.starttls()
                mark("tls_ready")
            if config.username and config.password:
                smtp.login(config.username, config.password)
                mark("authenticated")
            smtp.send_message(msg)
            mark("sent")
    except Exception as exc:
        print(f"[email] Failed to send message to {recipients}: {exc}")
        raise
    finally:
        mark("completed")
        if timing_enabled and len(timing_marks) > 1:
            summary_parts: list[str] = []
            prev_label, prev_time = timing_marks[0]
            for label, ts in timing_marks[1:]:
                summary_parts.append(f"{prev_label}->{label}:{(ts - prev_time)*1000:.0f}ms")
                prev_label, prev_time = label, ts
            total = (timing_marks[-1][1] - timing_marks[0][1]) * 1000
            logger.info(
                "Email timing to %s | %s | total:%dms",
                recipients,
                " ".join(summary_parts),
                int(total),
            )


def _log_async_result(future: Future) -> None:
    try:
        future.result()
    except Exception as exc:
        logging.getLogger("caseorg.email").error("Background email failed: %s", exc, exc_info=True)


def send_email_async(
    recipient: Union[str, Iterable[str]],
    subject: str,
    body: str,
) -> Future:
    recipients = _as_list(recipient)
    if not recipients:
        raise ValueError("At least one recipient must be provided")

    config = _load_smtp_config()

    def _task() -> None:
        send_email(recipient, subject, body, _config=config)

    future = _EMAIL_EXECUTOR.submit(_task)
    future.add_done_callback(_log_async_result)
    return future
