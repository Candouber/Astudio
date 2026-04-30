"""
Task attachment storage and lightweight extraction helpers.
"""
from __future__ import annotations

import csv
import html
import json
import re
import struct
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import UploadFile

from storage.sandbox_store import SandboxStore

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_ATTACHMENTS_PER_TASK = 8
MAX_TEXT_CHARS = 80_000
ALLOWED_EXTENSIONS = {
    ".csv",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".txt",
    ".webp",
    ".xlsx",
}

MANIFEST_PATH = ".astudio/attachments.json"
LEGACY_MANIFEST_PATH = ".antit/attachments.json"


class AttachmentError(ValueError):
    pass


def _safe_filename(name: str) -> str:
    raw = Path(name or "attachment").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return safe or "attachment"


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


async def save_task_attachments(task_id: str, files: list[UploadFile]) -> list[dict[str, Any]]:
    incoming = [f for f in files if f.filename]
    if not incoming:
        return []
    if len(incoming) > MAX_ATTACHMENTS_PER_TASK:
        raise AttachmentError(f"You can upload at most {MAX_ATTACHMENTS_PER_TASK} attachments per task.")

    sandbox, _ = await SandboxStore().ensure_for_task(task_id)
    root = Path(sandbox.path)
    uploads_dir = root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    existing = load_task_attachments(task_id)
    saved: list[dict[str, Any]] = []
    for upload in incoming:
        filename = _safe_filename(upload.filename or "attachment")
        ext = _extension(filename)
        if ext not in ALLOWED_EXTENSIONS:
            raise AttachmentError(f"Unsupported attachment type: {filename}")

        data = await upload.read()
        size = len(data)
        if size > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(f"Attachment too large: {filename}. Maximum size is {MAX_ATTACHMENT_BYTES // 1024 // 1024}MB.")

        att_id = f"att_{uuid.uuid4().hex[:8]}"
        stored_name = f"{att_id}_{filename}"
        rel_path = f"uploads/{stored_name}"
        target = uploads_dir / stored_name
        target.write_bytes(data)

        item = {
            "id": att_id,
            "filename": filename,
            "content_type": upload.content_type or _guess_content_type(ext),
            "extension": ext,
            "size": size,
            "path": rel_path,
            "summary": summarize_attachment(root / rel_path, filename, upload.content_type or ""),
        }
        saved.append(item)

    _write_manifest(root, [*existing, *saved])
    return saved


def load_task_attachments(task_id: str) -> list[dict[str, Any]]:
    sandbox = _get_sandbox_for_task_sync(task_id)
    if not sandbox:
        return []
    root = Path(sandbox["path"])
    manifest = root / MANIFEST_PATH
    if not manifest.exists():
        manifest = root / LEGACY_MANIFEST_PATH
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def task_has_attachments(task_id: str) -> bool:
    return bool(load_task_attachments(task_id))


def build_attachment_prompt(task_id: str) -> str:
    attachments = load_task_attachments(task_id)
    if not attachments:
        return ""
    lines = [
        "\n\n[User uploaded attachments]",
        "This task includes the following files. During execution, prefer attachment tools: list_attachments, read_uploaded_file, read_excel_sheet, read_pdf_text, image_metadata.",
    ]
    for item in attachments:
        lines.append(
            f"- id={item['id']} name={item['filename']} type={item.get('content_type') or item.get('extension')} "
            f"size={item.get('size', 0)} path={item.get('path')}"
        )
        if item.get("summary"):
            lines.append(f"  Summary: {item['summary']}")
    return "\n".join(lines)


def get_attachment(task_id: str, attachment_id: str) -> tuple[dict[str, Any], Path]:
    sandbox = _get_sandbox_for_task_sync(task_id)
    if not sandbox:
        raise AttachmentError("Task sandbox does not exist.")
    root = Path(sandbox["path"])
    for item in load_task_attachments(task_id):
        if item.get("id") == attachment_id or item.get("filename") == attachment_id or item.get("path") == attachment_id:
            target = (root / item["path"]).resolve()
            root_resolved = root.resolve()
            try:
                target.relative_to(root_resolved)
            except ValueError as exc:
                raise AttachmentError("Attachment path escapes the sandbox.") from exc
            if not target.exists() or not target.is_file():
                raise AttachmentError("Attachment file does not exist.")
            return item, target
    raise AttachmentError(f"Attachment not found: {attachment_id}")


def summarize_attachment(path: Path, filename: str, content_type: str = "") -> str:
    ext = _extension(filename)
    try:
        if ext == ".csv":
            return _summarize_csv(path)
        if ext == ".xlsx":
            return _summarize_xlsx(path)
        if ext == ".pdf":
            return _summarize_pdf(path)
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            meta = image_info(path)
            dims = f"{meta.get('width')}x{meta.get('height')}" if meta.get("width") else "unknown dimensions"
            return f"Image, format {meta.get('format') or ext.lstrip('.')}, {dims}"
        if ext in {".txt", ".md", ".json"} or content_type.startswith("text/"):
            text = path.read_text(encoding="utf-8", errors="replace")[:500]
            return "Text preview: " + re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        return f"Automatic summary failed: {e}"
    return "Saved; no automatic summary available."


def read_text_attachment(path: Path, max_chars: int = MAX_TEXT_CHARS) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _truncate(text, max_chars)


def read_tabular_attachment(path: Path, sheet_name: str | None = None, max_rows: int = 30) -> str:
    ext = path.suffix.lower()
    if ext == ".csv":
        return _read_csv(path, max_rows=max_rows)
    if ext == ".xlsx":
        return _read_xlsx(path, sheet_name=sheet_name, max_rows=max_rows)
    raise AttachmentError("This attachment is not a supported Excel/CSV file.")


def read_pdf_attachment(path: Path, max_pages: int = 10, max_chars: int = MAX_TEXT_CHARS) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for idx, page in enumerate(reader.pages[:max_pages], start=1):
            pages.append(f"\n--- page {idx} ---\n{page.extract_text() or ''}")
        return _truncate("\n".join(pages).strip() or "[No text extracted]", max_chars)
    except Exception:
        text = _extract_pdf_text_fallback(path, max_pages=max_pages)
        return _truncate(text or "[No text extracted; scanned PDFs need OCR or vision model support]", max_chars)


def image_info(path: Path) -> dict[str, Any]:
    data = path.read_bytes()[:512 * 1024]
    ext = path.suffix.lower()
    if ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return {"format": "png", "width": width, "height": height}
    if ext in {".jpg", ".jpeg"}:
        dims = _jpeg_size(data)
        return {"format": "jpeg", **dims}
    if ext == ".webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return {"format": "webp", **_webp_size(data)}
    return {"format": ext.lstrip(".") or "unknown", "width": None, "height": None}


def _write_manifest(root: Path, items: list[dict[str, Any]]) -> None:
    manifest = root / MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_sandbox_for_task_sync(task_id: str):
    import sqlite3

    from storage.database import DB_PATH

    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM sandboxes WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _guess_content_type(ext: str) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain",
        ".webp": "image/webp",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _summarize_csv(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return "Empty CSV file"
    return f"CSV, about {len(rows)} rows, columns: {', '.join(rows[0][:12])}"


def _read_csv(path: Path, max_rows: int = 30) -> str:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f))[:max_rows]
    return _format_rows(rows)


def _summarize_xlsx(path: Path) -> str:
    sheets = _xlsx_sheet_names(path)
    return f"Excel workbook, sheets: {', '.join(sheets[:12])}" if sheets else "Excel workbook"


def _read_xlsx(path: Path, sheet_name: str | None = None, max_rows: int = 30) -> str:
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = _xlsx_sheets(zf)
        if not sheets:
            raise AttachmentError("No worksheet found.")
        target = sheets[0]
        if sheet_name:
            target = next((s for s in sheets if s["name"] == sheet_name), None)
            if target is None:
                raise AttachmentError(f"Worksheet not found: {sheet_name}")
        rows = _xlsx_rows(zf, target["path"], shared, max_rows=max_rows)
    return f"Sheet: {target['name']}\n" + _format_rows(rows)


def _xlsx_sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return [s["name"] for s in _xlsx_sheets(zf)]


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out = []
    for si in root.iter():
        if _local_name(si.tag) != "si":
            continue
        parts = [node.text or "" for node in si.iter() if _local_name(node.tag) == "t"]
        out.append("".join(parts))
    return out


def _xlsx_sheets(zf: zipfile.ZipFile) -> list[dict[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {}
    for rel in rels:
        target = rel.attrib.get("Target", "")
        rel_map[rel.attrib.get("Id")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    sheets = []
    for sheet in workbook.iter():
        if _local_name(sheet.tag) != "sheet":
            continue
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        path = rel_map.get(rel_id or "")
        if path:
            sheets.append({"name": sheet.attrib.get("name", "Sheet"), "path": path})
    return sheets


def _xlsx_rows(zf: zipfile.ZipFile, sheet_path: str, shared: list[str], max_rows: int) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows = []
    for row in root.iter():
        if _local_name(row.tag) != "row":
            continue
        values: dict[int, str] = {}
        for cell in row:
            if _local_name(cell.tag) != "c":
                continue
            idx = _cell_col_index(cell.attrib.get("r", "A1"))
            values[idx] = _xlsx_cell_value(cell, shared)
        if values:
            max_idx = max(values)
            rows.append([values.get(i, "") for i in range(max_idx + 1)])
        if len(rows) >= max_rows:
            break
    return rows


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    text = ""
    for node in cell.iter():
        if _local_name(node.tag) in {"v", "t"} and node.text is not None:
            text = node.text
            break
    if cell_type == "s":
        try:
            return shared[int(text)]
        except Exception:
            return text
    return text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cell_col_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref)
    if not letters:
        return 0
    idx = 0
    for ch in letters.group(0).upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _format_rows(rows: list[list[str]]) -> str:
    if not rows:
        return "[No data]"
    return "\n".join("\t".join(str(v) for v in row) for row in rows)


def _summarize_pdf(path: Path) -> str:
    data = path.read_bytes()[:4096]
    pages = len(re.findall(rb"/Type\s*/Page\b", path.read_bytes()))
    return f"PDF document, about {pages or 'unknown'} pages, file header {data[:8]!r}"


def _extract_pdf_text_fallback(path: Path, max_pages: int = 10) -> str:
    data = path.read_bytes()
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.S):
        raw = match.group(1)
        for payload in (raw, _try_zlib(raw)):
            if not payload:
                continue
            chunks.extend(_extract_pdf_strings(payload))
        if len(chunks) > max_pages * 80:
            break
    if not chunks:
        chunks = _extract_pdf_strings(data)
    return "\n".join(chunks)


def _try_zlib(raw: bytes) -> bytes:
    try:
        return zlib.decompress(raw)
    except Exception:
        return b""


def _extract_pdf_strings(data: bytes) -> list[str]:
    text = data.decode("latin-1", errors="ignore")
    strings = []
    for raw in re.findall(r"\((?:\\.|[^\\)]){2,}\)", text):
        val = raw[1:-1]
        val = val.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        val = html.unescape(val)
        if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", val):
            strings.append(val)
    return strings


def _jpeg_size(data: bytes) -> dict[str, int | None]:
    idx = 2
    while idx + 9 < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue
        marker = data[idx + 1]
        length = int.from_bytes(data[idx + 2:idx + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[idx + 5:idx + 7], "big")
            width = int.from_bytes(data[idx + 7:idx + 9], "big")
            return {"width": width, "height": height}
        idx += 2 + max(length, 2)
    return {"width": None, "height": None}


def _webp_size(data: bytes) -> dict[str, int | None]:
    if data[12:16] == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return {"width": width, "height": height}
    return {"width": None, "height": None}


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + f"\n... [Truncated to {max_chars} characters]"
