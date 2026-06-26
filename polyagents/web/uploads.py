"""Upload handling for the Ask composer — extract file content for the agent.

MVP scope (text + image + PDF): a dropped/attached file is read, classified by
extension, and turned into something the chat model can consume:

  * text / pdf  → extracted UTF-8 text, truncated, inlined as a context block
  * image       → base64 + media type, sent as a multimodal image block

Extracted content is held in a small in-process cache keyed by id (cleared on
restart — persistence is out of MVP scope); ``/api/chat`` references attachments
by id and :func:`build_message_content` assembles the user message.

Pure/testable: classify / extract / build_message_content take bytes and dicts,
no FastAPI or network. PDF text extraction is delegated to :func:`_extract_pdf`
so tests can stub it without a real PDF.
"""
from __future__ import annotations

import base64
import uuid
from collections import OrderedDict

MAX_BYTES = 10 * 1024 * 1024          # 10 MB per file
MAX_CHARS = 20_000                    # truncate extracted text per file

TEXT_EXTS = {"txt", "md", "csv", "tsv", "json", "log", "py", "ts", "js",
             "yaml", "yml", "html", "xml", "sql"}
PDF_EXTS = {"pdf"}
IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "webp": "image/webp", "gif": "image/gif"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify(name: str) -> str | None:
    """'text' | 'image' | 'pdf' for a supported file, else None."""
    e = _ext(name)
    if e in TEXT_EXTS:
        return "text"
    if e in IMAGE_MEDIA:
        return "image"
    if e in PDF_EXTS:
        return "pdf"
    return None


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + "\n…(truncated)", True
    return text, False


def _extract_pdf(data: bytes) -> tuple[str, int]:
    """Return (text, n_pages). Isolated so tests can stub it."""
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages
    text = "\n".join((p.extract_text() or "") for p in pages)
    return text, len(pages)


def extract(name: str, data: bytes) -> dict:
    """Turn a raw upload into a record. Raises ValueError on reject."""
    kind = classify(name)
    if kind is None:
        raise ValueError(f"unsupported file type: {name}")
    if len(data) > MAX_BYTES:
        raise ValueError(f"file too large (> {MAX_BYTES // (1024*1024)}MB): {name}")

    rec: dict = {"name": name, "kind": kind, "size": len(data)}
    if kind == "text":
        text, trunc = _truncate(data.decode("utf-8", errors="replace"))
        rec.update(text=text, chars=len(text), truncated=trunc)
    elif kind == "pdf":
        raw, pages = _extract_pdf(data)
        text, trunc = _truncate(raw)
        rec.update(text=text, chars=len(text), pages=pages, truncated=trunc)
    else:  # image
        rec.update(b64=base64.b64encode(data).decode("ascii"),
                   media_type=IMAGE_MEDIA[_ext(name)])
    return rec


class UploadCache:
    """Bounded in-process id→record store (FIFO eviction). Not persistent."""

    def __init__(self, max_items: int = 60) -> None:
        self._d: "OrderedDict[str, dict]" = OrderedDict()
        self._max = max_items

    def put(self, record: dict) -> str:
        fid = "f_" + uuid.uuid4().hex[:8]
        self._d[fid] = record
        while len(self._d) > self._max:
            self._d.popitem(last=False)
        return fid

    def get(self, fid: str) -> dict | None:
        return self._d.get(fid)


def public_view(fid: str, rec: dict) -> dict:
    """The summary returned to the browser (no base64 / full text)."""
    out = {"id": fid, "name": rec["name"], "kind": rec["kind"], "size": rec["size"]}
    if rec["kind"] in ("text", "pdf"):
        out["chars"] = rec.get("chars", 0)
        out["truncated"] = rec.get("truncated", False)
    if rec["kind"] == "pdf":
        out["pages"] = rec.get("pages")
    if rec["kind"] == "image":
        out["media_type"] = rec["media_type"]
    return out


def build_message_content(text: str, records: list[dict]):
    """Assemble a chat user message from typed text + attachment records.

    Text/PDF are inlined as context blocks; images become multimodal blocks.
    Returns a plain string when there are no images, else a list of content
    blocks (the shape LangChain/ChatAnthropic accept for multimodal input).
    """
    docs = [r for r in records if r["kind"] in ("text", "pdf")]
    imgs = [r for r in records if r["kind"] == "image"]
    preamble = "".join(f"[file: {r['name']}]\n{r.get('text','')}\n\n" for r in docs)
    combined = preamble + (text or "")
    if not imgs:
        return combined
    blocks = [{"type": "text", "text": combined or "(see attached image)"}]
    for r in imgs:
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{r['media_type']};base64,{r['b64']}"},
        })
    return blocks
