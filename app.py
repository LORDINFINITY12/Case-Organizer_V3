from __future__ import annotations

import os
import re
import sqlite3
import shutil
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from decimal import Decimal, InvalidOperation
from io import BytesIO
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
import time
from functools import wraps
from pathlib import Path
import json
from typing import Dict, Any, Iterable, Optional

from flask import (
    Flask, request, jsonify, session, redirect, url_for,
    render_template, render_template_string, flash, send_file, g, abort
)
from werkzeug.utils import secure_filename

from services.db import get_app_db, close_app_db
from services.settings import settings_manager
from services import pdf_jobs, pdf_tools
from services.users import (
    create_user,
    count_users,
    count_admins,
    UserExistsError,
    EmailInUseError,
    authenticate_user,
    get_user_by_email,
    get_user_by_id,
    create_password_reset_token,
    get_password_reset,
    consume_password_reset,
    set_user_password,
    mark_user_login,
    list_users,
    set_user_active,
    update_user_email,
    update_user_role,
)
from services.email import send_email_async, EmailConfigError, clear_email_cache
from services.messages import (
    create_message,
    list_inbox,
    list_sent,
    mark_message_read,
    get_message,
    count_unread,
    delete_message,
)

# ---- App config (pulled from caseorg_config.py) ------------------------
try:
    import caseorg_config as config  # renamed to avoid clashing with Debian's 'config' module
except Exception as e:
    raise RuntimeError("caseorg_config.py missing or invalid") from e


FS_ROOT = Path(config.FS_ROOT).resolve() if getattr(config, "FS_ROOT", None) else None
SECRET_KEY = getattr(config, "SECRET_KEY", "dev-local-secret-key")
ALLOWED_EXTENSIONS = set(getattr(config, "ALLOWED_EXTENSIONS", []))

POSTFIX_PREFILL_FILE = settings_manager.paths.config_dir / "postfix.json"

SESSION_TIMEOUT = timedelta(minutes=10)
SESSION_ACTIVITY_KEY = "last_activity"


CASE_LAW_ROOT_NAME = "Case Law"
CASE_LAW_DB_NAME = "case_law_index.db"
CASE_LAW_PRIMARY_TYPES = ("Criminal", "Civil", "Commercial")
CASE_LAW_CASE_TYPES = {
    "Criminal": [
        "498A (Cruelty/Dowry)", "Murder", "Rape", "Sexual Harassment", "Hurt",
        "138 NI Act", "Fraud", "Human Trafficking", "NDPS", "PMLA", "POCSO", "Constitutional", "Others"
    ],
    "Civil": [
        "Property", "Rent Control", "Inheritance/Succession", "Contract",
        "Marital Divorce", "Marital Maintenance", "Marital Guardianship", "Constitutional", "Others"
    ],
    "Commercial": [
        "Trademark", "Copyright", "Patent", "Banking", "Others"
    ],
}


def _sanitize_filename_fragment(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "", (value or "").strip().replace(" ", "-")).lower()


def _build_invoice_filename(invoice: Dict[str, Any]) -> str:
    parts = [
        _sanitize_filename_fragment(invoice.get("invoice_number", "")),
        _sanitize_filename_fragment(invoice.get("client_name", "")),
        _sanitize_filename_fragment(invoice.get("invoice_date", "")),
    ]
    filtered = [part for part in parts if part]
    base = "_".join(filtered) or "invoice"
    if not base.endswith(".pdf"):
        base += ".pdf"
    return base


def generate_invoice_pdf(invoice: Dict[str, Any]) -> tuple[BytesIO, str]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "ReportLab is required to generate invoices. Install it with `pip install reportlab`."
        ) from exc

    pdf_buffer = BytesIO()
    letterhead_margin = 12.7 * mm  # extra half-inch for printed letterhead
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=(24 * mm) + letterhead_margin,
        bottomMargin=20 * mm,
        title=f"Invoice {invoice.get('invoice_number') or ''}".strip() or "Invoice",
    )
    AVAILABLE_MARGIN = 24  # ensure tables stay comfortably within the frame
    available_width = max(doc.width - AVAILABLE_MARGIN, doc.width * 0.9)

    styles = getSampleStyleSheet()
    title_style = styles["Title"].clone("InvoiceTitle")
    title_style.fontSize = 22
    title_style.leading = 26
    title_style.alignment = 0

    meta_block_style = ParagraphStyle(
        "InvoiceMetaBlock",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        alignment=2,
        leftIndent=available_width * 0.55,
    )
    party_style = ParagraphStyle("InvoiceParty", parent=styles["Normal"], fontSize=10, leading=14)
    party_style_right = ParagraphStyle(
        "InvoicePartyRight", parent=party_style, alignment=2  # right-align
    )
    table_header_style = ParagraphStyle(
        "InvoiceHeader",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        alignment=1,
        textColor=colors.whitesmoke,
        spaceAfter=4,
    )
    table_cell_style = ParagraphStyle("InvoiceCell", parent=styles["Normal"], fontSize=10, leading=13)
    money_style = ParagraphStyle(
        "InvoiceMoney", parent=styles["Normal"], fontSize=10, leading=13, alignment=2
    )
    total_label_style = ParagraphStyle(
        "InvoiceTotalLabel", parent=styles["Normal"], fontSize=10, leading=13, alignment=2
    )

    TWO_PLACES = Decimal("0.01")

    def as_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
        if value in (None, "", False):
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return default

    def money_text(amount: Decimal) -> str:
        quantized = amount.quantize(TWO_PLACES)
        return f"{quantized:,}"

    raw_items = invoice.get("items") or []
    processed_items: list[dict[str, Any]] = []
    sum_from_items = Decimal("0")
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        amount = as_decimal(row.get("amount"))
        sum_from_items += amount
        processed_items.append(
            {
                "sn": str(row.get("sn") or ""),
                "item": row.get("item") or "",
                "description": row.get("description") or "",
                "amount": amount,
                "amount_display": money_text(amount),
            }
        )

    requested_total = as_decimal(
        invoice.get("total"), sum_from_items if processed_items else Decimal("0")
    )
    if processed_items and requested_total == Decimal("0") and sum_from_items > Decimal("0"):
        requested_total = sum_from_items
    total_display = money_text(requested_total)

    story: list = []

    story.append(Paragraph("INVOICE", title_style))
    story.append(Spacer(1, 6))

    meta_block_html = (
        "<font size=9><b>Invoice #</b></font><br/>"
        f"{safe_text(invoice.get('invoice_number'))}<br/><br/>"
        "<font size=9><b>Date</b></font><br/>"
        f"{safe_text(invoice.get('invoice_date'))}"
    )
    story.append(Paragraph(meta_block_html, meta_block_style))
    story.append(Spacer(1, 18))

    def format_party(heading: str, lines, alignment: str = "left"):
        body = "<br/>".join(lines) if lines else "—"
        style = party_style_right if alignment == "right" else party_style
        return Paragraph(f"<b>{heading}</b><br/>{body}", style)

    party_col_widths = [available_width * 0.55, available_width * 0.45]
    parties_table = Table(
        [
            [
                format_party("From", invoice.get("issuer_lines") or []),
                format_party("To", invoice.get("recipient_lines") or [], alignment="right"),
            ]
        ],
        colWidths=party_col_widths,
    )
    parties_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    story.append(parties_table)
    story.append(Spacer(1, 18))

    table_data = [
        [
            Paragraph("S/N", table_header_style),
            Paragraph("Item", table_header_style),
            Paragraph("Description", table_header_style),
            Paragraph("Amount", table_header_style),
        ]
    ]

    if processed_items:
        for row in processed_items:
            table_data.append(
                [
                    Paragraph(row.get("sn") or "", table_cell_style),
                    Paragraph(row.get("item") or "", table_cell_style),
                    Paragraph(row.get("description") or "", table_cell_style),
                    Paragraph(row.get("amount_display") or "", money_style),
                ]
            )
    else:
        table_data.append(
            [
                Paragraph("", table_cell_style),
                Paragraph("", table_cell_style),
                Paragraph("No line items recorded", table_cell_style),
                Paragraph("", money_style),
            ]
        )

    table_data.append(
        [
            Paragraph("", table_cell_style),
            Paragraph("", table_cell_style),
            Paragraph("<b>Total</b>", total_label_style),
            Paragraph(total_display, money_style),
        ]
    )

    item_col_widths = [
        available_width * 0.12,
        available_width * 0.24,
        available_width * 0.38,
        available_width * 0.26,
    ]
    items_table = Table(
        table_data,
        colWidths=item_col_widths,
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f4f6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    story.append(items_table)
    story.append(Spacer(1, 12))

    doc.build(story)
    pdf_buffer.seek(0)
    filename = _build_invoice_filename(invoice)
    return pdf_buffer, filename

def safe_text(value: Any) -> str:
    text = str(value or "").strip() or "—"
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

INVOICE_NUMBER_PAD = 5


class InvoiceNumberConflict(Exception):
    """Raised when trying to reuse an existing invoice number."""


class InvoiceStorageError(Exception):
    """Raised when an invoice cannot be written to disk."""


def _format_invoice_number_value(value: int) -> str:
    return str(max(value, 0)).zfill(INVOICE_NUMBER_PAD)


def _parse_invoice_number(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _get_invoice_counter(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", ("invoice_next_number",)
    ).fetchone()
    if not row:
        return 1
    try:
        value = int(row["value"])
    except (TypeError, ValueError):
        value = 1
    return max(value, 1)


def _set_invoice_counter(conn: sqlite3.Connection, value: int) -> None:
    value = max(int(value), 1)
    conn.execute(
        """
        INSERT INTO app_settings(key, value, protected)
        VALUES('invoice_next_number', ?, 0)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(value),),
    )


def _compute_next_invoice_number(conn: sqlite3.Connection) -> int:
    counter = _get_invoice_counter(conn)
    row = conn.execute(
        """
        SELECT invoice_number
        FROM invoices
        WHERE invoice_number GLOB '[0-9]*'
        ORDER BY CAST(invoice_number AS INTEGER) DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        highest = _parse_invoice_number(row["invoice_number"])
        if highest is not None and highest >= counter:
            counter = highest + 1
    return counter


def _suggest_invoice_number(conn: sqlite3.Connection) -> str:
    return _format_invoice_number_value(_compute_next_invoice_number(conn))


def _reserve_invoice_number(conn: sqlite3.Connection) -> str:
    next_value = _compute_next_invoice_number(conn)
    _set_invoice_counter(conn, next_value + 1)
    return _format_invoice_number_value(next_value)


def _ensure_counter_after_use(conn: sqlite3.Connection, used_number: Optional[int]) -> None:
    if used_number is None:
        return
    current = _get_invoice_counter(conn)
    desired = used_number + 1
    if desired > current:
        _set_invoice_counter(conn, desired)


def _invoice_target_path(
    invoice_number: str,
    case_year: Optional[str],
    case_month: Optional[str],
    case_name: Optional[str],
) -> tuple[Path, Optional[Path]]:
    if FS_ROOT is None:
        raise InvoiceStorageError("File storage root is not configured.")

    main_invoices_dir = FS_ROOT / "Invoices"
    main_invoices_dir.mkdir(parents=True, exist_ok=True)

    case_year = (case_year or "").strip()
    case_month = (case_month or "").strip()
    case_name = (case_name or "").strip()

    case_invoices_dir: Optional[Path] = None
    if case_year and case_month and case_name:
        base_dir = FS_ROOT / case_year / case_month / case_name
        if not base_dir.exists():
            raise InvoiceStorageError("Case directory not found on disk.")
        case_invoices_dir = base_dir / "Invoices"
        case_invoices_dir.mkdir(parents=True, exist_ok=True)
    else:
        case_invoices_dir = None

    safe_number = re.sub(r"[^a-zA-Z0-9_-]", "_", invoice_number).strip("_") or "invoice"
    base_filename = f"Invoice_{safe_number}"

    def choose_filename() -> str:
        attempt = 0
        while True:
            if attempt == 0:
                candidate = f"{base_filename}.pdf"
            elif attempt == 1:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                candidate = f"{base_filename}_{ts}.pdf"
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                candidate = f"{base_filename}_{ts}_{secrets.token_hex(2)}.pdf"
            primary_exists = (main_invoices_dir / candidate).exists()
            case_exists = bool(case_invoices_dir and (case_invoices_dir / candidate).exists())
            if not primary_exists and not case_exists:
                return candidate
            attempt += 1

    final_filename = choose_filename()
    primary_path = main_invoices_dir / final_filename
    case_path = case_invoices_dir / final_filename if case_invoices_dir else None
    return primary_path, case_path


def _insert_invoice_row(
    conn: sqlite3.Connection,
    invoice_number: str,
    case_year: Optional[str],
    case_month: Optional[str],
    case_name: Optional[str],
    relative_path: str,
    payload_json: str,
    user_id: Optional[int],
) -> None:
    try:
        conn.execute(
            """
            INSERT INTO invoices (
                invoice_number,
                case_year,
                case_month,
                case_name,
                file_path,
                payload_json,
                generated_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_number,
                case_year or None,
                case_month or None,
                case_name or None,
                relative_path,
                payload_json,
                user_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        if "invoice_number" in str(exc).lower():
            raise InvoiceNumberConflict from exc
        raise


def _clean_lines(values: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(values, list):
        return lines
    for item in values:
        text = str(item or "").strip()
        if text:
            lines.append(text)
    return lines

    raw_items = invoice.get("items") or []
    processed_items: list[dict[str, Any]] = []
    sum_from_items = Decimal("0")
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        amount = as_decimal(row.get("amount"))
        sum_from_items += amount
        processed_items.append(
            {
                "sn": str(row.get("sn") or ""),
                "item": row.get("item") or "",
                "description": row.get("description") or "",
                "amount": amount,
                "amount_display": money_text(amount),
            }
        )

    requested_total = as_decimal(invoice.get("total"), sum_from_items if processed_items else Decimal("0"))
    if processed_items and requested_total == Decimal("0") and sum_from_items > Decimal("0"):
        requested_total = sum_from_items
    total_display = money_text(requested_total)

    story: list = []

    story.append(Paragraph("INVOICE", title_style))
    story.append(Spacer(1, 6))

    meta_block_html = (
        "<font size=9><b>Invoice #</b></font><br/>"
        f"{safe_text(invoice.get('invoice_number'))}<br/><br/>"
        "<font size=9><b>Date</b></font><br/>"
        f"{safe_text(invoice.get('invoice_date'))}"
    )
    story.append(Paragraph(meta_block_html, meta_block_style))
    story.append(Spacer(1, 18))

    def format_party(heading: str, lines, alignment: str = "left"):
        body = "<br/>".join(lines) if lines else "—"
        style = party_style_right if alignment == "right" else party_style
        return Paragraph(f"<b>{heading}</b><br/>{body}", style)

    party_col_widths = [available_width * 0.55, available_width * 0.45]
    parties_table = Table(
        [
            [
                format_party("From", invoice.get("issuer_lines") or []),
                format_party("To", invoice.get("recipient_lines") or [], alignment="right"),
            ]
        ],
        colWidths=party_col_widths,
    )
    parties_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    story.append(parties_table)
    story.append(Spacer(1, 18))

    table_data = [
        [
            Paragraph("S/N", table_header_style),
            Paragraph("Item", table_header_style),
            Paragraph("Description", table_header_style),
            Paragraph("Amount", table_header_style),
        ]
    ]

    if processed_items:
        for row in processed_items:
            table_data.append(
                [
                    Paragraph(row.get("sn") or "", table_cell_style),
                    Paragraph(row.get("item") or "", table_cell_style),
                    Paragraph(row.get("description") or "", table_cell_style),
                    Paragraph(row.get("amount_display") or "", money_style),
                ]
            )
    else:
        table_data.append(
            [
                Paragraph("", table_cell_style),
                Paragraph("", table_cell_style),
                Paragraph("No line items recorded", table_cell_style),
                Paragraph("", table_cell_style),
            ]
        )

    table_data.append(
        [
            Paragraph("", table_cell_style),
            Paragraph("", table_cell_style),
            Paragraph("<b>Total</b>", total_label_style),
            Paragraph(total_display, money_style),
        ]
    )

    item_col_widths = [
        available_width * 0.12,
        available_width * 0.24,
        available_width * 0.38,
        available_width * 0.26,
    ]
    items_table = Table(
        table_data,
        colWidths=item_col_widths,
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f4f6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    story.append(items_table)
    story.append(Spacer(1, 12))

    doc.build(story)

    pdf_buffer.seek(0)
    filename = _build_invoice_filename(invoice)
    return pdf_buffer, filename

def is_initial_setup_complete() -> bool:
    try:
        return bool(config.FS_ROOT) and count_users() > 0
    except Exception:
        return False


def login_user_session(user: sqlite3.Row) -> None:
    session.clear()
    session.permanent = True
    session['user_id'] = user['id']
    session['user_role'] = user['role']
    session['user_email'] = user['email']
    session[SESSION_ACTIVITY_KEY] = datetime.utcnow().isoformat()


def logout_user_session() -> None:
    session.clear()


def require_login(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        if g.get('current_user') is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return handler(*args, **kwargs)

    return wrapper


def require_login_api(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        user = g.get('current_user')
        if user is None:
            return jsonify({"ok": False, "msg": "Authentication required."}), 401
        return handler(*args, **kwargs)

    return wrapper


def require_admin(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        if g.get('current_user') is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        if g.current_user['role'] != 'admin':
            flash("Administrator access required.", "error")
            return redirect(url_for("home"))
        return handler(*args, **kwargs)

    return wrapper


def require_admin_api(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        user = g.get('current_user')
        if user is None:
            return jsonify({"ok": False, "msg": "Authentication required."}), 401
        if user['role'] != 'admin':
            return jsonify({"ok": False, "msg": "Administrator access required."}), 403
        return handler(*args, **kwargs)

    return wrapper


# ---- Flask setup --------------------------------------------------------
SLOW_REQ_THRESHOLD_MS = float(os.environ.get("CASEORG_SLOW_MS", "250") or "250")
STATIC_MAX_AGE = int(os.environ.get("CASEORG_STATIC_MAX_AGE", "86400") or "86400")
PDF_THUMB_MAX_PAGES = int(os.environ.get("CASEORG_PDF_THUMB_MAX_PAGES", "200") or "200")
PDF_THUMB_MAX_DIM_PX = int(
    os.environ.get(
        "CASEORG_PDF_THUMB_MAX_DIM",
        os.environ.get("CASEORG_PDF_THUMB_WIDTH", "520"),
    )
    or "520"
)


app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = SESSION_TIMEOUT
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = STATIC_MAX_AGE

PDF_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="caseorg-pdf")

_PDF_JOB_CLEANUP_TTL_SECONDS = 30.0
_PDF_JOB_CLEANUP_LAST_RUN_AT = 0.0

print("Running app.py from:", os.path.abspath(__file__))
print("FS_ROOT:", FS_ROOT)

# ---- Request timing (slow request logger) -------------------------------
@app.before_request
def _capture_request_start() -> None:
    g._req_started_at = time.perf_counter()


@app.after_request
def _log_slow_requests(response):
    started = getattr(g, "_req_started_at", None)
    if started is None:
        return response
    duration_ms = (time.perf_counter() - started) * 1000.0
    if duration_ms >= SLOW_REQ_THRESHOLD_MS and not _is_static_request():
        app.logger.info("slow request %.1f ms %s %s", duration_ms, request.method, request.path)
    return response


# ---- Utilities ----------------------------------------------------------
def ensure_root() -> None:
    """Create the storage root if configured."""
    if FS_ROOT:
        FS_ROOT.mkdir(parents=True, exist_ok=True)


def _case_law_root() -> Path:
    if not FS_ROOT:
        raise RuntimeError("Storage root is not configured yet")
    root = FS_ROOT / CASE_LAW_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _case_law_db_file() -> Path:
    ensure_root()
    root = FS_ROOT
    if not root:
        raise RuntimeError("Storage root is not configured yet")
    return root / CASE_LAW_DB_NAME


# Requests for static assets should not hit auth/session DB paths.
def _is_static_request() -> bool:
    endpoint = request.endpoint or ""
    return endpoint == "static" or (request.path or "").startswith("/static/")


_SETUP_CACHE = {"done": None, "checked_at": 0.0}
_SETUP_CACHE_TTL = 300.0  # seconds


def _setup_complete_cached() -> bool:
    now = time.perf_counter()
    if _SETUP_CACHE["done"] is True:
        return True
    if (now - _SETUP_CACHE["checked_at"]) < _SETUP_CACHE_TTL:
        return bool(_SETUP_CACHE["done"])
    _SETUP_CACHE["checked_at"] = now
    try:
        _SETUP_CACHE["done"] = bool(config.FS_ROOT) and count_users() > 0
    except Exception:
        _SETUP_CACHE["done"] = False
    return bool(_SETUP_CACHE["done"])


def _ensure_case_law_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS case_law (
            id INTEGER PRIMARY KEY,
            petitioner TEXT NOT NULL,
            respondent TEXT NOT NULL,
            citation TEXT NOT NULL,
            decision_year INTEGER NOT NULL,
            decision_month TEXT,
            primary_type TEXT NOT NULL,
            subtype TEXT NOT NULL,
            folder_rel TEXT NOT NULL,
            file_name TEXT NOT NULL,
            note_path_rel TEXT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(petitioner, respondent, citation, primary_type, subtype, decision_year)
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS case_law_fts USING fts5(
            content,
            petitioner,
            respondent,
            citation,
            note,
            case_id UNINDEXED
        )
        """
    )


def load_installed_postfix_defaults() -> Dict[str, Any]:
    try:
        data = json.loads(POSTFIX_PREFILL_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def write_postfix_prefill_files(host: str, port: int | str, username: str, password: str, use_tls: bool, from_email: str) -> None:
    payload = {
        "smtp_host": host,
        "smtp_port": str(port),
        "smtp_username": username,
        "smtp_password": password,
        "smtp_use_tls": use_tls,
        "smtp_from_email": from_email,
    }
    try:
        POSTFIX_PREFILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        POSTFIX_PREFILL_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        POSTFIX_PREFILL_FILE.chmod(0o640)
    except Exception:
        pass

    script_path = POSTFIX_PREFILL_FILE.with_name("setup-postfix.sh")
    tls_value = "yes" if use_tls else "no"
    script = f"""#!/bin/sh
set -e

MAIN_CF=/etc/postfix/main.cf
SASL=/etc/postfix/sasl_passwd

cat <<'EOF' | sudo tee "$MAIN_CF" >/dev/null
relayhost = [{host}]:{port} {username}:{password}
smtp_use_tls = {tls_value}
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtp_sasl_security_options = noanonymous
EOF

printf '[{host}]:{port} {username}:{password}\n' | sudo tee "$SASL" >/dev/null
sudo chmod 600 "$SASL"
if command -v postmap >/dev/null 2>&1; then
    sudo postmap "$SASL"
fi
if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable postfix.service || true
    sudo systemctl restart postfix.service || true
fi
echo "Postfix relay updated."
"""
    try:
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o600)
    except Exception:
        pass


def get_case_law_db() -> sqlite3.Connection:
    if 'case_law_db' not in g:
        path = _case_law_db_file()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        _ensure_case_law_schema(conn)
        g.case_law_db = conn
    return g.case_law_db


@app.teardown_appcontext
def close_case_law_db(_: Optional[BaseException]) -> None:
    conn = g.pop('case_law_db', None)
    if conn is not None:
        conn.close()


@app.before_request
def _enforce_session_timeout():
    if _is_static_request():
        return
    user_id = session.get('user_id')
    if user_id is None:
        return

    raw_last_activity = session.get(SESSION_ACTIVITY_KEY)
    now = datetime.utcnow()

    last_activity = None
    if raw_last_activity:
        with suppress(ValueError, TypeError):
            last_activity = datetime.fromisoformat(raw_last_activity)

    if last_activity and now - last_activity > SESSION_TIMEOUT:
        logout_user_session()
        accept_mimetypes = request.accept_mimetypes
        wants_json = (
            accept_mimetypes.best == "application/json"
            or accept_mimetypes["application/json"] > accept_mimetypes["text/html"]
        )
        if wants_json or request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": "Session expired. Please log in again."}), 401
        flash("Session expired due to inactivity. Please log in again.", "warning")
        return redirect(url_for("login"))

    session.permanent = True
    session[SESSION_ACTIVITY_KEY] = now.isoformat()


@app.before_request
def _load_current_user() -> None:
    if _is_static_request():
        return
    g.current_user = None
    g.unread_count = 0
    user_id = session.get('user_id')
    if user_id is None:
        return
    user = get_user_by_id(user_id)
    if user and user['is_active']:
        g.current_user = user
        g.unread_count = count_unread(user['id'])
    else:
        session.clear()


@app.before_request
def _cleanup_expired_pdf_jobs() -> None:
    if _is_static_request():
        return
    global _PDF_JOB_CLEANUP_LAST_RUN_AT
    now = time.monotonic()
    if (now - _PDF_JOB_CLEANUP_LAST_RUN_AT) < _PDF_JOB_CLEANUP_TTL_SECONDS:
        return
    _PDF_JOB_CLEANUP_LAST_RUN_AT = now
    with suppress(Exception):
        pdf_jobs.cleanup_expired_jobs()


@app.teardown_appcontext
def close_application_db(exc: Optional[BaseException]) -> None:
    close_app_db(exc)


def _bootstrap_app_state() -> None:
    conn = get_app_db()
    conn.close()
    g.pop('app_db', None)


_bootstrap_app_state_ran = False

if hasattr(app, 'before_first_request'):
    app.before_first_request(_bootstrap_app_state)
else:
    @app.before_request
    def _bootstrap_app_state_once():
        global _bootstrap_app_state_ran
        if _bootstrap_app_state_ran:
            return
        _bootstrap_app_state()
        _bootstrap_app_state_ran = True


@app.context_processor
def inject_current_user() -> Dict[str, Any]:
    return {
        "current_user": g.get('current_user'),
        "unread_count": g.get('unread_count', 0),
    }


@app.context_processor
def inject_static_url() -> Dict[str, Any]:
    def static_url(filename: str) -> str:
        safe_name = (filename or "").lstrip("/")
        rel_path = Path(safe_name)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            return url_for("static", filename=safe_name)
        try:
            full_path = Path(app.static_folder) / rel_path
            version = int(full_path.stat().st_mtime)
        except OSError:
            version = None

        if version is None:
            return url_for("static", filename=safe_name)
        return url_for("static", filename=safe_name, v=version)

    return {"static_url": static_url}


def refresh_case_law_index(
    conn: sqlite3.Connection,
    case_id: int,
    judgement_text: str,
    petitioner: str,
    respondent: str,
    citation: str,
    note_text: str,
) -> None:
    conn.execute("DELETE FROM case_law_fts WHERE rowid = ?", (case_id,))
    conn.execute(
        """
        INSERT INTO case_law_fts(rowid, content, petitioner, respondent, citation, note, case_id)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            judgement_text or "",
            petitioner,
            respondent,
            citation,
            note_text or "",
            case_id,
        ),
    )


_NEAR_RE = re.compile(r'("[^"]+"|\S+)\s+NEAR/(\d+)\s+("[^"]+"|\S+)', re.IGNORECASE)
_BOOLEAN_OPERATORS = {
    "and": "AND",
    "or": "OR",
    "not": "NOT",
    "near": "NEAR",
}


def normalize_boolean_query(raw: str) -> str:
    query = normalize_ws(raw)
    if not query:
        return ""

    def _near_sub(match: re.Match) -> str:
        left, distance, right = match.groups()
        return f"NEAR({left} {right}, {distance})"

    query = _NEAR_RE.sub(_near_sub, query)
    for lower, upper in _BOOLEAN_OPERATORS.items():
        query = re.sub(rf"\b{lower}\b", upper, query, flags=re.IGNORECASE)
    return query

@app.before_request
def _require_setup():
    if _is_static_request():
        return
    allowed_endpoints = {
        "setup",
        "login",
        "static",
        "ping",
        "__routes",
        "forgot_password",
        "reset_password",
    }

    endpoint = request.endpoint or ""

    if not _setup_complete_cached():
        if endpoint not in allowed_endpoints:
            return redirect(url_for("setup"))
        return

    if g.get('current_user') is None and endpoint not in allowed_endpoints:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    global FS_ROOT

    if is_initial_setup_complete():
        return redirect(url_for("login"))

    prefill = load_installed_postfix_defaults()

    try:
        stored_password = settings_manager.get_secret("smtp_password", "")
    except RuntimeError:
        stored_password = ""

    existing_tls = settings_manager.get("smtp_use_tls", None)

    form_state = {
        "fs_root": config.FS_ROOT or "",
        "smtp_host": settings_manager.get("smtp_host", ""),
        "smtp_port": settings_manager.get("smtp_port", ""),
        "smtp_username": settings_manager.get("smtp_username", ""),
        "smtp_use_tls": bool(existing_tls if existing_tls is not None else prefill.get("smtp_use_tls", True)),
        "smtp_from_email": settings_manager.get("smtp_from_email", ""),
        "admin_email": "",
        "smtp_password": stored_password,
    }

    smtp_locked = bool(prefill)

    def _coalesce(value, fallback):
        return value if value not in ("", None) else fallback

    form_state["smtp_host"] = _coalesce(form_state["smtp_host"], prefill.get("smtp_host", ""))
    form_state["smtp_username"] = _coalesce(form_state["smtp_username"], prefill.get("smtp_username", ""))
    form_state["smtp_from_email"] = _coalesce(form_state["smtp_from_email"], prefill.get("smtp_from_email", ""))
    form_state["smtp_password"] = _coalesce(form_state["smtp_password"], prefill.get("smtp_password", ""))

    prefill_port = prefill.get("smtp_port")
    current_port = form_state["smtp_port"]
    if isinstance(current_port, int):
        current_port_str = str(current_port)
    elif current_port in (None, ""):
        current_port_str = ""
    else:
        current_port_str = str(current_port)
    if (not current_port_str) and prefill_port not in (None, ""):
        current_port_str = str(prefill_port)
    form_state["smtp_port"] = current_port_str

    if existing_tls is None and prefill.get("smtp_use_tls") is not None:
        form_state["smtp_use_tls"] = bool(prefill["smtp_use_tls"])


    if request.method == "POST":
        form_state.update({
            "fs_root": (request.form.get("fs_root") or "").strip(),
            "smtp_host": (request.form.get("smtp_host") or "").strip(),
            "smtp_port": request.form.get("smtp_port") or "",
            "smtp_username": (request.form.get("smtp_username") or "").strip(),
            "smtp_use_tls": request.form.get("smtp_use_tls") in {"1", "true", "on"},
            "smtp_from_email": (request.form.get("smtp_from_email") or "").strip(),
            "admin_email": (request.form.get("admin_email") or "").strip(),
        })
        form_state["smtp_port"] = str(form_state["smtp_port"] or "").strip()
        smtp_password = request.form.get("smtp_password") or ""
        admin_password = request.form.get("admin_password") or ""
        admin_password2 = request.form.get("admin_password2") or ""

        if not smtp_password:
            smtp_password = form_state.get("smtp_password", "")
        form_state["smtp_password"] = smtp_password
        form_state["smtp_use_tls"] = bool(form_state["smtp_use_tls"])

        errors = []

        if not form_state["fs_root"]:
            errors.append("Please provide a storage path for FS Root.")

        if not form_state["smtp_host"]:
            errors.append("SMTP host is required.")

        try:
            smtp_port_int = int(form_state["smtp_port"]) if form_state["smtp_port"] else 0
            if smtp_port_int <= 0:
                raise ValueError
        except ValueError:
            errors.append("Enter a valid SMTP port.")
            smtp_port_int = 0

        if not form_state["smtp_from_email"]:
            errors.append("Default from-address is required for outgoing email.")

        if not form_state["admin_email"]:
            errors.append("Administrator email is required.")

        if not admin_password or len(admin_password) < 8:
            errors.append("Administrator password must be at least 8 characters long.")
        elif admin_password != admin_password2:
            errors.append("Administrator password confirmation does not match.")

        fs_root_path: Optional[Path] = None
        if not errors:
            try:
                fs_root_path = Path(form_state["fs_root"]).expanduser().resolve()
                fs_root_path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                errors.append(f"Failed to prepare storage directory: {exc}")

        admin_id = None
        if not errors:
            try:
                config.save_fs_root(str(fs_root_path))
                FS_ROOT = fs_root_path

                settings_manager.set("smtp_host", form_state["smtp_host"])
                settings_manager.set("smtp_port", smtp_port_int)
                settings_manager.set("smtp_username", form_state["smtp_username"])
                settings_manager.set("smtp_use_tls", bool(form_state["smtp_use_tls"]))
                settings_manager.set("smtp_from_email", form_state["smtp_from_email"])

                if smtp_password:
                    try:
                        settings_manager.set_secret("smtp_password", smtp_password)
                    except RuntimeError:
                        settings_manager.set("smtp_password", smtp_password)
                        flash(
                            "SMTP password stored without encryption; set CASEORG_SECRET_KEY for secure storage.",
                            "warning",
                        )

                clear_email_cache()

                secret_key = settings_manager.get("flask_secret_key")
                if not secret_key:
                    secret_key = secrets.token_urlsafe(32)
                    settings_manager.set("flask_secret_key", secret_key)
                config.SECRET_KEY = secret_key
                app.secret_key = secret_key

                admin_id = create_user(form_state["admin_email"], admin_password, role="admin")
            except UserExistsError:
                errors.append("An account with that email already exists.")
            except Exception as exc:
                errors.append(f"Failed to finalise setup: {exc}")

        if errors:
            for err in errors:
                flash(err, "error")
        else:
            if admin_id is not None:
                user_row = get_user_by_id(admin_id)
                if user_row:
                    login_user_session(user_row)
            if smtp_password and not smtp_locked:
                port_value = form_state.get("smtp_port") or smtp_port_int or ""
                write_postfix_prefill_files(
                    form_state["smtp_host"],
                    port_value,
                    form_state.get("smtp_username", ""),
                    smtp_password,
                    bool(form_state.get("smtp_use_tls", True)),
                    form_state.get("smtp_from_email", ""),
                )
            flash("Initial setup complete. You are signed in as the administrator.", "success")
            return redirect(url_for("home"))

    template = """
      <!doctype html>
      <title>Case Organizer – Setup</title>
      <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
      <div class="login-body">
        <div class="login-card">
          <h2>Initial Setup</h2>
          {% include '_flash.html' %}
          <form method="post" class="login-form">
            <label for="fs_root">Storage Folder</label>
            <input id="fs_root" name="fs_root" type="text" value="{{ state.fs_root }}" placeholder="/mnt/data/case-files" required>

            <h3>Outbound Email (SMTP)</h3>
            {% if smtp_locked %}
            <p class="setup-hint">
              These SMTP settings were captured during installation. To change them later, run
              <code>sudo dpkg-reconfigure case-organizer</code> after updating Postfix.
            </p>
            {% endif %}
            <label for="smtp_host">SMTP Host</label>
            <input id="smtp_host" name="smtp_host" type="text" value="{{ state.smtp_host }}" required{{ ' readonly' if smtp_locked else '' }}>

            <label for="smtp_port">SMTP Port</label>
            <input id="smtp_port" name="smtp_port" type="number" value="{{ state.smtp_port }}" required{{ ' readonly' if smtp_locked else '' }}>

            <label for="smtp_username">SMTP Username</label>
            <input id="smtp_username" name="smtp_username" type="text" value="{{ state.smtp_username }}"{{ ' readonly' if smtp_locked else '' }}>

            <label for="smtp_password">SMTP Password</label>
            <input id="smtp_password" name="smtp_password" type="password" autocomplete="new-password"{{ ' readonly' if smtp_locked else '' }}>

            {% if smtp_locked %}
            <label class="checkbox-inline locked-checkbox">
              <input type="checkbox" value="1" {% if state.smtp_use_tls %}checked{% endif %} disabled>
              Use TLS/STARTTLS
            </label>
            <input type="hidden" name="smtp_use_tls" value="{{ '1' if state.smtp_use_tls else '' }}">
            {% else %}
            <label class="checkbox-inline">
              <input type="checkbox" name="smtp_use_tls" value="1" {% if state.smtp_use_tls %}checked{% endif %}>
              Use TLS/STARTTLS
            </label>
            {% endif %}

            <label for="smtp_from_email">From Email</label>
            <input id="smtp_from_email" name="smtp_from_email" type="email" value="{{ state.smtp_from_email }}" required{{ ' readonly' if smtp_locked else '' }}>

            <h3>Administrator Account</h3>
            <label for="admin_email">Admin Email</label>
            <input id="admin_email" name="admin_email" type="email" value="{{ state.admin_email }}" required>

            <label for="admin_password">Password</label>
            <input id="admin_password" name="admin_password" type="password" autocomplete="new-password" required>

            <label for="admin_password2">Confirm Password</label>
            <input id="admin_password2" name="admin_password2" type="password" autocomplete="new-password" required>

            <button class="btn-primary" type="submit">Complete Setup</button>
          </form>
          <p class="login-foot">Settings stored in {{ settings_path }}</p>
        </div>
      </div>
    """

    return render_template_string(
        template,
        state=form_state,
        settings_path=str(settings_manager.paths.settings_file),
    )


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def month_dir_name(dt: datetime) -> str:
    # e.g., "Jan", "Feb" ...
    return dt.strftime("%b")

def ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d%m%Y")

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_ILLEGAL_FS_CHARS = re.compile(r"[\\/:*?\"<>|]")


def sanitize_case_law_component(text: str, replacement: str = " ") -> str:
    cleaned = normalize_ws(text)
    cleaned = _ILLEGAL_FS_CHARS.sub(replacement, cleaned)
    cleaned = normalize_ws(cleaned)
    return cleaned


def build_case_law_display_name(petitioner: str, respondent: str, citation: str) -> str:
    return f"{petitioner} vs {respondent} [{citation}]"


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def normalize_primary_type(value: str) -> Optional[str]:
    candidate = normalize_ws(value)
    for option in CASE_LAW_PRIMARY_TYPES:
        if candidate.lower() == option.lower():
            return option
    return None


def normalize_case_type(primary: str, value: str) -> Optional[str]:
    pool = CASE_LAW_CASE_TYPES.get(primary)
    if not pool:
        return None
    candidate = normalize_ws(value)
    for option in pool:
        if candidate.lower() == option.lower():
            return option
    return None


def case_law_error(message: str, status: int = 400):
    return jsonify({"ok": False, "msg": message}), status


def short_excerpt(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    compact = normalize_ws(text)
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def extract_note_summary(content: str) -> str:
    raw = content or ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("Note", "note", "Summary", "summary", "Additional Notes", "additional_notes"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return normalize_ws(value)
    except json.JSONDecodeError:
        pass
    return normalize_ws(raw)


def fetch_case_law_record(conn: sqlite3.Connection, case_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM case_law WHERE id = ?", (case_id,)).fetchone()


def case_law_file_path(row: sqlite3.Row) -> Path:
    base = (FS_ROOT / row["folder_rel"]).resolve()
    full = (base / row["file_name"]).resolve()
    root = FS_ROOT.resolve()
    if not str(full).startswith(str(root)):
        raise RuntimeError("Resolved file path escapes storage root")
    return full


def case_law_folder_path(row: sqlite3.Row) -> Path:
    folder = (FS_ROOT / row["folder_rel"]).resolve()
    root = FS_ROOT.resolve()
    if not str(folder).startswith(str(root)):
        raise RuntimeError("Resolved folder path escapes storage root")
    return folder


def case_law_note_path(row: sqlite3.Row) -> Path:
    note = (FS_ROOT / row["note_path_rel"]).resolve()
    root = FS_ROOT.resolve()
    if not str(note).startswith(str(root)):
        raise RuntimeError("Resolved note path escapes storage root")
    return note


def serialize_case_law(row: sqlite3.Row, text_preview: str = "") -> Dict[str, Any]:
    return {
        "id": row["id"],
        "petitioner": row["petitioner"],
        "respondent": row["respondent"],
        "citation": row["citation"],
        "decision_year": row["decision_year"],
        "decision_month": row["decision_month"],
        "primary_type": row["primary_type"],
        "case_type": row["case_type"],
        "folder": row["folder_rel"],
        "file_name": row["file_name"],
        "note_path": row["note_path_rel"],
        "note_preview": short_excerpt(row["note_text"]),
        "text_preview": short_excerpt(text_preview or row["note_text"]),
        "download_url": url_for("case_law_download", case_id=row["id"]),
        "note_url": url_for("case_law_note", case_id=row["id"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def case_dir(year: int, month_str: str, case_name: str) -> Path:
    return FS_ROOT / f"{year}" / month_str / case_name

def domain_code(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d == "criminal": return "CRL"
    if d == "civil":    return "CIVIL"
    if d == "commercial": return "COMM"
    return d.upper() if d else ""

def type_code(main_type: str) -> str:
    m = (main_type or "").strip().lower()
    # Extend this mapping as needed
    if m == "transfer petition":      return "TP"
    if m == "criminal revision":      return "CRL.REV."
    if m == "writ petition":          return "WP"
    if m == "bail application":       return "BAIL"
    if m == "orders" or m == "order": return "ORD"
    if m == "criminal miscellaneous": return "CRL.MISC."
    return (main_type or "").upper()

def build_filename(dt: datetime, main_type: str, domain: str, case_name: str, ext: str) -> str:
    # (DDMMYYYY) TYPE DOMAIN Petitioner v. Respondent.ext
    prefix = f"({ddmmyyyy(dt)}) {type_code(main_type)} {domain_code(domain)} {case_name}"
    return f"{prefix}.{ext}"

def build_case_name_from_parties(petitioner: str, respondent: str) -> str:
    pn = normalize_ws(petitioner)
    rn = normalize_ws(respondent)
    return f"{pn} v. {rn}" if pn and rn else ""


def extract_text_for_index(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pdfminer.high_level import extract_text  # type: ignore

            return extract_text(str(file_path))
        if suffix == ".txt":
            return file_path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".docx":
            from docx import Document  # type: ignore

            doc = Document(str(file_path))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        print(f"[case-law] Failed to extract text from {file_path}: {exc}")
    return ""


def make_note_json(payload: Dict[str, Any]) -> str:
    """
    Produce a human-readable JSON-like text with blank lines between sections.
    Valid JSON with extra blank lines (allowed) for easy reading in editors.
    """
    from collections import OrderedDict
    from json import dumps

    od = OrderedDict()
    # Parties
    od["Petitioner Name"] = payload.get("Petitioner Name", "")
    od["Petitioner Address"] = payload.get("Petitioner Address", "")
    od["Petitioner Contact"] = payload.get("Petitioner Contact", "")
    od["__BLANK1__"] = ""
    od["Respondent Name"] = payload.get("Respondent Name", "")
    od["Respondent Address"] = payload.get("Respondent Address", "")
    od["Respondent Contact"] = payload.get("Respondent Contact", "")
    od["__BLANK2__"] = ""
    od["Our Party"] = payload.get("Our Party", "")
    od["__BLANK3__"] = ""
    # Classification
    od["Case Category"] = payload.get("Case Category", "")
    od["Case Subcategory"] = payload.get("Case Subcategory", "")
    od["Case Type"] = payload.get("Case Type", "")
    od["__BLANK4__"] = ""
    # Courts
    od["Court of Origin"] = {
        "State":   payload.get("Origin State", ""),
        "District":payload.get("Origin District", ""),
        "Court/Forum": payload.get("Origin Court/Forum", ""),
    }
    od["__BLANK5__"] = ""
    od["Current Court/Forum"] = {
        "State":   payload.get("Current State", ""),
        "District":payload.get("Current District", ""),
        "Court/Forum": payload.get("Current Court/Forum", ""),
    }
    od["__BLANK6__"] = ""
    od["Additional Notes"] = payload.get("Additional Notes", "")

    s = dumps(od, indent=2, ensure_ascii=False)
    # Replace spacer keys with blank lines
    s = re.sub(r'\n\s+"__BLANK[0-9]+__":\s*"",\n', "\n\n", s)
    return s

# ---- Diagnostics --------------------------------------------------------
@app.get("/ping")
def ping():
    return "pong"

@app.get("/__routes")
def __routes():
    lines = [
        f"{r.rule}  [{','.join(sorted(m for m in r.methods if m not in {'HEAD','OPTIONS'}))}]"
        for r in app.url_map.iter_rules()
    ]
    return "<pre>" + "\n".join(sorted(lines)) + "</pre>"

# ---- Browse APIs for Manage Case ---------------------------------------
@app.get("/api/years")
def api_years():
    years = []
    if FS_ROOT.exists():
        for p in FS_ROOT.iterdir():
            if p.is_dir() and re.fullmatch(r"\d{4}", p.name):
                years.append(p.name)
    years.sort()  # ascending "2024", "2025"
    return jsonify({"years": years})

@app.get("/api/months")
def api_months():
    year = (request.args.get("year") or "").strip()
    months = []
    base = FS_ROOT / year
    if year and base.exists() and base.is_dir():
        for m in base.iterdir():
            if m.is_dir():
                months.append(m.name)
    # order by calendar month if using Jan..Dec names
    order = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    months.sort(key=lambda x: order.index(x) if x in order else x)
    return jsonify({"months": months})

@app.get("/api/cases")
def api_cases():
    year  = (request.args.get("year") or "").strip()
    month = (request.args.get("month") or "").strip()
    cases = []
    base = FS_ROOT / year / month
    if base.exists() and base.is_dir():
        for d in base.iterdir():
            if d.is_dir():
                cases.append(d.name)
    cases.sort(key=lambda s: s.lower())  # alphabetical by case name
    return jsonify({"cases": cases})


@app.get("/api/cases/search")
def api_case_search():
    query = (request.args.get("q") or "").strip()
    matches: list[dict[str, str]] = []
    if not query:
        return jsonify({"cases": matches})

    lowered = query.lower()
    if FS_ROOT.exists():
        for year_dir in FS_ROOT.iterdir():
            if not year_dir.is_dir():
                continue
            year = year_dir.name
            if not re.fullmatch(r"\d{4}", year):
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                month = month_dir.name
                for case_dir in month_dir.iterdir():
                    if not case_dir.is_dir():
                        continue
                    case_name = case_dir.name
                    if lowered in case_name.lower():
                        matches.append({
                            "year": year,
                            "month": month,
                            "case": case_name,
                        })
                        if len(matches) >= 100:
                            return jsonify({"cases": matches})
    return jsonify({"cases": matches})


@app.get("/api/invoices/next-number")
@require_login_api
def api_invoice_next_number():
    conn = get_app_db()
    try:
        suggestion = _suggest_invoice_number(conn)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"ok": False, "msg": f"Unable to determine invoice number: {exc}"}), 500
    return jsonify({"ok": True, "invoice_number": suggestion})

# ---- Auth & Home --------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if is_initial_setup_complete() and g.get('current_user') is not None:
        flash("You are already signed in.", "info")
        return redirect(url_for("home"))

    email_value = ""
    if request.method == "POST":
        raw_email = request.form.get("email") or request.form.get("username") or ""
        email_value = normalize_ws(raw_email).lower()
        password = request.form.get("password") or ""

        user = authenticate_user(email_value, password)
        if user:
            login_user_session(user)
            mark_user_login(user["id"])
            flash(f"Welcome back, {user['email']}", "success")
            return redirect(url_for("home"))
        flash("Invalid email or password.", "error")

    try:
        return render_template("login.html", email=email_value, username=email_value)
    except Exception:
        return render_template_string("""
            <!doctype html><title>Login</title>
            <h1>Case Organizer</h1>
            <form method="post">
              <input name="email" type="email" placeholder="Email" value="{{ email }}" required>
              <input name="password" type="password" placeholder="Password" required>
              <button>Sign In</button>
            </form>
            <p><a href="{{ url_for('forgot_password') }}">Forgot your password?</a></p>
        """, email=email_value)


@app.route("/logout")
def logout():
    logout_user_session()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not is_initial_setup_complete():
        return redirect(url_for("setup"))

    if request.method == "POST":
        email = normalize_ws(request.form.get("email") or "").lower()
        user = get_user_by_email(email)
        if user:
            token = create_password_reset_token(user["id"])
            reset_link = url_for("reset_password", token=token, _external=True)
            body = (
                "Hello,\n\n"
                "A password reset was requested for your Case Organizer account.\n"
                f"Use the following link to set a new password: {reset_link}\n\n"
                "If you did not request this change, you can ignore this email."
            )
            try:
                future = send_email_async(user["email"], "Case Organizer password reset", body)
            except EmailConfigError:
                flash("Email is not configured. Contact an administrator to reset your password.", "error")
                return redirect(url_for("forgot_password"))
            except Exception:
                flash("Unable to send the reset email. Try again later or contact an administrator.", "error")
                return redirect(url_for("forgot_password"))
            else:
                try:
                    future.result(timeout=1.0)
                except FutureTimeout:
                    app.logger.info("Password reset email queued for %s", user["email"])
                except Exception as exc:
                    app.logger.error("Password reset email failed quickly: %s", exc, exc_info=True)
                    flash("Unable to send the reset email. Try again later or contact an administrator.", "error")
                    return redirect(url_for("forgot_password"))

        flash("If that email is registered, reset instructions have been sent.", "info")
        return redirect(url_for("login"))

    try:
        return render_template("forgot_password.html")
    except Exception:
        return render_template_string("""
            <!doctype html><title>Forgot Password</title>
            <h1>Reset your password</h1>
            <form method="post">
              <input name="email" type="email" placeholder="Email" required>
              <button>Send reset link</button>
            </form>
            <p><a href="{{ url_for('login') }}">Back to login</a></p>
        """)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if not token:
        flash("Reset token is required.", "error")
        return redirect(url_for("forgot_password"))

    reset_row = get_password_reset(token)
    if not reset_row:
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("forgot_password"))

    user = get_user_by_id(reset_row["user_id"])
    if not user or not user["is_active"]:
        flash("The account associated with this link is no longer available.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
        elif password != password2:
            flash("Passwords do not match.", "error")
        else:
            set_user_password(user["id"], password)
            consume_password_reset(reset_row["id"])
            mark_user_login(user["id"])
            login_user_session(user)
            flash("Password updated. You are now signed in.", "success")
            return redirect(url_for("home"))

    try:
        return render_template("reset_password.html")
    except Exception:
        return render_template_string("""
            <!doctype html><title>Reset Password</title>
            <h1>Choose a new password</h1>
            <form method="post">
              <input name="password" type="password" placeholder="New password" required>
              <input name="password2" type="password" placeholder="Confirm password" required>
              <button>Update password</button>
            </form>
            <p><a href="{{ url_for('login') }}">Back to login</a></p>
        """)


@app.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return render_template_string("""
            <!doctype html><title>Home</title>
            <h1>Home (fallback)</h1>
            {% if current_user %}
              <p>Logged in as: {{ current_user.email }}</p>
            {% else %}
              <p>You are not signed in.</p>
            {% endif %}
            <p><a href="{{ url_for('logout') }}">Logout</a></p>
        """)


@app.route("/account", methods=["GET", "POST"])
@require_login
def account():
    user = g.current_user

    if request.method == "POST":
        form_name = request.form.get("form_name") or ""

        if form_name == "update_email":
            new_email = normalize_ws(request.form.get("new_email") or "").lower()
            current_password = request.form.get("current_password") or ""
            if not new_email:
                flash("Email cannot be empty.", "error")
            elif authenticate_user(user['email'], current_password) is None:
                flash("Current password is incorrect.", "error")
            else:
                try:
                    update_user_email(user['id'], new_email)
                    refreshed = get_user_by_id(user['id'])
                    if refreshed:
                        login_user_session(refreshed)
                        g.current_user = refreshed
                    flash("Email updated.", "success")
                except EmailInUseError:
                    flash("That email is already registered.", "error")
                except Exception as exc:
                    flash(f"Failed to update email: {exc}", "error")

        elif form_name == "update_password":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""
            if authenticate_user(user['email'], current_password) is None:
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters long.", "error")
            elif new_password != confirm_password:
                flash("Confirmation password does not match.", "error")
            else:
                set_user_password(user['id'], new_password)
                mark_user_login(user['id'])
                flash("Password updated successfully.", "success")
        else:
            flash("Unknown action submitted.", "error")

    try:
        return render_template("account.html")
    except Exception:
        return render_template_string("""
            <!doctype html><title>My Account</title>
            <h1>My Account</h1>
            <p>Email: {{ current_user.email }}</p>
        """)


@app.route("/invoice", methods=["GET"])
@require_login
def invoice_form():
    case_year = (request.args.get("year") or "").strip()
    case_month = (request.args.get("month") or "").strip()
    case_name = (request.args.get("case") or "").strip()
    case_context = None
    if case_year and case_month and case_name:
        case_context = {
            "year": case_year,
            "month": case_month,
            "case": case_name,
            "label": f"{case_year} / {case_month} — {case_name}",
        }
    try:
        return render_template("invoice.html", case_context=case_context)
    except Exception:
        return render_template_string("""
            <!doctype html><title>Generate Invoice</title>
            <h1>Invoice Generator</h1>
            <p>Unable to load the styled template. Please contact support.</p>
        """)


@app.post("/invoice/save")
@require_login
def invoice_save():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "msg": "Invalid invoice payload."}), 400
    case_year = (payload.get("case_year") or "").strip()
    case_month = (payload.get("case_month") or "").strip()
    case_name = (payload.get("case_name") or "").strip()
    requested_number = str(payload.get("invoice_number") or "").strip()

    invoice_data: Dict[str, Any] = {
        "invoice_number": requested_number,
        "invoice_date": str(payload.get("invoice_date") or "").strip()[:120],
        "client_name": str(payload.get("client_name") or "").strip()[:180],
        "issuer_lines": _clean_lines(payload.get("issuer_lines") or []),
        "recipient_lines": _clean_lines(payload.get("recipient_lines") or []),
        "generated_at": payload.get("generated_at") or datetime.utcnow().isoformat(),
    }

    two_places = Decimal("0.01")

    def _as_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
        if value in (None, "", False):
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return default

    items: list[dict[str, Any]] = []
    computed_total = Decimal("0")
    for row in payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        amount_decimal = _as_decimal(row.get("amount"))
        computed_total += amount_decimal
        cleaned = {
            "sn": str(row.get("sn") or "").strip()[:40],
            "item": str(row.get("item") or "").strip()[:160],
            "description": str(row.get("description") or "").strip()[:600],
            "amount": str(amount_decimal.quantize(two_places)),
        }
        if any(cleaned.values()):
            items.append(cleaned)
    invoice_data["items"] = items

    requested_total = _as_decimal(payload.get("total"), computed_total if items else Decimal("0"))
    if items and requested_total == Decimal("0") and computed_total > Decimal("0"):
        requested_total = computed_total
    invoice_data["total"] = str(requested_total.quantize(two_places))

    conn = get_app_db()
    user_id = g.current_user["id"] if g.get("current_user") else None
    pdf_bytes: Optional[bytes] = None
    final_number: Optional[str] = None
    primary_path: Optional[Path] = None
    case_copy_path: Optional[Path] = None
    relative_path = ""
    auto_assigned = False

    try:
        with conn:
            if requested_number:
                final_number = requested_number
                existing = conn.execute(
                    "SELECT 1 FROM invoices WHERE invoice_number = ?",
                    (final_number,),
                ).fetchone()
                if existing:
                    raise InvoiceNumberConflict
            else:
                final_number = _reserve_invoice_number(conn)
                auto_assigned = True

            invoice_data["invoice_number"] = final_number

            try:
                pdf_buffer, _ = generate_invoice_pdf(invoice_data)
            except RuntimeError as exc:
                raise RuntimeError(str(exc)) from exc
            except Exception as exc:
                raise InvoiceStorageError(f"Failed to render invoice: {exc}") from exc

            pdf_bytes = pdf_buffer.getvalue()
            primary_path, case_copy_path = _invoice_target_path(
                final_number, case_year, case_month, case_name
            )
            try:
                primary_path.write_bytes(pdf_bytes)
                if case_copy_path:
                    case_copy_path.write_bytes(pdf_bytes)
            except OSError as exc:
                with suppress(FileNotFoundError):
                    primary_path.unlink()
                if case_copy_path:
                    with suppress(FileNotFoundError):
                        case_copy_path.unlink()
                raise InvoiceStorageError(f"Unable to write invoice PDF: {exc}") from exc

            try:
                relative_path = (
                    str(primary_path.relative_to(FS_ROOT))
                    if FS_ROOT
                    else str(primary_path)
                )
            except Exception:
                relative_path = str(primary_path)

            payload_for_record = dict(invoice_data)
            payload_for_record.update(
                {
                    "case_year": case_year,
                    "case_month": case_month,
                    "case_name": case_name,
                }
            )
            payload_json = json.dumps(payload_for_record, ensure_ascii=False)

            try:
                _insert_invoice_row(
                    conn,
                    final_number,
                    case_year,
                    case_month,
                    case_name,
                    relative_path,
                    payload_json,
                    user_id,
                )
            except InvoiceNumberConflict:
                with suppress(FileNotFoundError):
                    primary_path.unlink()
                if case_copy_path:
                    with suppress(FileNotFoundError):
                        case_copy_path.unlink()
                raise

            if not auto_assigned:
                used_int = _parse_invoice_number(final_number)
                _ensure_counter_after_use(conn, used_int)
    except InvoiceNumberConflict:
        return jsonify({"ok": False, "msg": "Invoice number already exists."}), 409
    except RuntimeError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 503
    except InvoiceStorageError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"ok": False, "msg": f"Failed to save invoice: {exc}"}), 500

    if pdf_bytes is None or primary_path is None or final_number is None:
        return jsonify({"ok": False, "msg": "Invoice could not be generated."}), 500

    response = send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=primary_path.name,
    )
    response.headers["X-Invoice-Number"] = final_number
    if relative_path:
        response.headers["X-Invoice-Path"] = relative_path
    return response



@app.route("/messages", methods=["GET", "POST"])
@require_login
def messages_home():
    user = g.current_user
    draft = {
        "recipient_id": "",
        "subject": "",
        "body": "",
    }
    tab = (request.args.get("tab") or "inbox").lower()
    if tab not in {"inbox", "sent"}:
        tab = "inbox"

    if request.method == "POST":
        form_name = (request.form.get("form_name") or "").strip()
        if form_name == "send_message":
            draft["recipient_id"] = (request.form.get("recipient_id") or "").strip()
            draft["subject"] = (request.form.get("subject") or "").strip()
            draft["body"] = request.form.get("body") or ""

            try:
                recipient_id = int(draft["recipient_id"])
            except (TypeError, ValueError):
                recipient_id = 0

            subject = normalize_ws(draft["subject"])
            body = draft["body"].strip()

            if recipient_id <= 0:
                flash("Select a recipient.", "error")
            elif recipient_id == user['id']:
                flash("You cannot send a message to yourself.", "error")
            elif not body:
                flash("Message body cannot be empty.", "error")
            else:
                recipient = get_user_by_id(recipient_id)
                if not recipient or not recipient['is_active']:
                    flash("Recipient not found or inactive.", "error")
                else:
                    try:
                        message_id = create_message(user['id'], recipient_id, subject, body)
                    except Exception as exc:
                        flash(f"Unable to send message: {exc}", "error")
                    else:
                        try:
                            message_url = url_for("message_detail", message_id=message_id, _external=True)
                        except RuntimeError:
                            message_url = url_for("message_detail", message_id=message_id)

                        email_subject = subject or "You have a new message"
                        email_subject = f"New message in Case Organizer - {email_subject}" if subject else "New message in Case Organizer"
                        email_body_parts = [
                            f"You have received a new message from {user['email']}.",
                        ]
                        if subject:
                            email_body_parts.append(f"Subject: {subject}")
                        email_body_parts.extend(
                            [
                                "",
                                f"Read it here: {message_url}",
                                "",
                                "This is an automated notification from Case Organizer.",
                            ]
                        )
                        email_body = "\n".join(email_body_parts)

                        try:
                            future = send_email_async(recipient['email'], email_subject, email_body)
                        except EmailConfigError as exc:
                            app.logger.warning("Email notification skipped: %s", exc)
                            flash("Message sent, but notification email could not be delivered (email not configured).", "warning")
                        except Exception as exc:
                            app.logger.exception("Failed to queue notification email: %s", exc)
                            flash("Message sent, but notification email could not be queued.", "warning")
                        else:
                            try:
                                future.result(timeout=1.0)
                            except FutureTimeout:
                                app.logger.info("Notification email queued for message %s", message_id)
                            except Exception as exc:
                                app.logger.exception("Failed to send notification email: %s", exc)
                                flash("Message sent, but notification email failed to send.", "warning")

                        flash("Message sent.", "success")
                        return redirect(url_for("messages_home", tab="sent"))
        else:
            flash("Unknown action submitted.", "error")

    inbox_rows = list_inbox(user['id'])
    sent_rows = list_sent(user['id'])
    recipients = [u for u in list_users() if u['is_active'] and u['id'] != user['id']]
    smtp_locked = False

    try:
        return render_template(
            "messages.html",
            tab=tab,
            inbox=inbox_rows,
            sent=sent_rows,
            recipients=recipients,
            draft=draft,
            smtp_locked=smtp_locked,
        )
    except Exception:
        return render_template_string(
            """
            <!doctype html><title>Messages</title>
            <h1>Messages</h1>
            <p>This feature requires HTML templates. Inbox entries: {{ inbox|length }}</p>
            """,
            tab=tab,
            inbox=inbox_rows,
            sent=sent_rows,
            recipients=recipients,
            draft=draft,
            smtp_locked=smtp_locked,
        )


@app.route("/messages/<int:message_id>")
@require_login
def message_detail(message_id: int):
    user = g.current_user
    row = get_message(message_id, user['id'])
    if not row:
        flash("Message not found.", "error")
        return redirect(url_for("messages_home"))

    message = dict(row)
    if message['recipient_id'] == user['id'] and not message['is_read']:
        mark_message_read(message_id, user['id'])
        message['is_read'] = 1
        try:
            g.unread_count = count_unread(user['id'])
        except Exception:
            pass

    try:
        return render_template("message_detail.html", message=message)
    except Exception:
        return render_template_string(
            """
            <!doctype html><title>Message</title>
            <h1>{{ message.subject or 'No subject' }}</h1>
            <p>From: {{ message.sender_email }} | To: {{ message.recipient_email }}</p>
            <pre>{{ message.body }}</pre>
            """,
            message=message,
        )


@app.post("/messages/<int:message_id>/delete")
@require_login
def delete_message_action(message_id: int):
    user = g.current_user
    tab = (request.form.get("current_tab") or "inbox").lower()
    if tab not in {"inbox", "sent"}:
        tab = "inbox"

    try:
        removed = delete_message(message_id, user['id'])
    except Exception as exc:
        flash(f"Unable to delete message: {exc}", "error")
    else:
        if removed:
            flash("Message deleted.", "success")
        else:
            flash("Message not found or cannot be deleted.", "error")

    return redirect(url_for("messages_home", tab=tab))


@app.route("/settings", methods=["GET", "POST"])
@require_admin
def admin_settings():
    global FS_ROOT

    if request.method == "POST":
        form_name = request.form.get("form_name") or ""

        if form_name == "fs_root":
            path_input = (request.form.get("fs_root") or "").strip()
            if not path_input:
                flash("Storage path is required.", "error")
            else:
                try:
                    new_path = Path(path_input).expanduser().resolve()
                    new_path.mkdir(parents=True, exist_ok=True)
                    config.save_fs_root(str(new_path))
                    FS_ROOT = new_path
                    flash("Storage location updated.", "success")
                except Exception as exc:
                    flash(f"Failed to update storage path: {exc}", "error")

        elif form_name == "smtp":
            host = (request.form.get("smtp_host") or "").strip()
            port_raw = request.form.get("smtp_port") or ""
            username = (request.form.get("smtp_username") or "").strip()
            password = request.form.get("smtp_password") or ""
            use_tls = request.form.get("smtp_use_tls") in {"1", "true", "on"}
            from_email = (request.form.get("smtp_from_email") or "").strip()

            errors = []
            if not host:
                errors.append("SMTP host is required.")
            try:
                port = int(port_raw) if port_raw else 0
                if port <= 0:
                    raise ValueError
            except ValueError:
                errors.append("SMTP port must be a positive number.")
                port = None
            if not from_email:
                errors.append("From-address is required.")

            if errors:
                for err in errors:
                    flash(err, "error")
            else:
                settings_manager.set("smtp_host", host)
                settings_manager.set("smtp_port", port)
                settings_manager.set("smtp_username", username)
                settings_manager.set("smtp_use_tls", use_tls)
                settings_manager.set("smtp_from_email", from_email)
                if password:
                    try:
                        settings_manager.set_secret("smtp_password", password)
                    except RuntimeError:
                        settings_manager.set("smtp_password", password)
                        flash("SMTP password saved without encryption; set CASEORG_SECRET_KEY for secure storage.", "warning")
                clear_email_cache()
                flash("SMTP configuration updated.", "success")

        elif form_name == "create_user":
            email = normalize_ws(request.form.get("user_email") or "")
            role = (request.form.get("user_role") or "user").lower()
            password = request.form.get("user_password") or ""

            if role not in {"admin", "user"}:
                flash("Invalid role specified.", "error")
            elif len(password) < 8:
                flash("User password must be at least 8 characters long.", "error")
            else:
                try:
                    create_user(email, password, role=role)
                    flash(f"User {email} created.", "success")
                except UserExistsError:
                    flash("A user with that email already exists.", "error")
                except Exception as exc:
                    flash(f"Failed to create user: {exc}", "error")

        elif form_name == "toggle_user":
            try:
                target_id = int(request.form.get("user_id", "0"))
            except ValueError:
                target_id = 0
            new_state = request.form.get("new_state") == "1"
            target_user = get_user_by_id(target_id)
            if not target_user:
                flash("User not found.", "error")
            elif target_user['id'] == g.current_user['id'] and not new_state:
                flash("You cannot deactivate your own account.", "error")
            elif target_user['role'] == 'admin' and not new_state and count_admins(active_only=True) <= 1:
                flash("At least one active administrator must remain.", "error")
            else:
                try:
                    set_user_active(target_id, new_state)
                    flash("User status updated.", "success")
                except Exception as exc:
                    flash(f"Failed to update user status: {exc}", "error")

        elif form_name == "update_user":
            try:
                target_id = int(request.form.get("user_id", "0"))
            except ValueError:
                target_id = 0
            target_user = get_user_by_id(target_id)
            if not target_user:
                flash("User not found.", "error")
            else:
                new_email = normalize_ws(request.form.get("new_email") or "").lower()
                new_role = (request.form.get("new_role") or target_user['role']).lower()
                if new_role not in {"admin", "user"}:
                    flash("Invalid role selected.", "error")
                else:
                    changes_made = False
                    try:
                        if new_email and new_email != target_user['email']:
                            update_user_email(target_id, new_email)
                            changes_made = True
                        if new_role != target_user['role']:
                            if target_user['role'] == 'admin' and new_role != 'admin' and count_admins(active_only=True) <= 1:
                                flash("At least one administrator must remain.", "error")
                            else:
                                update_user_role(target_id, new_role)
                                changes_made = True
                        if changes_made:
                            flash("User details updated.", "success")
                        else:
                            flash("No changes detected.", "info")
                    except EmailInUseError:
                        flash("That email is already in use.", "error")
                    except Exception as exc:
                        flash(f"Failed to update user: {exc}", "error")

        elif form_name == "reset_password_user":
            try:
                target_id = int(request.form.get("user_id", "0"))
            except ValueError:
                target_id = 0
            target_user = get_user_by_id(target_id)
            if not target_user or not target_user['is_active']:
                flash("Only active users can receive reset emails.", "error")
            else:
                try:
                    token = create_password_reset_token(target_user['id'])
                    reset_link = url_for("reset_password", token=token, _external=True)
                    body = (
                        "Hello,\n\n"
                        "An administrator initiated a password reset for your Case Organizer account.\n"
                        f"Use the link below to set a new password: {reset_link}\n\n"
                        "If you were not expecting this, please contact the administrator immediately."
                    )
                    future = send_email_async(target_user['email'], "Case Organizer password reset", body)
                except EmailConfigError:
                    flash("SMTP settings are incomplete. Configure email before sending resets.", "error")
                except Exception as exc:
                    flash(f"Failed to queue reset email: {exc}", "error")
                else:
                    try:
                        future.result(timeout=1.0)
                    except FutureTimeout:
                        app.logger.info("Admin-triggered reset email queued for %s", target_user['email'])
                        flash("Password reset email queued.", "success")
                    except Exception as exc:
                        app.logger.exception("Failed to send reset email: %s", exc)
                        flash(f"Failed to send reset email: {exc}", "error")
                    else:
                        flash("Password reset email sent.", "success")

        else:
            flash("Unknown action submitted.", "error")

    fs_value = str(FS_ROOT) if FS_ROOT else (config.FS_ROOT or "")

    smtp_config = {
        "host": settings_manager.get("smtp_host", ""),
        "port": settings_manager.get("smtp_port", 587),
        "username": settings_manager.get("smtp_username", ""),
        "use_tls": bool(settings_manager.get("smtp_use_tls", True)),
        "from_email": settings_manager.get("smtp_from_email", ""),
    }

    try:
        smtp_password_configured = bool(settings_manager.get_secret("smtp_password"))
    except RuntimeError:
        smtp_password_configured = bool(settings_manager.get("smtp_password"))

    users = list_users()
    active_admins = [u for u in users if u['role'] == 'admin' and u['is_active']]
    last_active_admin_id = active_admins[0]['id'] if len(active_admins) == 1 else None

    try:
        return render_template(
            "settings.html",
            fs_root=fs_value,
            smtp=smtp_config,
            smtp_password_configured=smtp_password_configured,
            users=users,
            last_active_admin_id=last_active_admin_id,
        )
    except Exception:
        return render_template_string("""
            <!doctype html><title>Settings</title>
            <h1>Admin Settings (fallback)</h1>
            <p>Storage root: {{ fs_root }}</p>
            <p>SMTP host: {{ smtp.host }}</p>
        """, fs_root=fs_value, smtp=smtp_config)

# ---- Create Case --------------------------------------------------------
@app.post("/create-case")
def create_case():
    form = request.form

    # Parties (authoritative for Case Name)
    pn = normalize_ws(form.get("Petitioner Name"))
    rn = normalize_ws(form.get("Respondent Name"))
    case_name = normalize_ws(form.get("Case Name"))  # UI may send it; we recompute to be safe
    auto_case_name = build_case_name_from_parties(pn, rn)
    if not auto_case_name:
        return jsonify({"ok": False, "msg": "Petitioner Name and Respondent Name are required to form Case Name."}), 400
    if not case_name:
        case_name = auto_case_name

    # Date (YYYY-MM-DD) or today
    date_str = normalize_ws(form.get("Date"))
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid Date. Use YYYY-MM-DD."}), 400

    year = int(dt.strftime("%Y"))
    month = month_dir_name(dt)
    cdir = case_dir(year, month, case_name)
    cdir.mkdir(parents=True, exist_ok=True)

    # Note.json payload (Title Case keys with spaces)
    fields = [
        "Petitioner Name", "Petitioner Address", "Petitioner Contact",
        "Respondent Name", "Respondent Address", "Respondent Contact",
        "Our Party", "Case Category", "Case Subcategory", "Case Type",
        "Origin State", "Origin District", "Origin Court/Forum",
        "Current State", "Current District", "Current Court/Forum",
        "Additional Notes",
    ]
    payload = {k: form.get(k, "") for k in fields}
    # Ensure consistency with Case Name used
    payload["Petitioner Name"] = pn
    payload["Respondent Name"] = rn

    note_text = make_note_json(payload)
    (cdir / "Note.json").write_text(note_text, encoding="utf-8")

    return jsonify({"ok": True, "path": str(cdir)})

# ---- Manage Case (Upload, Copy & Rename) --------------------------------

@app.post("/manage-case/upload")
@require_login_api
def manage_case_upload():
    form = request.form

    # Locate existing case folder by Year + Month + Case Name
    year_sel  = (form.get("Year") or "").strip()
    month_sel = (form.get("Month") or "").strip()
    case_name = normalize_ws(form.get("Case Name"))
    if not (year_sel and month_sel and case_name):
        return jsonify({"ok": False, "msg": "Year, Month, and Case Name are required."}), 400

    # Classification that influences filename
    domain      = normalize_ws(form.get("Domain"))        # Criminal / Civil / Commercial / Case Law
    subcategory = normalize_ws(form.get("Subcategory"))   # optional subfolder
    main_type   = normalize_ws(form.get("Main Type"))     # OPTIONAL now

    if not domain:
        return jsonify({"ok": False, "msg": "Case Category (Domain) is required."}), 400

    # Date used for filename (only in the 'typed' scheme)
    date_str = normalize_ws(form.get("Date"))
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid Date. Use YYYY-MM-DD."}), 401

    # Accept MULTIPLE files
    files = request.files.getlist("file")
    if not files:
        return jsonify({"ok": False, "msg": "No files provided."}), 400

    cdir = FS_ROOT / year_sel / month_sel / case_name
    if not cdir.exists():
        return jsonify({"ok": False, "msg": "Case directory does not exist. Create the case first."}), 400

    # Helper: safe original base (without extension)
    def safe_stem(filename: str) -> str:
        base = Path(secure_filename(filename)).stem
        return re.sub(r"\s+", " ", base).strip()

    saved_paths = []

    # ---------- NEW: Case Law handling ----------
    if domain.lower() == "case law":
        target_dir = cdir / "Case Laws"
        target_dir.mkdir(parents=True, exist_ok=True)

        for f in files:
            if not f or f.filename == "":
                continue
            if not allowed_file(f.filename):
                continue

            ext = f.filename.rsplit(".", 1)[1].lower()

            # Filename = Main Type (as typed) OR fallback to original stem
            base = (main_type or "").strip() or safe_stem(f.filename)
            # sanitize whitespace
            base = re.sub(r"\s+", " ", base).strip()
            new_name = f"{base}.{ext}"

            tmp = target_dir / secure_filename(f"_upload_{datetime.now().timestamp()}_{secure_filename(f.filename)}")
            f.save(tmp)
            dest = target_dir / new_name

            final_dest = dest
            counter = 1
            while final_dest.exists():
                final_dest = target_dir / (dest.stem + f"_{counter}" + dest.suffix)
                counter += 1

            shutil.copyfile(tmp, final_dest)
            tmp.unlink(missing_ok=True)
            saved_paths.append(str(final_dest))

        if not saved_paths:
            return jsonify({"ok": False, "msg": "No files were saved (unsupported type?)"}), 400

        return jsonify({"ok": True, "saved_as": saved_paths})
    # ---------- END Case Law handling ----------

    # Regular categories (Criminal/Civil/Commercial)
    target_dir = cdir / subcategory if subcategory else cdir
    target_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        if not f or f.filename == "":
            continue
        if not allowed_file(f.filename):
            continue

        ext = f.filename.rsplit(".", 1)[1].lower()

        # Naming rules:
        # - If subcategory is "Primary Documents" OR main_type is empty => keep original name, append " - {Case Name}"
        # - Else => use the typed scheme "(DDMMYYYY) TYPE DOMAIN CaseName.ext"
        is_primary_docs = subcategory and subcategory.strip().lower() == "primary documents"
        if is_primary_docs or not main_type:
            base = safe_stem(f.filename)
            new_name = f"{base} - {case_name}.{ext}"
        else:
            new_name = build_filename(dt, main_type, domain, case_name, ext)

        tmp = target_dir / secure_filename(f"_upload_{datetime.now().timestamp()}_{secure_filename(f.filename)}")
        f.save(tmp)
        dest = target_dir / new_name

        final_dest = dest
        counter = 1
        while final_dest.exists():
            final_dest = target_dir / (dest.stem + f"_{counter}" + dest.suffix)
            counter += 1

        shutil.copyfile(tmp, final_dest)
        tmp.unlink(missing_ok=True)
        saved_paths.append(str(final_dest))

    if not saved_paths:
        return jsonify({"ok": False, "msg": "No files were saved (unsupported type?)"}), 400

    return jsonify({"ok": True, "saved_as": saved_paths})

# ---- Safe file serving (whitelist FS_ROOT) ------------------------------

@app.get("/static-serve")
def static_serve():
    raw = request.args.get("path", "")
    download = request.args.get("download") in {"1", "true", "yes"}
    try:
        path = Path(raw).resolve(strict=True)
    except Exception:
        return "Not found", 404
    root = FS_ROOT.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return "Not found", 404
    resp = send_file(path, as_attachment=download, conditional=True)
    resp.cache_control.public = True
    resp.cache_control.max_age = STATIC_MAX_AGE
    resp.expires = datetime.utcnow() + timedelta(seconds=STATIC_MAX_AGE)
    return resp

# ---- Search -------------------------------------------------------------

@app.get("/search")
def search():
    """
    Query params:
      q: free text (matches relative path)
      year: '2025'
      month: 'Jan' | 'Feb' | ...
      party: fragment to match in Case folder name (Petitioner v. Respondent)
      domain: 'Criminal' | 'Civil' | 'Commercial'
      subcategory: subfolder name e.g. 'Transfer Petitions', 'Orders/Judgments', 'Primary Documents'
      type: ignored for folder-driven search (still accepted but not required)
    Behavior:
      - If domain is given but subcategory is empty => return empty result set (force specificity).
      - If subcategory is provided => enumerate case dirs and list files found under that subfolder only.
      - Otherwise (no domain/subcategory) => fallback to broad scan with q/party/year/month filters.
    """
    q        = normalize_ws(request.args.get("q"))
    year     = normalize_ws(request.args.get("year"))
    month    = normalize_ws(request.args.get("month"))
    party    = normalize_ws(request.args.get("party"))
    domain   = normalize_ws(request.args.get("domain"))        # used only to require subcat if provided
    subcat   = normalize_ws(request.args.get("subcategory"))
    # ftype kept for backward compat but not used in folder mode
    # ftype    = normalize_ws(request.args.get("type"))

    results = []
    if not FS_ROOT.exists():
        return jsonify({"results": results})

    # Helper: yield candidate month directories given year/month filters
    def month_dirs():
        root = FS_ROOT
        years = [FS_ROOT / year] if year else [d for d in root.iterdir() if d.is_dir()]
        for y in years:
            if not y.is_dir():
                continue
            months = [y / month] if month else [d for d in y.iterdir() if d.is_dir()]
            for m in months:
                if m.is_dir():
                    yield m  # e.g., fs-files/2025/Jan

    # HARD RULE: if domain is given but subcategory is missing -> force empty
    if domain and not subcat:
        return jsonify({"results": []})

    # FOLDER-DRIVEN SEARCH when subcategory is present
    if subcat:
        subcat_lower = subcat.lower()

        for mdir in month_dirs():
            # case directories: fs-files/YYYY/Mon/<Case Name>
            for case_dir_path in mdir.iterdir():
                if not case_dir_path.is_dir():
                    continue

                case_name = case_dir_path.name  # "Petitioner v. Respondent"

                # party filter against case folder name
                if party and party.lower() not in case_name.lower():
                    continue

                # locate a child directory whose name matches subcategory (case-insensitive)
                target = None
                for child in case_dir_path.iterdir():
                    if child.is_dir() and child.name.lower() == subcat_lower:
                        target = child
                        break
                if target is None:
                    continue  # this case has no such subcategory folder

                # list allowed files inside that subcategory folder (non-recursive)
                for name in sorted(os.listdir(target)):
                    p = target / name
                    if not p.is_file():
                        continue
                    if "." not in name:
                        continue
                    ext = name.rsplit(".", 1)[1].lower()
                    if ext not in ALLOWED_EXTENSIONS:
                        continue

                    rel = p.relative_to(FS_ROOT)
                    # optional q filter against relative path text
                    if q and (q.lower() not in str(rel).lower()):
                        continue

                    results.append({
                        "file": name,
                        "path": str(p),
                        "rel":  str(rel),
                    })

        return jsonify({"results": results})

    # FALLBACK: no subcategory provided -> optional broad search
    # (Only if user didn't specify domain; if domain is provided we already early-returned empty)
    for root, dirs, files in os.walk(FS_ROOT):
        # Apply year/month filters by relative path segments
        try:
            rel = Path(root).relative_to(FS_ROOT)
            parts = rel.parts  # e.g., ('2025','Jan','Case Name', 'Some Subdir'...)
        except Exception:
            parts = ()

        if year and (len(parts) < 1 or parts[0] != year):
            continue
        if month and (len(parts) < 2 or parts[1] != month):
            continue

        # party filter checks the Case Name when available (3rd segment)
        if party:
            case_seg = parts[2] if len(parts) >= 3 else ""
            if party.lower() not in case_seg.lower():
                continue

        for name in files:
            if "." not in name:
                continue
            ext = name.rsplit(".", 1)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            p = Path(root) / name
            rel_file = p.relative_to(FS_ROOT)

            if q and (q.lower() not in str(rel_file).lower()):
                continue

            results.append({
                "file": name,
                "path": str(p),
                "rel":  str(rel_file),
            })

    return jsonify({"results": results})

# ---- delete-file --------------------------------


@app.post("/api/delete-file")
@require_admin_api
def api_delete_file():
    """
    Delete a file under FS_ROOT, given JSON:
      {"path": "/full/path/inside/FS_ROOT/.."}
    """
    try:
        data = request.get_json(silent=True) or {}
        raw = (data.get("path") or "").strip()
        if not raw:
            return jsonify({"ok": False, "msg": "Missing 'path'"}), 400

        target = Path(raw).resolve(strict=True)
        root = FS_ROOT.resolve()
        if not str(target).startswith(str(root)):
            return jsonify({"ok": False, "msg": "Not found"}), 404
        if not target.is_file():
            return jsonify({"ok": False, "msg": "Not a file"}), 400

        target.unlink()
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "File not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Delete failed: {e}"}), 500


# ---- Case Law Upload & Search -------------------------------------------


@app.post("/case-law/upload")
@require_login_api
def case_law_upload():
    if not FS_ROOT:
        return case_law_error("Storage root is not configured yet.")

    ensure_root()

    form = request.form
    petitioner = normalize_ws(form.get("petitioner") or "")
    respondent = normalize_ws(form.get("respondent") or "")
    citation = normalize_ws(form.get("citation") or "")
    decision_year_raw = normalize_ws(form.get("decision_year") or "")
    primary_raw = normalize_ws(form.get("primary_type") or "")
    case_type_raw = normalize_ws(form.get("case_type") or form.get("subtype") or "")
    note_text = (form.get("note") or "").strip()

    if not petitioner:
        return case_law_error("Petitioner name is required.")
    if not respondent:
        return case_law_error("Respondent name is required.")
    if not citation:
        return case_law_error("Citation is required.")

    try:
        decision_year = int(decision_year_raw)
    except ValueError:
        return case_law_error("Decision year must be a number.")

    current_year = datetime.now().year
    if decision_year < 1800 or decision_year > current_year + 1:
        return case_law_error("Decision year looks invalid.")

    decision_month = ""

    primary = normalize_primary_type(primary_raw)
    if not primary:
        return case_law_error("Primary classification must be Civil, Criminal, or Commercial.")

    case_type = normalize_case_type(primary, case_type_raw)
    if not case_type:
        return case_law_error("Please select a valid case type for the chosen classification.")

    if not note_text:
        return case_law_error("An additional note is required for case law entries.")

    upload = request.files.get("file")
    if not upload or upload.filename == "":
        return case_law_error("Attach the judgment file to upload.")

    if "." not in upload.filename:
        return case_law_error("The uploaded file must include an extension.")

    ext = upload.filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return case_law_error(f"File type '.{ext}' is not allowed.")

    conn = get_case_law_db()
    existing = conn.execute(
        """
        SELECT id FROM case_law
        WHERE petitioner = ? AND respondent = ? AND citation = ?
          AND primary_type = ? AND subtype = ? AND decision_year = ?
        """,
        (petitioner, respondent, citation, primary, case_type, decision_year),
    ).fetchone()
    if existing:
        return case_law_error("A case law record with the same metadata already exists.", 409)

    display_name = build_case_law_display_name(petitioner, respondent, citation)
    safe_case_name = sanitize_case_law_component(display_name) or f"Case Law {decision_year}"

    case_law_root = _case_law_root()
    primary_segment = sanitize_case_law_component(primary, replacement="-") or "General"
    type_segment = sanitize_case_law_component(case_type, replacement="-") or "General"
    base_dir = case_law_root / primary_segment / type_segment / str(decision_year)
    base_dir.mkdir(parents=True, exist_ok=True)

    case_dir = ensure_unique_path(base_dir / safe_case_name)
    case_dir.mkdir(exist_ok=False)

    tmp_name = secure_filename(f"upload_{datetime.now().timestamp()}_{upload.filename}")
    tmp_path = case_dir / tmp_name
    upload.save(tmp_path)

    target_file = case_dir / f"{safe_case_name}.{ext}"
    target_file = ensure_unique_path(target_file)
    tmp_path.rename(target_file)

    note_payload = {
        "Petitioner": petitioner,
        "Respondent": respondent,
        "Citation": citation,
        "Decision Year": decision_year,
        "Primary Type": primary,
        "Case Type": case_type,
        "Note": note_text,
        "Saved At": datetime.now().isoformat(timespec="seconds"),
    }
    note_json = json.dumps(note_payload, indent=2)

    note_file = case_dir / "note.json"
    note_file.write_text(note_json, encoding="utf-8")

    judgement_text = extract_text_for_index(target_file)
    folder_rel = str(case_dir.relative_to(FS_ROOT))
    note_rel = str(note_file.relative_to(FS_ROOT))

    try:
        cur = conn.execute(
            """
            INSERT INTO case_law (
                petitioner, respondent, citation, decision_year, decision_month,
                primary_type, subtype, folder_rel, file_name, note_path_rel, note_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                petitioner,
                respondent,
                citation,
                decision_year,
                decision_month,
                primary,
                case_type,
                folder_rel,
                target_file.name,
                note_rel,
                note_text,
            ),
        )
        case_id = cur.lastrowid
        refresh_case_law_index(
            conn,
            case_id,
            judgement_text,
            petitioner,
            respondent,
            citation,
            note_json,
        )
        conn.commit()
    except Exception as exc:
        shutil.rmtree(case_dir, ignore_errors=True)
        raise exc

    return jsonify({
        "ok": True,
        "case_id": case_id,
        "folder": folder_rel,
        "file": target_file.name,
        "note": note_rel,
    })


@app.get("/case-law/search")
def case_law_search():
    if not FS_ROOT:
        return jsonify({"results": [], "filters": {}})

    conn = get_case_law_db()
    params: list[Any] = []
    where: list[str] = []
    join_fts = False

    text_query_raw = request.args.get("text") or ""
    text_query = normalize_ws(text_query_raw)
    if text_query:
        fts_query = normalize_boolean_query(text_query)
        if not fts_query:
            return jsonify({"results": []})
        join_fts = True
        where.append("c.id IN (SELECT rowid FROM case_law_fts WHERE case_law_fts MATCH ?)")
        params.append(fts_query)

    party_raw = normalize_ws(request.args.get("party") or "")
    party_mode = normalize_ws(request.args.get("party_mode") or "either")
    if party_raw:
        like = f"%{party_raw.lower()}%"
        if party_mode == "petitioner":
            where.append("LOWER(c.petitioner) LIKE ?")
            params.append(like)
        elif party_mode == "respondent":
            where.append("LOWER(c.respondent) LIKE ?")
            params.append(like)
        else:
            where.append("(LOWER(c.petitioner) LIKE ? OR LOWER(c.respondent) LIKE ?)")
            params.extend([like, like])

    citation_raw = normalize_ws(request.args.get("citation") or "")
    if citation_raw:
        where.append("LOWER(c.citation) LIKE ?")
        params.append(f"%{citation_raw.lower()}%")

    year_raw = normalize_ws(request.args.get("year") or "")
    if year_raw:
        try:
            year_val = int(year_raw)
        except ValueError:
            return jsonify({"results": [], "error": "Invalid year filter supplied."}), 400
        where.append("c.decision_year = ?")
        params.append(year_val)

    primary_raw = normalize_ws(request.args.get("primary_type") or "")
    if primary_raw:
        primary = normalize_primary_type(primary_raw)
        if not primary:
            return jsonify({"results": [], "error": "Invalid primary classification."}), 400
        where.append("c.primary_type = ?")
        params.append(primary)

    case_type_raw = normalize_ws(request.args.get("case_type") or "")
    if case_type_raw and primary_raw:
        primary = normalize_primary_type(primary_raw)
        case_type = normalize_case_type(primary or "", case_type_raw)
        if not case_type:
            return jsonify({"results": [], "error": "Invalid case type supplied."}), 400
        where.append("c.subtype = ?")
        params.append(case_type)

    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    select_fields = [
        "c.id",
        "c.petitioner",
        "c.respondent",
        "c.citation",
        "c.decision_year",
        "c.decision_month",
        "c.primary_type",
        "c.subtype AS case_type",
        "c.folder_rel",
        "c.file_name",
        "c.note_path_rel",
        "c.note_text",
        "c.created_at",
        "c.updated_at",
    ]

    select_fields.append("'' AS fts_content")

    sql = "SELECT " + ", ".join(select_fields) + " FROM case_law c"

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " ORDER BY c.decision_year DESC, c.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    results = [
        serialize_case_law(row, row["fts_content"]) for row in rows
    ]

    years = [r[0] for r in conn.execute("SELECT DISTINCT decision_year FROM case_law ORDER BY decision_year DESC").fetchall()]
    return jsonify({
        "results": results,
        "filters": {
            "years": years,
            "primary_types": list(CASE_LAW_PRIMARY_TYPES),
            "case_types": CASE_LAW_CASE_TYPES,
        },
    })


@app.delete("/case-law/<int:case_id>")
@require_admin_api
def case_law_delete(case_id: int):
    if not FS_ROOT:
        return case_law_error("Storage root is not configured yet.")

    conn = get_case_law_db()
    row = fetch_case_law_record(conn, case_id)
    if not row:
        return case_law_error("Case law entry not found.", 404)

    try:
        folder_path = case_law_folder_path(row)
    except Exception as exc:
        return case_law_error(f"Invalid case law folder: {exc}", 500)

    try:
        if folder_path.exists():
            shutil.rmtree(folder_path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        return case_law_error(f"Failed to remove case files: {exc}", 500)

    try:
        conn.execute("DELETE FROM case_law WHERE id = ?", (case_id,))
        conn.execute("DELETE FROM case_law_fts WHERE rowid = ?", (case_id,))
        conn.commit()
    except Exception as exc:
        return case_law_error(f"Failed to remove database record: {exc}", 500)

    return jsonify({"ok": True, "deleted_id": case_id})


@app.get("/case-law/<int:case_id>/download")
def case_law_download(case_id: int):
    if not FS_ROOT:
        return "Not found", 404

    conn = get_case_law_db()
    row = fetch_case_law_record(conn, case_id)
    if not row:
        return "Not found", 404

    try:
        file_path = case_law_file_path(row)
    except Exception:
        return "Not found", 404

    if not file_path.exists():
        return "Not found", 404

    return send_file(file_path, as_attachment=True)


@app.route("/case-law/<int:case_id>/note", methods=["GET", "POST"])
def case_law_note(case_id: int):
    if not FS_ROOT:
        return case_law_error("Storage root is not configured yet.")

    conn = get_case_law_db()
    row = fetch_case_law_record(conn, case_id)
    if not row:
        return case_law_error("Case law record not found.", 404)

    try:
        note_path = case_law_note_path(row)
    except Exception:
        return case_law_error("Invalid note path for this record."), 400

    if request.method == "GET":
        content = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
        return jsonify({
            "ok": True,
            "content": content,
            "summary": row["note_text"],
        })

    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    summary = extract_note_summary(content)

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")

    judgement_row = conn.execute("SELECT content FROM case_law_fts WHERE rowid = ?", (case_id,)).fetchone()
    judgement_text = judgement_row["content"] if judgement_row else ""

    refresh_case_law_index(
        conn,
        case_id,
        judgement_text,
        row["petitioner"],
        row["respondent"],
        row["citation"],
        content,
    )

    conn.execute(
        "UPDATE case_law SET note_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (summary, case_id),
    )
    conn.commit()

    return jsonify({"ok": True, "summary": summary})


# ---- Directory Search --------------------------

@app.get("/api/dir-tree")
def api_dir_tree():
    """
    List directory contents starting from FS_ROOT.
    Query param:
      path: relative path under FS_ROOT (optional)
    Returns:
      { "dirs": [names], "files": [ {name, path} ] }
    """
    rel = (request.args.get("path") or "").strip()
    base = FS_ROOT
    try:
        if rel:
            base = (FS_ROOT / rel).resolve()
            # enforce FS_ROOT jail
            if not str(base).startswith(str(FS_ROOT.resolve())):
                return jsonify({"dirs": [], "files": []})
        if not base.exists() or not base.is_dir():
            return jsonify({"dirs": [], "files": []})

        dirs = []
        files = []
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir():
                dirs.append(entry.name)
            elif entry.is_file():
                if "." in entry.name and entry.name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS:
                    files.append({"name": entry.name, "path": str(entry)})
        return jsonify({"dirs": dirs, "files": files})
    except Exception as e:
        return jsonify({"dirs": [], "files": [], "error": str(e)}), 500


# ---- API: fetch Note.json content (for modal) --------------------------
@app.route("/api/note/<year>/<month>/<case_name>", methods=["POST"])
def api_update_note(year, month, case_name):
    cdir = FS_ROOT / year / month / case_name
    note_path = cdir / "Note.json"

    if not note_path.exists():
        template = make_note_json({})
        return jsonify({"ok": False, "msg": "Note.json not found", "template": template}), 404

    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    try:
        note_path.write_text(content, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Write failed: {e}"}), 500



# ---- API: Create Note.json --------------------------------
@app.post("/api/create-note")
def api_create_note():
    data = request.get_json(silent=True) or {}
    case_path = (data.get("case_path") or "").strip()
    if case_path:
        parts = Path(case_path).parts
        if len(parts) < 3:
            return jsonify({"ok": False, "msg": "Invalid case_path"}), 400
        year, month, case = parts[-3], parts[-2], parts[-1]
    else:
        year  = (data.get("year") or "").strip()
        month = (data.get("month") or "").strip()
        case  = normalize_ws(data.get("case") or "")

    if not (year and month and case):
        return jsonify({"ok": False, "msg": "Year, month, and case are required"}), 400

    cdir = (FS_ROOT / year / month / case).resolve()
    root = FS_ROOT.resolve()
    if not str(cdir).startswith(str(root)):
        return jsonify({"ok": False, "msg": "Invalid path"}), 400
    if not cdir.exists():
        return jsonify({"ok": False, "msg": "Case folder not found"}), 404

    note_file = cdir / "Note.json"
    if note_file.exists():
        return jsonify({"ok": False, "msg": "Note.json already exists"}), 400

    content = data.get("content") or ""

    payload: Dict[str, Any] = {}
    if content.strip():
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                payload = parsed
            else:
                payload = {"Additional Notes": content}
        except json.JSONDecodeError:
            payload = {"Additional Notes": content}

    try:
        text_out = make_note_json(payload)
        note_file.write_text(text_out, encoding="utf-8")
        return jsonify({"ok": True, "path": str(note_file)})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Write failed: {e}"}), 500

@app.route("/api/note/<year>/<month>/<case_name>", methods=["GET", "POST"])
def api_note(year, month, case_name):
    cdir = FS_ROOT / year / month / case_name
    note_path = cdir / "Note.json"
    if not note_path.exists():
        template = make_note_json({})
        return jsonify({"ok": False, "msg": "Note.json not found", "template": template}), 404

    if request.method == "GET":
        content = note_path.read_text(encoding="utf-8")
        return jsonify({"ok": True, "content": content, "template": make_note_json({})})

    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    try:
        note_path.write_text(content, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Write failed: {e}"}), 500



# ---- PDF Tools -----------------------------------------------------------

PDF_TOOL_CATALOG: list[dict[str, Any]] = [
    {"id": "merge", "title": "Merge PDF", "icon": "fa-object-group", "accept": ".pdf", "multiple": True, "implemented": True},
    {"id": "split", "title": "Split PDF", "icon": "fa-scissors", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "compress", "title": "Compress PDF", "icon": "fa-file-zipper", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "remove-pages", "title": "Remove PDF Pages", "icon": "fa-trash-can", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "reorder-pages", "title": "Rearrange PDF Pages", "icon": "fa-grip", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "ocr", "title": "OCR PDF", "icon": "fa-eye", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "page-numbers", "title": "Add Page Numbers", "icon": "fa-list-ol", "accept": ".pdf", "multiple": False, "implemented": True},
    {"id": "jpeg-to-pdf", "title": "JPEG to PDF", "icon": "fa-file-image", "accept": "image/*", "multiple": True, "implemented": True},
    {"id": "flatten", "title": "Flatten PDF", "icon": "fa-layer-group", "accept": ".pdf", "multiple": False, "implemented": True},
]


def _pdf_tool_by_id(tool_id: str) -> dict[str, Any]:
    for tool in PDF_TOOL_CATALOG:
        if tool["id"] == tool_id:
            return tool
    abort(404)


@app.get("/pdf-tools")
@require_login
def pdf_tools_home():
    return render_template("pdf_tools.html", tools=PDF_TOOL_CATALOG)


@app.get("/pdf-tools/<tool_id>")
@require_login
def pdf_tool_page(tool_id: str):
    tool = _pdf_tool_by_id(tool_id)
    ocr_languages: list[str] = []
    ocr_error: str | None = None
    if tool_id == "ocr":
        try:
            ocr_languages = pdf_tools.tesseract_languages()
        except Exception as exc:
            ocr_error = str(exc) or "Unable to load OCR languages."
    return render_template("pdf_tool.html", tool=tool, ocr_languages=ocr_languages, ocr_error=ocr_error)


def _current_user_id() -> int:
    user = g.get("current_user")
    if not user:
        raise RuntimeError("Authentication required.")
    return int(user["id"])


@app.post("/api/pdf-tools/<tool_id>/start")
@require_login_api
def api_pdf_tools_start(tool_id: str):
    tool = _pdf_tool_by_id(tool_id)
    if not tool.get("implemented"):
        return jsonify({"ok": False, "msg": "This tool is not implemented yet."}), 501

    owner_user_id = _current_user_id()
    job_id = pdf_jobs.create_job(tool=tool_id, owner_user_id=owner_user_id)
    paths = pdf_jobs.build_job_paths(job_id)

    def bad_request(msg: str):
        with suppress(Exception):
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            pdf_jobs.mark_job_completed(job_id)
        return jsonify({"ok": False, "msg": msg}), 400

    if tool_id == "merge":
        uploads = request.files.getlist("file")
        if len(uploads) < 2:
            return bad_request("Select at least two PDF files to merge.")

        input_paths: list[Path] = []
        merge_files: list[dict[str, Any]] = []
        for upload in uploads:
            if not upload or not upload.filename:
                continue
            if not (upload.filename or "").lower().endswith(".pdf"):
                continue
            file_idx = len(input_paths) + 1
            original_name = upload.filename
            safe_name = secure_filename(original_name) or f"document_{file_idx}.pdf"
            dest = paths.input_dir / f"{file_idx:02d}_{safe_name}"
            upload.save(dest)
            input_paths.append(dest)
            merge_files.append({"idx": file_idx, "name": original_name, "stored": dest.name})

        if len(input_paths) < 2:
            return bad_request("No valid PDF files were uploaded.")

        thumbs_dir = paths.input_dir / "merge_thumbs"
        with suppress(Exception):
            shutil.rmtree(thumbs_dir, ignore_errors=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        pdf_jobs.update_job_meta(
            job_id,
            merge_files=merge_files,
            merge_file_count=len(merge_files),
            thumbs_ready=False,
        )
        pdf_jobs.set_job_status(
            job_id,
            state="processing",
            percent=1,
            stage="thumbnails",
            message="Generating document previews…",
        )

        def _task_merge_thumbnails():
            try:
                total = len(input_paths)
                for idx, pdf_path in enumerate(input_paths, start=1):
                    pct = int((idx / total) * 99)
                    pct = max(1, min(99, pct))
                    pdf_jobs.set_job_status(
                        job_id,
                        state="processing",
                        percent=pct,
                        stage="thumbnails",
                        message=f"Generating document previews… ({idx}/{total})",
                    )
                    out_png = thumbs_dir / f"file-{idx:02d}.png"
                    pdf_tools.generate_pdf_first_page_thumbnail(
                        input_pdf=pdf_path,
                        output_png=out_png,
                        max_dim_px=PDF_THUMB_MAX_DIM_PX,
                    )

                pdf_jobs.update_job_meta(job_id, thumbs_ready=True)
                pdf_jobs.set_job_status(
                    job_id,
                    state="awaiting_order",
                    percent=0,
                    stage="awaiting_order",
                    message="Reorder documents and click Merge.",
                )
            except Exception as exc:
                msg = str(exc) or "Failed to generate document previews."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)

                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_merge_thumbnails)
        return jsonify({"ok": True, "job_id": job_id, "file_count": len(merge_files)})

    if tool_id == "split":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to split.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for splitting.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        try:
            page_count = pdf_tools.count_pdf_pages(input_pdf)
        except Exception as exc:
            return bad_request(str(exc) or "Unable to read the PDF.")

        pdf_jobs.update_job_meta(job_id, page_count=page_count, thumbs_ready=False)
        pdf_jobs.set_job_status(
            job_id,
            state="awaiting_split",
            percent=0,
            stage="awaiting_split",
            message="Ready to split.",
        )
        return jsonify({"ok": True, "job_id": job_id, "page_count": page_count})

    if tool_id == "remove-pages":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to edit.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for page removal.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        try:
            page_count = pdf_tools.count_pdf_pages(input_pdf)
        except Exception as exc:
            return bad_request(str(exc) or "Unable to read the PDF.")

        pdf_jobs.update_job_meta(job_id, page_count=page_count, thumbs_ready=False)
        pdf_jobs.set_job_status(
            job_id,
            state="awaiting_remove",
            percent=0,
            stage="awaiting_remove",
            message="Ready to remove pages.",
        )
        return jsonify({"ok": True, "job_id": job_id, "page_count": page_count})

    if tool_id == "page-numbers":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to edit.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for page numbering.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        try:
            page_count = pdf_tools.count_pdf_pages(input_pdf)
        except Exception as exc:
            return bad_request(str(exc) or "Unable to read the PDF.")

        pdf_jobs.update_job_meta(job_id, page_count=page_count)
        pdf_jobs.set_job_status(
            job_id,
            state="awaiting_numbers",
            percent=0,
            stage="awaiting_numbers",
            message="Ready to add page numbers.",
        )
        return jsonify({"ok": True, "job_id": job_id, "page_count": page_count})

    if tool_id == "ocr":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to OCR.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for OCR.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        try:
            page_count = pdf_tools.count_pdf_pages(input_pdf)
        except Exception as exc:
            return bad_request(str(exc) or "Unable to read the PDF.")

        try:
            available_languages = pdf_tools.tesseract_languages()
        except Exception as exc:
            return bad_request(str(exc) or "OCR is not available.")

        available_codes = {
            lang.get("code")
            for lang in available_languages
            if isinstance(lang, dict) and lang.get("code")
        }

        selected_languages = [str(lang).strip() for lang in request.form.getlist("ocr_language") if str(lang).strip()]
        if not selected_languages:
            if "eng" in available_codes:
                selected_languages = ["eng"]
            else:
                return bad_request("Select at least one OCR language.")

        invalid = [lang for lang in selected_languages if lang not in available_codes]
        if invalid:
            return bad_request(f"Unsupported OCR language(s): {', '.join(invalid)}.")

        dpi_raw = request.form.get("ocr_dpi") or "300"
        try:
            dpi = int(dpi_raw)
        except (TypeError, ValueError):
            return bad_request("DPI must be a number.")
        if dpi not in {192, 288, 384}:
            return bad_request("DPI must be one of: 192, 288, 384.")

        binarize = str(request.form.get("ocr_binarize") or "").lower() in {"1", "true", "yes", "on"}

        pdf_jobs.update_job_meta(job_id, page_count=page_count)
        pdf_jobs.set_job_status(job_id, state="queued", percent=0, stage="queued", message="Queued")

        def _task_ocr():
            try:
                pdf_jobs.set_job_status(job_id, state="processing", percent=5, stage="ocr", message="Preparing OCR…")

                def _hook(page_num: int, total_pages: int) -> None:
                    pct = int((page_num / max(1, total_pages)) * 90)
                    pct = max(5, min(95, pct))
                    pdf_jobs.set_job_status(
                        job_id,
                        state="processing",
                        percent=pct,
                        stage="ocr",
                        message=f"Running OCR… ({page_num}/{total_pages})",
                    )

                out = paths.output_dir / "ocr.pdf"
                pdf_tools.ocr_pdf(
                    input_pdf=input_pdf,
                    output_pdf=out,
                    languages=selected_languages,
                    dpi=dpi,
                    binarize=binarize,
                    page_count=page_count,
                    status_hook=_hook,
                )

                pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
            except Exception as exc:
                msg = str(exc) or "OCR failed."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            finally:
                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_ocr)
        return jsonify({"ok": True, "job_id": job_id, "page_count": page_count})

    if tool_id == "jpeg-to-pdf":
        uploads = request.files.getlist("file")
        uploads = [u for u in uploads if u and u.filename]
        if not uploads:
            return bad_request("Select at least one image to convert.")

        image_entries: list[dict[str, Any]] = []
        for idx, upload in enumerate(uploads, start=1):
            original_name = secure_filename(upload.filename) or f"image_{idx}"
            ext = Path(original_name).suffix.lower()
            if not ext:
                ext = ".png"
            stored = f"image_{idx:03d}{ext}"
            target = paths.input_dir / stored
            upload.save(target)
            image_entries.append({"idx": idx, "name": original_name, "stored": stored})

        if not image_entries:
            return bad_request("No valid images were uploaded.")

        digits = max(2, len(str(len(image_entries))))
        pdf_jobs.update_job_meta(
            job_id,
            image_files=image_entries,
            image_file_count=len(image_entries),
            image_digits=digits,
            thumbs_ready=False,
        )
        pdf_jobs.set_job_status(
            job_id,
            state="processing",
            percent=1,
            stage="thumbnails",
            message="Generating image previews…",
        )

        def _task_image_thumbnails():
            try:
                thumbs_dir = paths.input_dir / "image_thumbs"
                with suppress(Exception):
                    shutil.rmtree(thumbs_dir, ignore_errors=True)
                thumbs_dir.mkdir(parents=True, exist_ok=True)

                total = len(image_entries)
                for count, entry in enumerate(image_entries, start=1):
                    img_path = paths.input_dir / entry["stored"]
                    thumb_path = thumbs_dir / f"image-{entry['idx']:0{digits}d}.png"
                    pdf_tools.generate_image_thumbnail(
                        input_image=img_path,
                        output_png=thumb_path,
                        max_dim_px=PDF_THUMB_MAX_DIM_PX,
                    )
                    pct = int((count / total) * 100)
                    pct = max(1, min(99, pct))
                    pdf_jobs.set_job_status(
                        job_id,
                        state="processing",
                        percent=pct,
                        stage="thumbnails",
                        message="Generating image previews…",
                    )

                preview_pdf = paths.output_dir / "preview.pdf"
                pdf_jobs.set_job_status(
                    job_id,
                    state="processing",
                    percent=99,
                    stage="preview",
                    message="Preparing preview PDF…",
                )
                pdf_tools.images_to_pdf([paths.input_dir / entry["stored"] for entry in image_entries], preview_pdf)
                pdf_jobs.update_job_meta(job_id, preview_filename=preview_pdf.name, preview_ready=True)

                pdf_jobs.update_job_meta(job_id, thumbs_ready=True)
                pdf_jobs.set_job_status(
                    job_id,
                    state="awaiting_order",
                    percent=0,
                    stage="awaiting_order",
                    message="Reorder images and click Apply.",
                )
            except Exception as exc:
                msg = str(exc) or "Failed to generate image previews."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_image_thumbnails)
        return jsonify({"ok": True, "job_id": job_id, "image_count": len(image_entries)})

    if tool_id == "compress":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to compress.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for compression.")

        level = (request.form.get("compression_level") or "medium").strip().lower()
        if level not in {"low", "medium", "high"}:
            level = "medium"
        method = (request.form.get("compression_method") or "photon").strip().lower()
        if method not in {"rectal", "photon"}:
            method = "photon"

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        pdf_jobs.set_job_status(job_id, state="queued", percent=0, stage="queued", message="Queued")

        def _task_compress():
            try:
                pdf_jobs.set_job_status(
                    job_id,
                    state="processing",
                    percent=25,
                    stage="compress",
                    message="Compressing PDF…",
                )
                out = paths.output_dir / "compressed.pdf"
                result = pdf_tools.compress_pdf(input_pdf=input_pdf, output_pdf=out, level=level, method=method)

                def _format_bytes(value: int) -> str:
                    size = float(max(0, value))
                    for unit in ("B", "KB", "MB", "GB", "TB"):
                        if size < 1024 or unit == "TB":
                            if unit == "B":
                                return f"{int(size)} {unit}"
                            return f"{size:.1f} {unit}"
                        size /= 1024
                    return f"{size:.1f} TB"

                before = _format_bytes(result.input_bytes)
                after = _format_bytes(result.output_bytes)
                if result.input_bytes <= 0:
                    message = "Ready"
                elif result.output_bytes < result.input_bytes:
                    delta = 100 - (result.output_bytes / result.input_bytes * 100)
                    message = f"Ready ({before} -> {after}, -{delta:.0f}%)"
                elif result.output_bytes > result.input_bytes:
                    delta = (result.output_bytes / result.input_bytes * 100) - 100
                    alt = "Photon" if method == "rectal" else "Rectal"
                    message = f"Ready ({before} -> {after}, +{delta:.0f}%). Try {alt} for smaller output."
                else:
                    alt = "Photon" if method == "rectal" else "Rectal"
                    message = f"Ready ({before} -> {after}, 0%). Try {alt} for smaller output."

                pdf_jobs.set_job_status(
                    job_id,
                    state="done",
                    percent=100,
                    stage="done",
                    message=message,
                    result_filename=out.name,
                )
            except Exception as exc:
                msg = str(exc) or "Compression failed."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            finally:
                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_compress)
        return jsonify({"ok": True, "job_id": job_id})

    if tool_id == "reorder-pages":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to reorder.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for page reordering.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        try:
            page_count = pdf_tools.count_pdf_pages(input_pdf)
        except Exception as exc:
            return bad_request(str(exc) or "Unable to read the PDF.")

        if page_count > PDF_THUMB_MAX_PAGES:
            return bad_request(
                f"This PDF has {page_count} pages. The current preview limit is {PDF_THUMB_MAX_PAGES} pages."
            )

        thumbs_dir = paths.input_dir / "thumbs"
        with suppress(Exception):
            shutil.rmtree(thumbs_dir, ignore_errors=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        pdf_jobs.update_job_meta(job_id, page_count=page_count, thumbs_ready=False)
        pdf_jobs.set_job_status(
            job_id,
            state="processing",
            percent=1,
            stage="thumbnails",
            message="Generating page previews…",
        )

        def _task_thumbnails():
            try:
                def _hook(pct: int) -> None:
                    # Keep room for the final UI state
                    pct2 = max(1, min(99, int(pct)))
                    pdf_jobs.set_job_status(
                        job_id,
                        state="processing",
                        percent=pct2,
                        stage="thumbnails",
                        message="Generating page previews…",
                    )

                pdf_tools.generate_pdf_thumbnails(
                    input_pdf=input_pdf,
                    output_dir=thumbs_dir,
                    page_count=page_count,
                    max_dim_px=PDF_THUMB_MAX_DIM_PX,
                    status_hook=_hook,
                )
                pdf_jobs.update_job_meta(job_id, thumbs_ready=True)
                pdf_jobs.set_job_status(
                    job_id,
                    state="awaiting_order",
                    percent=0,
                    stage="awaiting_order",
                    message="Reorder pages and click Apply.",
                )
            except Exception as exc:
                msg = str(exc) or "Failed to generate page previews."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_thumbnails)
        return jsonify({"ok": True, "job_id": job_id, "page_count": page_count})

    if tool_id == "flatten":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return bad_request("Select a PDF file to flatten.")
        if not (upload.filename or "").lower().endswith(".pdf"):
            return bad_request("Only PDF files are supported for flattening.")

        input_pdf = paths.input_dir / "input.pdf"
        upload.save(input_pdf)

        pdf_jobs.set_job_status(job_id, state="queued", percent=0, stage="queued", message="Queued")

        def _task_flatten():
            try:
                pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="flatten", message="Flattening PDF…")
                out = paths.output_dir / "flattened.pdf"
                pdf_tools.flatten_pdf_annotations(input_pdf=input_pdf, output_pdf=out, mode="all")
                pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
            except Exception as exc:
                msg = str(exc) or "Flatten failed."
                pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            finally:
                pdf_jobs.mark_job_completed(job_id)

        PDF_TOOL_EXECUTOR.submit(_task_flatten)
        return jsonify({"ok": True, "job_id": job_id})

    return bad_request("Unknown tool.")


@app.post("/api/pdf-tools/jobs/<job_id>/apply-order")
@require_login_api
def api_pdf_tools_apply_order(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "reorder-pages":
        return jsonify({"ok": False, "msg": "This job is not a reorder-pages job."}), 400

    data = request.get_json(silent=True) or {}
    order_raw = data.get("order")
    if not isinstance(order_raw, list) or not order_raw:
        return jsonify({"ok": False, "msg": "Missing 'order' list."}), 400

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400

    try:
        # client sends 1-based page numbers
        order_1based = [int(v) for v in order_raw]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Order must contain integers."}), 400

    if len(order_1based) != page_count:
        return jsonify({"ok": False, "msg": f"Order must list all {page_count} pages."}), 400
    if set(order_1based) != set(range(1, page_count + 1)):
        return jsonify({"ok": False, "msg": "Order must be a permutation of all pages."}), 400

    order_0based = [v - 1 for v in order_1based]
    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="reorder", message="Reordering pages…")

    def _task_reorder():
        try:
            out = paths.output_dir / "reordered.pdf"
            pdf_tools.reorder_pdf(input_pdf, order_0based, out)
            pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
        except Exception as exc:
            msg = str(exc) or "Reorder failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_reorder)
    return jsonify({"ok": True})


@app.post("/api/pdf-tools/jobs/<job_id>/apply-merge")
@require_login_api
def api_pdf_tools_apply_merge(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "merge":
        return jsonify({"ok": False, "msg": "This job is not a merge job."}), 400

    merge_files = meta.get("merge_files") or []
    if not isinstance(merge_files, list) or len(merge_files) < 2:
        return jsonify({"ok": False, "msg": "Job is missing merge file metadata."}), 400

    by_idx: dict[int, str] = {}
    for item in merge_files:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        stored = item.get("stored")
        if isinstance(stored, str) and stored:
            by_idx[idx] = stored

    file_count = len(by_idx)
    if file_count < 2:
        return jsonify({"ok": False, "msg": "Job has fewer than two valid PDFs."}), 400

    data = request.get_json(silent=True) or {}
    order_raw = data.get("order")
    if not isinstance(order_raw, list) or not order_raw:
        return jsonify({"ok": False, "msg": "Missing 'order' list."}), 400

    try:
        order_1based = [int(v) for v in order_raw]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Order must contain integers."}), 400

    if len(order_1based) != file_count:
        return jsonify({"ok": False, "msg": f"Order must list all {file_count} PDFs."}), 400
    if set(order_1based) != set(range(1, file_count + 1)):
        return jsonify({"ok": False, "msg": "Order must be a permutation of all PDFs."}), 400

    paths = pdf_jobs.build_job_paths(job_id)
    input_paths: list[Path] = []
    for idx in order_1based:
        stored = by_idx.get(idx)
        if not stored:
            return jsonify({"ok": False, "msg": "Input file missing (job expired?)"}), 410
        p = paths.input_dir / stored
        if not p.exists():
            return jsonify({"ok": False, "msg": "Input file missing (job expired?)"}), 410
        input_paths.append(p)

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="merge", message="Merging PDFs…")

    def _task_merge():
        try:
            out = paths.output_dir / "merged.pdf"
            pdf_tools.merge_pdfs(input_paths, out)
            pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
        except Exception as exc:
            msg = str(exc) or "Merge failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_merge)
    return jsonify({"ok": True})

@app.post("/api/pdf-tools/jobs/<job_id>/split-thumbs")
@require_login_api
def api_pdf_tools_split_thumbnails(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "split":
        return jsonify({"ok": False, "msg": "This job is not a split job."}), 400

    if bool(meta.get("thumbs_ready")):
        return jsonify({"ok": True, "thumbs_ready": True})

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400
    if page_count > PDF_THUMB_MAX_PAGES:
        return jsonify(
            {"ok": False, "msg": f"This PDF has {page_count} pages. Preview limit is {PDF_THUMB_MAX_PAGES} pages."}
        ), 400

    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    thumbs_dir = paths.input_dir / "thumbs"
    with suppress(Exception):
        shutil.rmtree(thumbs_dir, ignore_errors=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    pdf_jobs.update_job_meta(job_id, thumbs_ready=False)
    pdf_jobs.set_job_status(
        job_id,
        state="processing",
        percent=1,
        stage="thumbnails",
        message="Generating page previews…",
    )

    def _task_split_thumbnails():
        try:
            def _hook(pct: int) -> None:
                pct2 = max(1, min(99, int(pct)))
                pdf_jobs.set_job_status(
                    job_id,
                    state="processing",
                    percent=pct2,
                    stage="thumbnails",
                    message="Generating page previews…",
                )

            pdf_tools.generate_pdf_thumbnails(
                input_pdf=input_pdf,
                output_dir=thumbs_dir,
                page_count=page_count,
                max_dim_px=PDF_THUMB_MAX_DIM_PX,
                status_hook=_hook,
            )
            pdf_jobs.update_job_meta(job_id, thumbs_ready=True)
            pdf_jobs.set_job_status(
                job_id,
                state="awaiting_split",
                percent=0,
                stage="awaiting_split",
                message="Ready to split.",
            )
        except Exception as exc:
            msg = str(exc) or "Failed to generate page previews."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_split_thumbnails)
    return jsonify({"ok": True, "thumbs_ready": False})


@app.post("/api/pdf-tools/jobs/<job_id>/remove-thumbs")
@require_login_api
def api_pdf_tools_remove_thumbnails(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "remove-pages":
        return jsonify({"ok": False, "msg": "This job is not a remove-pages job."}), 400

    if bool(meta.get("thumbs_ready")):
        return jsonify({"ok": True, "thumbs_ready": True})

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400
    if page_count > PDF_THUMB_MAX_PAGES:
        return jsonify(
            {"ok": False, "msg": f"This PDF has {page_count} pages. Preview limit is {PDF_THUMB_MAX_PAGES} pages."}
        ), 400

    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    thumbs_dir = paths.input_dir / "thumbs"
    with suppress(Exception):
        shutil.rmtree(thumbs_dir, ignore_errors=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    pdf_jobs.update_job_meta(job_id, thumbs_ready=False)
    pdf_jobs.set_job_status(
        job_id,
        state="processing",
        percent=1,
        stage="thumbnails",
        message="Generating page previews…",
    )

    def _task_remove_thumbnails():
        try:
            def _hook(pct: int) -> None:
                pct2 = max(1, min(99, int(pct)))
                pdf_jobs.set_job_status(
                    job_id,
                    state="processing",
                    percent=pct2,
                    stage="thumbnails",
                    message="Generating page previews…",
                )

            pdf_tools.generate_pdf_thumbnails(
                input_pdf=input_pdf,
                output_dir=thumbs_dir,
                page_count=page_count,
                max_dim_px=PDF_THUMB_MAX_DIM_PX,
                status_hook=_hook,
            )
            pdf_jobs.update_job_meta(job_id, thumbs_ready=True)
            pdf_jobs.set_job_status(
                job_id,
                state="awaiting_remove",
                percent=0,
                stage="awaiting_remove",
                message="Ready to remove pages.",
            )
        except Exception as exc:
            msg = str(exc) or "Failed to generate page previews."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_remove_thumbnails)
    return jsonify({"ok": True, "thumbs_ready": False})


@app.post("/api/pdf-tools/jobs/<job_id>/apply-split")
@require_login_api
def api_pdf_tools_apply_split(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "split":
        return jsonify({"ok": False, "msg": "This job is not a split job."}), 400

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in {"ranges", "odd", "even", "all", "visual"}:
        return jsonify({"ok": False, "msg": "Invalid split mode."}), 400

    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    ranges: list[tuple[int, int]] = []
    selected_pages: list[int] = []

    if mode == "ranges":
        raw_ranges = data.get("ranges")
        if not isinstance(raw_ranges, list) or not raw_ranges:
            return jsonify({"ok": False, "msg": "Provide at least one page range."}), 400
        for item in raw_ranges:
            start = end = None
            if isinstance(item, dict):
                start = item.get("start")
                end = item.get("end")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                start, end = item
            try:
                start_i = int(start)
                end_i = int(end)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "msg": "Ranges must contain valid numbers."}), 400
            if start_i < 1 or end_i < 1 or start_i > end_i:
                return jsonify({"ok": False, "msg": "Ranges must be in ascending order and start at 1."}), 400
            if end_i > page_count:
                return jsonify({"ok": False, "msg": f"Range exceeds page count ({page_count})."}), 400
            ranges.append((start_i, end_i))

    if mode == "visual":
        raw_pages = data.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            return jsonify({"ok": False, "msg": "Select at least one page."}), 400
        for value in raw_pages:
            try:
                page_i = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= page_i <= page_count:
                selected_pages.append(page_i)
        if not selected_pages:
            return jsonify({"ok": False, "msg": "Select at least one valid page."}), 400
        selected_pages = sorted(set(selected_pages))

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="split", message="Splitting PDF…")

    def _task_split():
        try:
            if mode == "ranges":
                output_dir = paths.output_dir / "ranges"
                outputs = pdf_tools.split_pdf_ranges(input_pdf, ranges, output_dir)
                zip_path = paths.output_dir / "split_ranges.zip"
                pdf_tools.zip_paths(outputs, zip_path)
                result_name = zip_path.name
            elif mode == "all":
                output_dir = paths.output_dir / "pages"
                outputs = pdf_tools.split_pdf_all_pages(input_pdf, output_dir)
                zip_path = paths.output_dir / "split_pages.zip"
                pdf_tools.zip_paths(outputs, zip_path)
                result_name = zip_path.name
            elif mode == "odd":
                out = paths.output_dir / "odd_pages.pdf"
                pdf_tools.split_pdf_odd_even(input_pdf, odd=True, output_file=out)
                result_name = out.name
            elif mode == "even":
                out = paths.output_dir / "even_pages.pdf"
                pdf_tools.split_pdf_odd_even(input_pdf, odd=False, output_file=out)
                result_name = out.name
            elif mode == "visual":
                out = paths.output_dir / "selected_pages.pdf"
                pdf_tools.split_pdf_selected_pages(input_pdf, selected_pages, out)
                result_name = out.name
            else:
                raise RuntimeError("Unsupported split mode.")

            pdf_jobs.set_job_status(
                job_id,
                state="done",
                percent=100,
                stage="done",
                message="Ready",
                result_filename=result_name,
            )
        except Exception as exc:
            msg = str(exc) or "Split failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_split)
    return jsonify({"ok": True})


@app.post("/api/pdf-tools/jobs/<job_id>/apply-remove")
@require_login_api
def api_pdf_tools_apply_remove(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "remove-pages":
        return jsonify({"ok": False, "msg": "This job is not a remove-pages job."}), 400

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in {"ranges", "odd", "even", "visual"}:
        return jsonify({"ok": False, "msg": "Invalid remove mode."}), 400

    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    remove_pages: set[int] = set()

    if mode == "ranges":
        raw_ranges = data.get("ranges")
        if not isinstance(raw_ranges, list) or not raw_ranges:
            return jsonify({"ok": False, "msg": "Provide at least one page range."}), 400
        for item in raw_ranges:
            start = end = None
            if isinstance(item, dict):
                start = item.get("start")
                end = item.get("end")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                start, end = item
            try:
                start_i = int(start)
                end_i = int(end)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "msg": "Ranges must contain valid numbers."}), 400
            if start_i < 1 or end_i < 1 or start_i > end_i:
                return jsonify({"ok": False, "msg": "Ranges must be in ascending order and start at 1."}), 400
            if end_i > page_count:
                return jsonify({"ok": False, "msg": f"Range exceeds page count ({page_count})."}), 400
            remove_pages.update(range(start_i, end_i + 1))

    if mode == "visual":
        raw_pages = data.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            return jsonify({"ok": False, "msg": "Select at least one page to remove."}), 400
        for value in raw_pages:
            try:
                page_i = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= page_i <= page_count:
                remove_pages.add(page_i)
        if not remove_pages:
            return jsonify({"ok": False, "msg": "Select at least one valid page."}), 400

    if mode == "odd":
        remove_pages.update(range(1, page_count + 1, 2))
    if mode == "even":
        remove_pages.update(range(2, page_count + 1, 2))

    if not remove_pages:
        return jsonify({"ok": False, "msg": "Select at least one page to remove."}), 400
    if len(remove_pages) >= page_count:
        return jsonify({"ok": False, "msg": "Removing all pages is not allowed."}), 400

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="remove", message="Removing pages…")

    def _task_remove():
        try:
            out = paths.output_dir / "pages_removed.pdf"
            pdf_tools.remove_pdf_pages(input_pdf, sorted(remove_pages), out)
            pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
        except Exception as exc:
            msg = str(exc) or "Remove pages failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_remove)
    return jsonify({"ok": True})


@app.post("/api/pdf-tools/jobs/<job_id>/apply-page-numbers")
@require_login_api
def api_pdf_tools_apply_page_numbers(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "page-numbers":
        return jsonify({"ok": False, "msg": "This job is not a page-numbers job."}), 400

    page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return jsonify({"ok": False, "msg": "Job is missing page count."}), 400

    data = request.get_json(silent=True) or {}
    try:
        start_page = int(data.get("start_page"))
        end_page = int(data.get("end_page"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Start/end page must be numbers."}), 400

    if start_page < 1 or end_page < 1 or start_page > end_page:
        return jsonify({"ok": False, "msg": "Page ranges must be in ascending order and start at 1."}), 400
    if end_page > page_count:
        return jsonify({"ok": False, "msg": f"End page exceeds page count ({page_count})."}), 400

    position = str(data.get("position") or "").strip().lower()
    if position not in {"top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"}:
        return jsonify({"ok": False, "msg": "Invalid position."}), 400

    font_name = str(data.get("font_name") or "").strip()
    if font_name not in {"Helvetica", "Helvetica-Bold", "Times-Roman", "Times-Bold", "Courier"}:
        return jsonify({"ok": False, "msg": "Unsupported font."}), 400

    try:
        font_size = int(data.get("font_size"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Font size must be a number."}), 400
    if font_size < 6 or font_size > 72:
        return jsonify({"ok": False, "msg": "Font size must be between 6 and 72."}), 400

    font_color = str(data.get("font_color") or "").strip()

    paths = pdf_jobs.build_job_paths(job_id)
    input_pdf = paths.input_dir / "input.pdf"
    if not input_pdf.exists():
        return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="page_numbers", message="Adding page numbers…")

    def _task_numbers():
        try:
            out = paths.output_dir / "page_numbers.pdf"
            pdf_tools.add_page_numbers(
                input_pdf=input_pdf,
                output_pdf=out,
                start_page=start_page,
                end_page=end_page,
                position=position,
                font_name=font_name,
                font_size=font_size,
                font_color=font_color,
            )
            pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
        except Exception as exc:
            msg = str(exc) or "Page numbering failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_numbers)
    return jsonify({"ok": True})


@app.post("/api/pdf-tools/jobs/<job_id>/apply-images")
@require_login_api
def api_pdf_tools_apply_images(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "jpeg-to-pdf":
        return jsonify({"ok": False, "msg": "This job is not an image-to-PDF job."}), 400

    image_files = meta.get("image_files") or []
    if not isinstance(image_files, list) or not image_files:
        return jsonify({"ok": False, "msg": "Job is missing image metadata."}), 400

    data = request.get_json(silent=True) or {}
    order_raw = data.get("order")
    if not isinstance(order_raw, list) or not order_raw:
        return jsonify({"ok": False, "msg": "Missing 'order' list."}), 400

    try:
        order = [int(v) for v in order_raw]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Order must contain integers."}), 400

    valid_ids = []
    for item in image_files:
        if isinstance(item, dict):
            try:
                valid_ids.append(int(item.get("idx")))
            except (TypeError, ValueError):
                continue

    if len(order) != len(valid_ids):
        return jsonify({"ok": False, "msg": "Order must include all images."}), 400
    if set(order) != set(valid_ids):
        return jsonify({"ok": False, "msg": "Order must be a permutation of all images."}), 400

    by_idx: dict[int, str] = {}
    for item in image_files:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        stored = item.get("stored")
        if isinstance(stored, str) and stored:
            by_idx[idx] = stored

    paths = pdf_jobs.build_job_paths(job_id)
    input_paths: list[Path] = []
    for idx in order:
        stored = by_idx.get(idx)
        if not stored:
            return jsonify({"ok": False, "msg": "Input file missing (job expired?)"}), 410
        path = paths.input_dir / stored
        if not path.exists():
            return jsonify({"ok": False, "msg": "Input file missing (job expired?)"}), 410
        input_paths.append(path)

    pdf_jobs.set_job_status(job_id, state="processing", percent=25, stage="convert", message="Converting images…")

    def _task_images():
        try:
            out = paths.output_dir / "images.pdf"
            pdf_tools.images_to_pdf(input_paths, out)
            pdf_jobs.set_job_status(job_id, state="done", percent=100, stage="done", message="Ready", result_filename=out.name)
        except Exception as exc:
            msg = str(exc) or "Image conversion failed."
            pdf_jobs.set_job_status(job_id, state="error", percent=100, stage="error", message=msg, error=msg)
        finally:
            pdf_jobs.mark_job_completed(job_id)

    PDF_TOOL_EXECUTOR.submit(_task_images)
    return jsonify({"ok": True})


@app.post("/api/pdf-tools/jobs/<job_id>/cancel")
@require_login_api
def api_pdf_tools_job_cancel(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    if bool(meta.get("cancel_requested")):
        with suppress(Exception):
            pdf_jobs.mark_job_completed(job_id)
        return jsonify({"ok": True})

    pdf_jobs.update_job_meta(
        job_id,
        cancel_requested=True,
        state="canceled",
        percent=100,
        stage="canceled",
        message="Canceled.",
        error=None,
        result_filename=None,
        thumbs_ready=False,
    )
    with suppress(Exception):
        pdf_jobs.mark_job_completed(job_id)
    return jsonify({"ok": True})


@app.get("/api/pdf-tools/jobs/<job_id>/status")
@require_login_api
def api_pdf_tools_job_status(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "state": "expired", "msg": "Job expired."}), 410

    expires_in = None
    if expires_at:
        with suppress(TypeError, ValueError):
            expires_in = max(0, int(float(expires_at) - time.time()))

    merge_files_public: list[dict[str, Any]] = []
    merge_files_meta = meta.get("merge_files")
    if isinstance(merge_files_meta, list):
        for item in merge_files_meta:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("idx"))
            except (TypeError, ValueError):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            merge_files_public.append({"idx": idx, "name": name})

    image_files_public: list[dict[str, Any]] = []
    image_files_meta = meta.get("image_files")
    if isinstance(image_files_meta, list):
        for item in image_files_meta:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("idx"))
            except (TypeError, ValueError):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            image_files_public.append({"idx": idx, "name": name})

    return jsonify(
        {
            "ok": True,
            "job_id": meta.get("job_id"),
            "tool": meta.get("tool"),
            "state": meta.get("state"),
            "percent": meta.get("percent", 0),
            "stage": meta.get("stage", ""),
            "message": meta.get("message", ""),
            "error": meta.get("error"),
            "result_filename": meta.get("result_filename"),
            "expires_at": meta.get("expires_at"),
            "expires_in": expires_in,
            "page_count": meta.get("page_count"),
            "thumbs_ready": bool(meta.get("thumbs_ready", False)),
            "merge_file_count": meta.get("merge_file_count") or len(merge_files_public),
            "merge_files": merge_files_public,
            "image_file_count": meta.get("image_file_count") or len(image_files_public),
            "image_files": image_files_public,
        }
    )


@app.get("/api/pdf-tools/jobs/<job_id>/thumb/<int:page_number>")
@require_login_api
def api_pdf_tools_job_thumbnail(job_id: str, page_number: int):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    tool_id = meta.get("tool")
    if tool_id not in {"reorder-pages", "split", "remove-pages", "page-numbers"}:
        return jsonify({"ok": False, "msg": "Thumbnails are only available for reorder-pages, split, remove-pages, and page-numbers."}), 400

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    page_count = int(meta.get("page_count") or 0)
    if page_number < 1 or (page_count and page_number > page_count):
        return jsonify({"ok": False, "msg": "Page not found"}), 404

    paths = pdf_jobs.build_job_paths(job_id)
    root = (paths.input_dir / "thumbs").resolve()

    candidates: list[Path] = []
    if page_count:
        digits = max(1, len(str(page_count)))
        candidates.append((root / f"page-{page_number:0{digits}d}.png").resolve())
    candidates.append((root / f"page-{page_number}.png").resolve())

    thumb: Path | None = None
    for candidate in candidates:
        if not str(candidate).startswith(str(root)):
            return jsonify({"ok": False, "msg": "Not found"}), 404
        if candidate.exists():
            thumb = candidate
            break

    if not thumb and tool_id == "page-numbers":
        input_pdf = paths.input_dir / "input.pdf"
        if not input_pdf.exists():
            return jsonify({"ok": False, "msg": "Input PDF is missing (job expired?)"}), 410
        root.mkdir(parents=True, exist_ok=True)
        target = candidates[0]
        try:
            pdf_tools.generate_pdf_page_thumbnail(
                input_pdf=input_pdf,
                output_png=target,
                page_number=page_number,
                max_dim_px=PDF_THUMB_MAX_DIM_PX,
            )
        except Exception as exc:
            return jsonify({"ok": False, "msg": str(exc) or "Thumbnail failed."}), 400
        if target.exists():
            thumb = target

    if not thumb:
        # still generating or job expired
        return jsonify({"ok": False, "msg": "Thumbnail not ready"}), 404

    return send_file(thumb, mimetype="image/png", conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/image-thumb/<int:image_index>")
@require_login_api
def api_pdf_tools_job_image_thumbnail(job_id: str, image_index: int):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "jpeg-to-pdf":
        return jsonify({"ok": False, "msg": "Image thumbnails are only available for image-to-PDF jobs."}), 400

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    image_files = meta.get("image_files") or []
    if not isinstance(image_files, list) or not image_files:
        return jsonify({"ok": False, "msg": "Thumbnail not ready"}), 404

    count = 0
    for item in image_files:
        if isinstance(item, dict):
            count += 1
    if image_index < 1 or image_index > count:
        return jsonify({"ok": False, "msg": "Not found"}), 404

    digits = int(meta.get("image_digits") or max(2, len(str(count))))
    paths = pdf_jobs.build_job_paths(job_id)
    root = (paths.input_dir / "image_thumbs").resolve()
    thumb = (root / f"image-{image_index:0{digits}d}.png").resolve()
    if not str(thumb).startswith(str(root)):
        return jsonify({"ok": False, "msg": "Not found"}), 404
    if not thumb.exists():
        return jsonify({"ok": False, "msg": "Thumbnail not ready"}), 404

    return send_file(thumb, mimetype="image/png", conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/image/<int:image_index>")
@require_login_api
def api_pdf_tools_job_image(job_id: str, image_index: int):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "jpeg-to-pdf":
        return jsonify({"ok": False, "msg": "Images are only available for image-to-PDF jobs."}), 400

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    image_files = meta.get("image_files") or []
    if not isinstance(image_files, list) or not image_files:
        return jsonify({"ok": False, "msg": "Image not found"}), 404

    stored = None
    for item in image_files:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx == image_index:
            stored = item.get("stored")
            break
    if not stored or not isinstance(stored, str):
        return jsonify({"ok": False, "msg": "Image not found"}), 404

    paths = pdf_jobs.build_job_paths(job_id)
    root = paths.input_dir.resolve()
    target = (paths.input_dir / stored).resolve()
    if not str(target).startswith(str(root)) or not target.exists():
        return jsonify({"ok": False, "msg": "Image not found"}), 404

    return send_file(target, conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/image-preview")
@require_login_api
def api_pdf_tools_job_image_preview(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "jpeg-to-pdf":
        return jsonify({"ok": False, "msg": "Preview is only available for image-to-PDF jobs."}), 400

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    preview_name = meta.get("preview_filename") or "preview.pdf"
    paths = pdf_jobs.build_job_paths(job_id)
    target = (paths.output_dir / preview_name).resolve()
    root = paths.output_dir.resolve()
    if not str(target).startswith(str(root)) or not target.exists():
        return jsonify({"ok": False, "msg": "Preview not ready"}), 404

    return send_file(target, mimetype="application/pdf", conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/merge-thumb/<int:file_index>")
@require_login_api
def api_pdf_tools_job_merge_thumbnail(job_id: str, file_index: int):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    if meta.get("tool") != "merge":
        return jsonify({"ok": False, "msg": "Merge thumbnails are only available for merge jobs."}), 400

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    merge_files = meta.get("merge_files") or []
    if not isinstance(merge_files, list) or len(merge_files) < 1:
        return jsonify({"ok": False, "msg": "Thumbnail not ready"}), 404

    file_count = 0
    for item in merge_files:
        if isinstance(item, dict):
            file_count += 1

    if file_index < 1 or file_index > file_count:
        return jsonify({"ok": False, "msg": "Not found"}), 404

    paths = pdf_jobs.build_job_paths(job_id)
    root = (paths.input_dir / "merge_thumbs").resolve()
    thumb = (root / f"file-{file_index:02d}.png").resolve()
    if not str(thumb).startswith(str(root)):
        return jsonify({"ok": False, "msg": "Not found"}), 404
    if not thumb.exists():
        return jsonify({"ok": False, "msg": "Thumbnail not ready"}), 404

    return send_file(thumb, mimetype="image/png", conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/view")
@require_login_api
def api_pdf_tools_job_view(job_id: str):
    """Serve the finished PDF inline for in-app preview (not as a download)."""
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    if (meta.get("state") or "").lower() != "done":
        return jsonify({"ok": False, "msg": "Job is not ready for preview yet."}), 400

    result_filename = meta.get("result_filename") or "result.pdf"
    paths = pdf_jobs.build_job_paths(job_id)
    target = (paths.output_dir / result_filename).resolve()
    root = paths.output_dir.resolve()
    if not str(target).startswith(str(root)) or not target.exists():
        return jsonify({"ok": False, "msg": "Result file missing (job expired?)"}), 410

    return send_file(target, mimetype="application/pdf", conditional=True)


@app.get("/api/pdf-tools/jobs/<job_id>/download")
@require_login_api
def api_pdf_tools_job_download(job_id: str):
    owner_user_id = _current_user_id()
    try:
        meta = pdf_jobs.get_job_meta(job_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404
    try:
        pdf_jobs.assert_job_owner(meta, owner_user_id=owner_user_id)
    except PermissionError:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    expires_at = meta.get("expires_at")
    if expires_at:
        with suppress(TypeError, ValueError):
            if time.time() > float(expires_at):
                return jsonify({"ok": False, "msg": "Job expired."}), 410

    if (meta.get("state") or "").lower() != "done":
        return jsonify({"ok": False, "msg": "Job is not ready for download yet."}), 400

    result_filename = meta.get("result_filename") or "result.pdf"
    paths = pdf_jobs.build_job_paths(job_id)
    target = (paths.output_dir / result_filename).resolve()
    root = paths.output_dir.resolve()
    if not str(target).startswith(str(root)) or not target.exists():
        return jsonify({"ok": False, "msg": "Result file missing (job expired?)"}), 410

    return send_file(target, as_attachment=True, download_name=result_filename, conditional=True)


# ---- Entrypoint ---------------------------------------------------------
if __name__ == "__main__":
    ensure_root()
    print("\nURL map:")
    for r in app.url_map.iter_rules():
        methods = ",".join(sorted(m for m in r.methods if m not in {"HEAD","OPTIONS"}))
        print(f"  {r.rule:22s} [{methods}]")
    print()
    app.run(host="0.0.0.0", port=5000, debug=True)
