"""
Attachment tools exposed to agents.
"""
from services.attachments import (
    AttachmentError,
    get_attachment,
    image_info,
    load_task_attachments,
    read_pdf_attachment,
    read_tabular_attachment,
    read_text_attachment,
)
from tools.context import get_current_tool_context


async def file_analysis() -> str:
    """Return a short guide plus current attachment list."""
    return (
        "Attachment analysis tools are enabled. Common workflow: call list_attachments first; "
        "use read_excel_sheet for Excel/CSV, read_pdf_text for PDF, image_metadata for images, "
        "and read_uploaded_file for plain text/JSON/Markdown.\n\n"
        + await list_attachments()
    )


async def list_attachments() -> str:
    context = get_current_tool_context()
    items = load_task_attachments(context.task_id)
    if not items:
        return "[No attachments]"
    lines = ["Current task attachments:"]
    for item in items:
        lines.append(
            f"- id={item['id']} name={item['filename']} type={item.get('content_type') or item.get('extension')} "
            f"size={item.get('size', 0)} path={item.get('path')}"
        )
        if item.get("summary"):
            lines.append(f"  Summary: {item['summary']}")
    return "\n".join(lines)


async def read_uploaded_file(attachment_id: str, max_chars: int = 80000) -> str:
    context = get_current_tool_context()
    try:
        item, path = get_attachment(context.task_id, attachment_id)
        if item.get("extension") not in {".txt", ".md", ".json", ".csv"}:
            return "[Not suitable for direct read] Use read_excel_sheet for Excel, read_pdf_text for PDF, and image_metadata for images."
        return read_text_attachment(path, max_chars=max(1000, min(max_chars, 120000)))
    except AttachmentError as e:
        return f"[Error] {e}"


async def read_excel_sheet(attachment_id: str, sheet_name: str = "", max_rows: int = 30) -> str:
    context = get_current_tool_context()
    try:
        _item, path = get_attachment(context.task_id, attachment_id)
        return read_tabular_attachment(path, sheet_name=sheet_name or None, max_rows=max(1, min(max_rows, 200)))
    except AttachmentError as e:
        return f"[Error] {e}"
    except Exception as e:
        return f"[Parse failed] {e}"


async def read_pdf_text(attachment_id: str, max_pages: int = 10, max_chars: int = 80000) -> str:
    context = get_current_tool_context()
    try:
        _item, path = get_attachment(context.task_id, attachment_id)
        if path.suffix.lower() != ".pdf":
            return "[Error] This attachment is not a PDF."
        return read_pdf_attachment(
            path,
            max_pages=max(1, min(max_pages, 50)),
            max_chars=max(1000, min(max_chars, 120000)),
        )
    except AttachmentError as e:
        return f"[Error] {e}"
    except Exception as e:
        return f"[Parse failed] {e}"


async def image_metadata(attachment_id: str) -> str:
    context = get_current_tool_context()
    try:
        item, path = get_attachment(context.task_id, attachment_id)
        if item.get("extension") not in {".png", ".jpg", ".jpeg", ".webp"}:
            return "[Error] This attachment is not a supported image."
        meta = image_info(path)
        return (
            f"Image: {item['filename']}\n"
            f"Format: {meta.get('format')}\n"
            f"Width: {meta.get('width') or 'unknown'}\n"
            f"Height: {meta.get('height') or 'unknown'}\n"
            f"Size: {item.get('size', 0)} bytes"
        )
    except AttachmentError as e:
        return f"[Error] {e}"
    except Exception as e:
        return f"[Parse failed] {e}"


FILE_ANALYSIS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_analysis",
        "description": "View current task attachments and get guidance for attachment analysis tools.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LIST_ATTACHMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_attachments",
        "description": "List user-uploaded attachments for the current task, including id, filename, type, size, and automatic summary.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

READ_UPLOADED_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_uploaded_file",
        "description": "Read an uploaded text/CSV/JSON/Markdown attachment for the current task.",
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "Attachment id, filename, or relative path"},
                "max_chars": {"type": "integer", "description": "Maximum returned characters", "default": 80000},
            },
            "required": ["attachment_id"],
        },
    },
}

READ_EXCEL_SHEET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_excel_sheet",
        "description": "Read an uploaded Excel .xlsx or CSV attachment and return the first rows of a sheet. CSV ignores sheet_name.",
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "Attachment id, filename, or relative path"},
                "sheet_name": {"type": "string", "description": "Excel sheet name; empty means read the first sheet", "default": ""},
                "max_rows": {"type": "integer", "description": "Maximum rows to read", "default": 30},
            },
            "required": ["attachment_id"],
        },
    },
}

READ_PDF_TEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_pdf_text",
        "description": "Extract text from an uploaded PDF. Scanned PDFs may not extract text and may need OCR or a vision model.",
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "Attachment id, filename, or relative path"},
                "max_pages": {"type": "integer", "description": "Maximum pages to read", "default": 10},
                "max_chars": {"type": "integer", "description": "Maximum returned characters", "default": 80000},
            },
            "required": ["attachment_id"],
        },
    },
}

IMAGE_METADATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "image_metadata",
        "description": "Read uploaded image format, dimensions, and file size. Semantic image understanding requires a vision model or later OCR.",
        "parameters": {
            "type": "object",
            "properties": {"attachment_id": {"type": "string", "description": "Attachment id, filename, or relative path"}},
            "required": ["attachment_id"],
        },
    },
}
