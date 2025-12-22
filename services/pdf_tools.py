"""PDF processing primitives used by the PDF Tools feature-set.

All imports of optional dependencies are inside functions so the main app can
start even if PDF tooling deps are not installed yet.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class CompressionResult:
    input_bytes: int
    output_bytes: int
    method: str
    level: str

    @property
    def reduced(self) -> bool:
        return self.output_bytes < self.input_bytes


def merge_pdfs(input_files: Iterable[Path], output_file: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF merge. Install it with `pip install pypdf`.") from exc

    writer = PdfWriter()
    files = list(input_files)
    if len(files) < 2:
        raise ValueError("Select at least two PDF files to merge.")

    for path in files:
        with path.open("rb") as fh:
            reader = PdfReader(fh)
            for page in reader.pages:
                writer.add_page(page)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("wb") as out:
        writer.write(out)


def flatten_pdf_annotations(
    *,
    input_pdf: Path,
    output_pdf: Path,
    mode: str = "all",
) -> None:
    """Flatten PDF annotations into the content stream using qpdf.

    This is commonly used to "bake in" signatures/markups and remove interactive
    annotation layers so the PDF looks the same everywhere.

    mode: one of "all", "print", "screen" (qpdf semantics)
    """

    qpdf = shutil.which("qpdf")
    if not qpdf:
        raise RuntimeError("qpdf is required for flattening PDFs. Please install qpdf.")

    mode_key = (mode or "all").strip().lower()
    if mode_key not in {"all", "print", "screen"}:
        mode_key = "all"

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        qpdf,
        f"--flatten-annotations={mode_key}",
        str(input_pdf),
        str(output_pdf),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True)
    if not output_pdf.exists():
        raise RuntimeError("Failed to flatten PDF.")


def compress_pdf(
    *,
    input_pdf: Path,
    output_pdf: Path,
    level: str = "medium",
    method: str = "photon",
) -> CompressionResult:
    """Compress a PDF using the selected method.

    method: "rectal" (lossless, text-heavy) or "photon" (lossy, image-heavy)
    level: "low", "medium", "high"
    """

    input_size = input_pdf.stat().st_size
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    method_key = (method or "photon").strip().lower()
    if method_key not in {"rectal", "photon"}:
        method_key = "photon"

    level_key = (level or "medium").strip().lower()
    if level_key not in {"low", "medium", "high"}:
        level_key = "medium"

    if method_key == "rectal":
        output_size = _compress_rectal(input_pdf=input_pdf, output_pdf=output_pdf, level=level_key)
    else:
        output_size = _compress_photon(input_pdf=input_pdf, output_pdf=output_pdf, level=level_key)

    if not output_pdf.exists():
        raise RuntimeError("Failed to compress PDF.")

    return CompressionResult(
        input_bytes=input_size,
        output_bytes=output_size,
        method=method_key,
        level=level_key,
    )


def _compress_rectal(*, input_pdf: Path, output_pdf: Path, level: str) -> int:
    qpdf = shutil.which("qpdf")
    if not qpdf:
        raise RuntimeError("qpdf is required for Rectal compression. Please install qpdf.")

    compression_level = {"low": "6", "medium": "8", "high": "9"}.get(level, "8")
    candidates = [
        [f"--compression-level={compression_level}", "--object-streams=generate", "--stream-data=compress", "--recompress-flate"],
        ["--object-streams=generate", "--stream-data=compress", "--recompress-flate"],
        ["--object-streams=generate", "--stream-data=compress"],
        ["--stream-data=compress"],
    ]

    last_error = "Failed to compress PDF."
    for args in candidates:
        if output_pdf.exists():
            output_pdf.unlink()
        cmd = [qpdf, *args, str(input_pdf), str(output_pdf)]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0 and output_pdf.exists():
            return output_pdf.stat().st_size
        detail = (result.stderr or "").strip()
        if detail:
            last_error = detail

    raise RuntimeError(last_error or "Failed to compress PDF.")


def _compress_photon(*, input_pdf: Path, output_pdf: Path, level: str) -> int:
    gs = shutil.which("gs")
    if not gs:
        raise RuntimeError("Ghostscript is required for Photon compression. Please install ghostscript.")

    settings = {
        "low": {"preset": "/printer", "color": 200, "gray": 200, "mono": 400, "jpegq": 80},
        "medium": {"preset": "/ebook", "color": 150, "gray": 150, "mono": 300, "jpegq": 60},
        "high": {"preset": "/screen", "color": 96, "gray": 96, "mono": 200, "jpegq": 40},
    }
    cfg = settings.get(level) or settings["medium"]

    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={cfg['preset']}",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={cfg['color']}",
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={cfg['gray']}",
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={cfg['mono']}",
        "-dAutoFilterColorImages=false",
        "-dAutoFilterGrayImages=false",
        "-dColorImageFilter=/DCTEncode",
        "-dGrayImageFilter=/DCTEncode",
        "-dMonoImageFilter=/CCITTFaxEncode",
        f"-dJPEGQ={cfg['jpegq']}",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        f"-sOutputFile={output_pdf}",
        str(input_pdf),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(detail or "Failed to compress PDF.")
    if not output_pdf.exists():
        raise RuntimeError("Failed to compress PDF.")
    return output_pdf.stat().st_size


def reorder_pdf(input_file: Path, order: list[int], output_file: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF page reordering. Install it with `pip install pypdf`.") from exc

    with input_file.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        if len(order) != total:
            raise ValueError(f"Order length must match page count ({total}).")

        if set(order) != set(range(total)):
            raise ValueError("Order must be a permutation of all pages.")

        writer = PdfWriter()
        for idx in order:
            writer.add_page(reader.pages[idx])

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("wb") as out:
            writer.write(out)


def remove_pdf_pages(input_pdf: Path, remove_pages: Iterable[int], output_file: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for removing PDF pages. Install it with `pip install pypdf`.") from exc

    output_file.parent.mkdir(parents=True, exist_ok=True)
    remove_set = {int(p) for p in remove_pages if int(p) > 0}

    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        remove_set = {p for p in remove_set if 1 <= p <= total}
        if not remove_set:
            raise ValueError("Select at least one page to remove.")

        writer = PdfWriter()
        for idx in range(total):
            page_number = idx + 1
            if page_number in remove_set:
                continue
            writer.add_page(reader.pages[idx])

        if not writer.pages:
            raise ValueError("Cannot remove all pages.")

        with output_file.open("wb") as out:
            writer.write(out)


def add_page_numbers(
    *,
    input_pdf: Path,
    output_pdf: Path,
    start_page: int,
    end_page: int,
    position: str,
    font_name: str,
    font_size: int,
    font_color: str,
) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for adding page numbers. Install it with `pip install pypdf`.") from exc
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("reportlab is required for page numbering. Install it with `pip install reportlab`.") from exc

    allowed_positions = {
        "top-left",
        "top-center",
        "top-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    }
    allowed_fonts = {
        "Helvetica",
        "Helvetica-Bold",
        "Times-Roman",
        "Times-Bold",
        "Courier",
    }

    pos_key = (position or "bottom-right").strip().lower()
    if pos_key not in allowed_positions:
        raise ValueError("Invalid page-number position.")

    font_key = (font_name or "Helvetica").strip()
    if font_key not in allowed_fonts:
        raise ValueError("Unsupported font.")

    try:
        font_size_i = int(font_size)
    except (TypeError, ValueError):
        raise ValueError("Font size must be a number.")
    if font_size_i < 6 or font_size_i > 72:
        raise ValueError("Font size must be between 6 and 72.")

    def _parse_hex_color(value: str) -> tuple[float, float, float]:
        text = (value or "").strip().lstrip("#")
        if len(text) == 3:
            text = "".join([ch * 2 for ch in text])
        if len(text) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", text):
            raise ValueError("Invalid font color.")
        r = int(text[0:2], 16) / 255.0
        g = int(text[2:4], 16) / 255.0
        b = int(text[4:6], 16) / 255.0
        return r, g, b

    color_rgb = _parse_hex_color(font_color)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        if start_page < 1 or end_page < 1 or start_page > end_page:
            raise ValueError("Page ranges must be in ascending order and start at 1.")
        if end_page > total:
            raise ValueError(f"End page exceeds page count ({total}).")

        writer = PdfWriter()
        margin = 36

        for idx, page in enumerate(reader.pages, start=1):
            if start_page <= idx <= end_page:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                label = str(idx)
                text_width = pdfmetrics.stringWidth(label, font_key, font_size_i)

                if pos_key.endswith("left"):
                    x = margin
                elif pos_key.endswith("right"):
                    x = max(margin, width - margin - text_width)
                else:
                    x = max(margin, (width - text_width) / 2)

                if pos_key.startswith("top"):
                    y = max(margin, height - margin - font_size_i)
                else:
                    y = margin

                packet = BytesIO()
                c = canvas.Canvas(packet, pagesize=(width, height))
                c.setFont(font_key, font_size_i)
                c.setFillColorRGB(*color_rgb)
                c.drawString(x, y, label)
                c.save()
                packet.seek(0)
                overlay = PdfReader(packet).pages[0]
                page.merge_page(overlay)

            writer.add_page(page)

        with output_pdf.open("wb") as out:
            writer.write(out)


def count_pdf_pages(input_file: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF tools. Install it with `pip install pypdf`.") from exc

    with input_file.open("rb") as fh:
        reader = PdfReader(fh)
        return len(reader.pages)


def generate_pdf_first_page_thumbnail(
    *,
    input_pdf: Path,
    output_png: Path,
    max_dim_px: int = 520,
) -> None:
    """Generate a PNG thumbnail for the first page of a PDF using poppler's pdftoppm.

    `max_dim_px` is treated as the maximum pixel dimension (largest side).
    """

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is required for thumbnail previews (poppler). Please install poppler-utils.")
    max_dim = int(max_dim_px) if int(max_dim_px) > 0 else 520

    output_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_png.with_suffix("")

    cmd = [
        pdftoppm,
        "-png",
        "-singlefile",
        "-scale-to",
        str(max_dim),
        str(input_pdf),
        str(prefix),
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if not output_png.exists():
        raise RuntimeError("Failed to generate thumbnail.")


def generate_pdf_page_thumbnail(
    *,
    input_pdf: Path,
    output_png: Path,
    page_number: int,
    max_dim_px: int = 520,
) -> None:
    """Generate a PNG thumbnail for a specific page of a PDF using pdftoppm."""

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is required for thumbnail previews (poppler). Please install poppler-utils.")
    if page_number < 1:
        raise ValueError("Page number must be 1 or higher.")
    max_dim = int(max_dim_px) if int(max_dim_px) > 0 else 520

    output_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_png.with_suffix("")
    cmd = [
        pdftoppm,
        "-png",
        "-singlefile",
        "-f",
        str(int(page_number)),
        "-l",
        str(int(page_number)),
        "-scale-to",
        str(max_dim),
        str(input_pdf),
        str(prefix),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if not output_png.exists():
        raise RuntimeError("Failed to generate thumbnail.")


def generate_pdf_thumbnails(
    *,
    input_pdf: Path,
    output_dir: Path,
    page_count: int,
    max_dim_px: int = 520,
    status_hook: Optional[Callable[[int], None]] = None,
) -> None:
    """Generate PNG thumbnails for each page of a PDF using poppler's pdftoppm.

    `max_dim_px` is treated as the maximum pixel dimension (largest side).

    Produces files with a `page-<n>.png` suffix (note: for multi-digit page counts,
    pdftoppm may zero-pad the page number, e.g. `page-01.png`).
    """

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is required for thumbnail previews (poppler). Please install poppler-utils.")
    if page_count <= 0:
        raise ValueError("PDF has no pages.")
    # pdftoppm's `-scale-to-x` only changes the horizontal scale (it does NOT preserve aspect ratio).
    # Use `-scale-to` instead, which scales uniformly to fit within a square box.
    max_dim = int(max_dim_px) if int(max_dim_px) > 0 else 520

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"

    cmd = [
        pdftoppm,
        "-png",
        "-progress",
        "-f",
        "1",
        "-l",
        str(int(page_count)),
        "-scale-to",
        str(max_dim),
        str(input_pdf),
        str(prefix),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    last_progress = 0
    progress_re = re.compile(r"^(?P<cur>\d+)\s+(?P<total>\d+)\s+.+$")
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            raw = (line or "").strip()
            if not raw:
                continue
            match = progress_re.match(raw)
            if not match:
                continue
            cur = int(match.group("cur"))
            total = int(match.group("total")) if int(match.group("total")) > 0 else page_count
            percent = int((cur / total) * 100)
            percent = max(0, min(100, percent))
            if percent != last_progress:
                last_progress = percent
                if callable(status_hook):
                    status_hook(percent)
    finally:
        rc = proc.wait()

    if rc != 0:
        raise RuntimeError("Failed to generate page thumbnails.")


def generate_image_thumbnail(
    *,
    input_image: Path,
    output_png: Path,
    max_dim_px: int = 520,
) -> None:
    """Generate a PNG thumbnail for an image using Pillow."""

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Pillow is required for image thumbnails. Install it with `pip install pillow`.") from exc

    max_dim = int(max_dim_px) if int(max_dim_px) > 0 else 520
    output_png.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(input_image) as img:
        img = ImageOps.exif_transpose(img)
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img.thumbnail((max_dim, max_dim), resample)
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGBA")
        img.save(output_png, format="PNG")

    if not output_png.exists():
        raise RuntimeError("Failed to generate image thumbnail.")


def images_to_pdf(image_paths: Iterable[Path], output_pdf: Path) -> None:
    """Convert images to a single PDF using img2pdf (if available) or Pillow."""

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    paths = list(image_paths)
    if not paths:
        raise ValueError("Select at least one image.")

    try:
        import img2pdf  # type: ignore
    except Exception:
        img2pdf = None

    if img2pdf:
        pdf_bytes = img2pdf.convert([str(p) for p in paths])
        output_pdf.write_bytes(pdf_bytes)
        if not output_pdf.exists():
            raise RuntimeError("Failed to convert images to PDF.")
        return

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Pillow is required for image conversion. Install it with `pip install pillow`.") from exc

    images = []
    try:
        for path in paths:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    rgba = img.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[-1])
                    images.append(bg)
                else:
                    images.append(img.convert("RGB"))

        if not images:
            raise ValueError("Select at least one image.")

        base = images[0]
        rest = images[1:]
        base.save(output_pdf, "PDF", save_all=True, append_images=rest)
    finally:
        for img in images:
            with suppress(Exception):
                img.close()

    if not output_pdf.exists():
        raise RuntimeError("Failed to convert images to PDF.")


TESSERACT_LANGUAGE_NAMES: dict[str, str] = {
    "afr": "Afrikaans",
    "amh": "Amharic",
    "ara": "Arabic",
    "asm": "Assamese",
    "aze": "Azerbaijani",
    "aze_cyrl": "Azerbaijani (Cyrillic)",
    "bel": "Belarusian",
    "ben": "Bengali",
    "bod": "Tibetan",
    "bos": "Bosnian",
    "bre": "Breton",
    "bul": "Bulgarian",
    "cat": "Catalan",
    "ceb": "Cebuano",
    "ces": "Czech",
    "chi": "Chinese",
    "chi_sim": "Chinese (Simplified)",
    "chi_sim_vert": "Chinese (Simplified, Vertical)",
    "chi_tra": "Chinese (Traditional)",
    "chi_tra_vert": "Chinese (Traditional, Vertical)",
    "chr": "Cherokee",
    "cos": "Corsican",
    "cym": "Welsh",
    "dan": "Danish",
    "dan_frak": "Danish (Fraktur)",
    "deu": "German",
    "deu_frak": "German (Fraktur)",
    "div": "Dhivehi",
    "dzo": "Dzongkha",
    "ell": "Greek",
    "eng": "English",
    "enm": "Middle English",
    "epo": "Esperanto",
    "equ": "Equations (Math)",
    "est": "Estonian",
    "eus": "Basque",
    "fao": "Faroese",
    "fas": "Persian (Farsi)",
    "fil": "Filipino",
    "fin": "Finnish",
    "fra": "French",
    "frk": "Fraktur",
    "frm": "Middle French",
    "fry": "Western Frisian",
    "gla": "Scottish Gaelic",
    "gle": "Irish",
    "glg": "Galician",
    "grc": "Ancient Greek",
    "guj": "Gujarati",
    "hat": "Haitian Creole",
    "heb": "Hebrew",
    "hin": "Hindi",
    "hrv": "Croatian",
    "hun": "Hungarian",
    "hye": "Armenian",
    "iku": "Inuktitut",
    "ind": "Indonesian",
    "isl": "Icelandic",
    "ita": "Italian",
    "ita_old": "Italian (Old)",
    "jav": "Javanese",
    "jpn": "Japanese",
    "jpn_vert": "Japanese (Vertical)",
    "kan": "Kannada",
    "kat": "Georgian",
    "kat_old": "Georgian (Old)",
    "kaz": "Kazakh",
    "khm": "Khmer",
    "kir": "Kyrgyz",
    "kmr": "Kurdish (Kurmanji)",
    "kor": "Korean",
    "kor_vert": "Korean (Vertical)",
    "lao": "Lao",
    "lat": "Latin",
    "lav": "Latvian",
    "lit": "Lithuanian",
    "ltz": "Luxembourgish",
    "mal": "Malayalam",
    "mar": "Marathi",
    "mkd": "Macedonian",
    "mlt": "Maltese",
    "mon": "Mongolian",
    "mri": "Maori",
    "msa": "Malay",
    "mya": "Burmese",
    "nep": "Nepali",
    "nld": "Dutch",
    "nor": "Norwegian",
    "oci": "Occitan",
    "ori": "Odia (Oriya)",
    "osd": "Orientation and Script Detection",
    "pan": "Punjabi",
    "pol": "Polish",
    "por": "Portuguese",
    "pus": "Pashto",
    "que": "Quechua",
    "ron": "Romanian",
    "rus": "Russian",
    "san": "Sanskrit",
    "sin": "Sinhala",
    "slk": "Slovak",
    "slk_frak": "Slovak (Fraktur)",
    "slv": "Slovenian",
    "snd": "Sindhi",
    "spa": "Spanish",
    "spa_old": "Spanish (Old)",
    "sqi": "Albanian",
    "srp": "Serbian",
    "srp_latn": "Serbian (Latin)",
    "sun": "Sundanese",
    "swa": "Swahili",
    "swe": "Swedish",
    "syr": "Syriac",
    "tam": "Tamil",
    "tat": "Tatar",
    "tel": "Telugu",
    "tgk": "Tajik",
    "tgl": "Tagalog",
    "tha": "Thai",
    "tir": "Tigrinya",
    "ton": "Tonga",
    "tur": "Turkish",
    "uig": "Uyghur",
    "ukr": "Ukrainian",
    "urd": "Urdu",
    "uzb": "Uzbek",
    "uzb_cyrl": "Uzbek (Cyrillic)",
    "vie": "Vietnamese",
    "yid": "Yiddish",
    "yor": "Yoruba",
}


def _tesseract_language_label(code: str) -> str:
    if not code:
        return code
    if code in TESSERACT_LANGUAGE_NAMES:
        return TESSERACT_LANGUAGE_NAMES[code]
    if "_" in code:
        base, *suffixes = code.split("_")
        base_name = TESSERACT_LANGUAGE_NAMES.get(base, base)
        suffix_map = {
            "vert": "Vertical",
            "cyrl": "Cyrillic",
            "latn": "Latin",
            "old": "Old",
            "frak": "Fraktur",
            "sim": "Simplified",
            "tra": "Traditional",
        }
        suffix_label = ", ".join(suffix_map.get(s, s.upper()) for s in suffixes if s)
        if suffix_label:
            return f"{base_name} ({suffix_label})"
    return code


def tesseract_languages() -> list[dict[str, str]]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("Tesseract OCR is required. Please install tesseract and language packs.")

    result = subprocess.run(
        [tesseract, "--list-langs"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "Unable to list OCR languages.")

    langs: list[str] = []
    for line in output.splitlines():
        item = line.strip()
        if not item:
            continue
        if item.lower().startswith("list of available languages"):
            continue
        langs.append(item)

    if not langs:
        raise RuntimeError("No OCR languages found. Install Tesseract language packs.")

    seen: set[str] = set()
    unique: list[str] = []
    for lang in langs:
        if lang in seen:
            continue
        seen.add(lang)
        unique.append(lang)

    display_list: list[dict[str, str]] = []
    for lang in unique:
        display_list.append({"code": lang, "name": _tesseract_language_label(lang)})

    display_list.sort(
        key=lambda item: (
            1 if item["code"] == "osd" else 0,
            item["name"].lower(),
        )
    )
    return display_list


def ocr_pdf(
    *,
    input_pdf: Path,
    output_pdf: Path,
    languages: Iterable[str],
    dpi: int = 300,
    binarize: bool = False,
    page_count: Optional[int] = None,
    status_hook: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Run OCR on a PDF using Tesseract, producing a searchable PDF."""

    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("Tesseract OCR is required. Please install tesseract and language packs.")

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is required for OCR. Please install poppler-utils.")

    lang_list = [str(lang).strip() for lang in languages if str(lang).strip()]
    if not lang_list:
        raise ValueError("Select at least one OCR language.")

    try:
        dpi_value = int(dpi)
    except (TypeError, ValueError):
        raise ValueError("DPI must be a number.")
    if dpi_value < 72 or dpi_value > 600:
        raise ValueError("DPI must be between 72 and 600.")

    total_pages = int(page_count or 0)
    if total_pages <= 0:
        total_pages = count_pdf_pages(input_pdf)
    if total_pages <= 0:
        raise ValueError("The selected PDF has no pages.")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_pdf.parent / "ocr_work"
    with suppress(Exception):
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    lang_arg = "+".join(lang_list)

    def _binarize_image(image_path: Path) -> None:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("Pillow is required for binarizing OCR images. Install it with `pip install pillow`.") from exc

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            gray = img.convert("L")
            gray = ImageOps.autocontrast(gray)

            hist = gray.histogram()
            total = sum(hist)
            if total <= 0:
                threshold = 180
            else:
                sum_total = 0
                for i, count in enumerate(hist):
                    sum_total += i * count

                sum_bg = 0
                weight_bg = 0
                max_var = -1.0
                threshold = 180
                for i in range(256):
                    weight_bg += hist[i]
                    if weight_bg == 0:
                        continue
                    weight_fg = total - weight_bg
                    if weight_fg == 0:
                        break
                    sum_bg += i * hist[i]
                    mean_bg = sum_bg / weight_bg
                    mean_fg = (sum_total - sum_bg) / weight_fg
                    var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
                    if var_between > max_var:
                        max_var = var_between
                        threshold = i

            bw = gray.point(lambda p: 255 if p > threshold else 0)
            bw.save(image_path, format="PNG")

    try:
        for page in range(1, total_pages + 1):
            prefix = work_dir / f"page-{page:04d}"
            image_path = prefix.with_suffix(".png")
            cmd = [
                pdftoppm,
                "-r",
                str(dpi_value),
                "-png",
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                str(input_pdf),
                str(prefix),
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0 or not image_path.exists():
                detail = (result.stderr or "").strip()
                raise RuntimeError(detail or "Failed to render PDF pages for OCR.")

            if binarize:
                _binarize_image(image_path)

            out_base = work_dir / f"ocr-{page:04d}"
            cmd = [
                tesseract,
                str(image_path),
                str(out_base),
                "-l",
                lang_arg,
                "--dpi",
                str(dpi_value),
                "pdf",
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                detail = (result.stderr or "").strip()
                raise RuntimeError(detail or "Tesseract OCR failed.")

            ocr_page = out_base.with_suffix(".pdf")
            if not ocr_page.exists():
                raise RuntimeError("Failed to generate OCR output for a page.")

            if callable(status_hook):
                status_hook(page, total_pages)

        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("pypdf is required for OCR output. Install it with `pip install pypdf`.") from exc

        writer = PdfWriter()
        for page in range(1, total_pages + 1):
            ocr_page = work_dir / f"ocr-{page:04d}.pdf"
            with ocr_page.open("rb") as fh:
                reader = PdfReader(fh)
                if not reader.pages:
                    raise RuntimeError("OCR output is missing pages.")
                for pdf_page in reader.pages:
                    writer.add_page(pdf_page)

        with output_pdf.open("wb") as out:
            writer.write(out)
        if not output_pdf.exists():
            raise RuntimeError("Failed to create OCR PDF.")
    finally:
        with suppress(Exception):
            shutil.rmtree(work_dir, ignore_errors=True)

def split_pdf_ranges(input_pdf: Path, ranges: list[tuple[int, int]], output_dir: Path) -> list[Path]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF splitting. Install it with `pip install pypdf`.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        outputs: list[Path] = []
        for idx, (start, end) in enumerate(ranges, start=1):
            if start < 1 or end < 1 or start > end:
                raise ValueError("Ranges must be in ascending order and start at 1.")
            if end > total:
                raise ValueError("Range exceeds PDF page count.")
            writer = PdfWriter()
            for page_index in range(start - 1, end):
                writer.add_page(reader.pages[page_index])
            filename = f"range_{idx:02d}_{start:03d}-{end:03d}.pdf"
            out_path = output_dir / filename
            with out_path.open("wb") as out:
                writer.write(out)
            outputs.append(out_path)
        return outputs


def split_pdf_selected_pages(input_pdf: Path, pages: list[int], output_file: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF splitting. Install it with `pip install pypdf`.") from exc

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        writer = PdfWriter()
        for page_number in pages:
            if page_number < 1 or page_number > total:
                raise ValueError("Selected page exceeds PDF page count.")
            writer.add_page(reader.pages[page_number - 1])

        if not writer.pages:
            raise ValueError("Select at least one page.")

        with output_file.open("wb") as out:
            writer.write(out)


def split_pdf_all_pages(input_pdf: Path, output_dir: Path) -> list[Path]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF splitting. Install it with `pip install pypdf`.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        outputs: list[Path] = []
        for page_index in range(total):
            writer = PdfWriter()
            writer.add_page(reader.pages[page_index])
            filename = f"page_{page_index + 1:03d}.pdf"
            out_path = output_dir / filename
            with out_path.open("wb") as out:
                writer.write(out)
            outputs.append(out_path)
        return outputs


def split_pdf_odd_even(input_pdf: Path, *, odd: bool, output_file: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF splitting. Install it with `pip install pypdf`.") from exc

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with input_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        total = len(reader.pages)
        if total <= 0:
            raise ValueError("The selected PDF has no pages.")

        writer = PdfWriter()
        start = 0 if odd else 1
        for page_index in range(start, total, 2):
            writer.add_page(reader.pages[page_index])

        if not writer.pages:
            raise ValueError("No pages matched the selection.")

        with output_file.open("wb") as out:
            writer.write(out)


def zip_paths(paths: list[Path], zip_path: Path) -> None:
    if not paths:
        raise ValueError("No files to add to the ZIP.")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for path in paths:
            if not path.exists():
                continue
            zipf.write(path, arcname=path.name)
