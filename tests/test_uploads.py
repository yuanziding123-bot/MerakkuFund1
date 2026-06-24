"""Ask upload handling — classify / extract / cache / multimodal assembly."""
from __future__ import annotations

import base64

import pytest

from polyagents.web import uploads
from polyagents.web.uploads import (
    UploadCache, build_message_content, classify, extract, public_view,
)


def test_classify_by_extension():
    assert classify("notes.md") == "text"
    assert classify("data.CSV") == "text"
    assert classify("chart.png") == "image"
    assert classify("photo.JPG") == "image"
    assert classify("report.pdf") == "pdf"
    assert classify("archive.zip") is None
    assert classify("noext") is None


def test_extract_text_file():
    rec = extract("notes.md", b"# hello\nworld")
    assert rec["kind"] == "text" and rec["text"] == "# hello\nworld"
    assert rec["chars"] == 13 and rec["truncated"] is False


def test_extract_truncates_long_text():
    rec = extract("big.txt", b"x" * (uploads.MAX_CHARS + 500))
    assert rec["truncated"] is True and rec["text"].endswith("…(truncated)")
    assert rec["chars"] <= uploads.MAX_CHARS + 20


def test_extract_image_is_base64():
    data = b"\x89PNG\r\n\x1a\n fake png bytes"
    rec = extract("c.png", data)
    assert rec["kind"] == "image" and rec["media_type"] == "image/png"
    assert base64.b64decode(rec["b64"]) == data


def test_extract_pdf_uses_extractor(monkeypatch):
    monkeypatch.setattr(uploads, "_extract_pdf", lambda d: ("PDF TEXT", 3))
    rec = extract("report.pdf", b"%PDF-1.4 ...")
    assert rec["kind"] == "pdf" and rec["text"] == "PDF TEXT" and rec["pages"] == 3


def test_extract_rejects_unsupported_and_oversize():
    with pytest.raises(ValueError):
        extract("x.zip", b"...")
    with pytest.raises(ValueError):
        extract("big.txt", b"x" * (uploads.MAX_BYTES + 1))


def test_cache_put_get_and_eviction():
    c = UploadCache(max_items=2)
    a = c.put({"name": "a", "kind": "text", "size": 1})
    b = c.put({"name": "b", "kind": "text", "size": 1})
    cc = c.put({"name": "c", "kind": "text", "size": 1})   # evicts a
    assert c.get(b)["name"] == "b" and c.get(cc)["name"] == "c"
    assert c.get(a) is None


def test_public_view_hides_payload():
    rec = extract("c.png", b"\x89PNG fake")
    pv = public_view("f_1", rec)
    assert pv["kind"] == "image" and "b64" not in pv and pv["media_type"] == "image/png"
    rec2 = extract("n.md", b"hello")
    pv2 = public_view("f_2", rec2)
    assert pv2["chars"] == 5 and "text" not in pv2


def test_build_content_text_only_returns_string():
    recs = [{"kind": "text", "name": "n.md", "text": "DATA"}]
    out = build_message_content("explain this", recs)
    assert isinstance(out, str)
    assert "[file: n.md]" in out and "DATA" in out and out.endswith("explain this")


def test_build_content_with_image_returns_blocks():
    recs = [{"kind": "text", "name": "n.md", "text": "DATA"},
            {"kind": "image", "name": "c.png", "b64": "QUJD", "media_type": "image/png"}]
    out = build_message_content("look", recs)
    assert isinstance(out, list)
    assert out[0]["type"] == "text" and "[file: n.md]" in out[0]["text"]
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
