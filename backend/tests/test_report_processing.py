"""Exercise actual parsers and the trust boundary around model candidates."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from app import report_processing as p

SAMPLES = Path(__file__).resolve().parents[2] / "assets" / "report-samples"


def test_real_pdf_and_scan_parsing():
    pdf = (SAMPLES / "fictional-two-page-report.pdf").read_bytes()
    scan = (SAMPLES / "fictional-first-page-scan.png").read_bytes()
    assert p.inspect_document(pdf) == {"mime_type": "application/pdf", "page_count": 2}
    assert p.inspect_document(scan) == {"mime_type": "image/png", "page_count": 1}
    page = p._child_operation(pdf, "page", 1)
    assert "虚构测试样本" in page["text"] and "132/84" in page["text"]
    assert not page["has_images"]
    assert p.render_page(scan, 1).startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        p.render_page(pdf, 3)


def test_reject_active_encrypted_oversized_and_invalid_documents():
    for data in (b"", b"abc", b"x" * (p.MAX_BYTES + 1)):
        with pytest.raises(ValueError):
            p.inspect_document(data)
    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    writer.add_js("app.alert('untrusted')")
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(ValueError):
        p.inspect_document(stream.getvalue())
    writer = PdfWriter()
    writer.add_blank_page(600, 800); writer.encrypt("test-only")
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(ValueError):
        p.inspect_document(stream.getvalue())
    writer = PdfWriter()
    for _ in range(6): writer.add_blank_page(600, 800)
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(ValueError):
        p.inspect_document(stream.getvalue())


@pytest.mark.parametrize("url", ["http://api.deepseek.com", "https://api.deepseek.com.evil.test", "https://other.test",
                                     "https://api.deepseek.com:444", "https://user@api.deepseek.com",
                                     "https://api.deepseek.com/path", "https://api.deepseek.com?secret=x"])
def test_provider_destination_is_allowlisted(monkeypatch, url):
    monkeypatch.setattr(p, "settings", SimpleNamespace(deepseek_base_url=url, app_env="production", deepseek_api_key="fake"))
    with pytest.raises(ValueError): p.provider_base()


def _mock_model(monkeypatch, *, malformed=False):
    monkeypatch.setattr(p, "inspect_document", lambda data: {"mime_type": "image/png", "page_count": 1})
    monkeypatch.setattr(p, "_child_operation", lambda *args: {"text": "", "image": "fake-base64", "has_images": True})
    monkeypatch.setattr(p, "settings", SimpleNamespace(deepseek_base_url="https://api.deepseek.com", app_env="test",
                                                     deepseek_api_key="synthetic-secret", deepseek_model="deepseek-flash"))
    calls = []
    async def send(request):
        calls.append(json.loads(request.content))
        good = {"id": "arbitrary", "name": "体重", "value": "62.5", "unit": "kg", "page": 4,
                "quote": "体重 62.5 kg", "reference_range": "", "flag": ""}
        output = {"text": "体重 62.5 kg", "items": [good, {**good, "id": "other", "quote": "fabricated"}]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "{}" if malformed else json.dumps(output)}}]})
    factory = httpx.AsyncClient
    monkeypatch.setattr(p.httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(send), **kwargs))
    return calls


def test_model_evidence_is_bounded_and_not_promoted_to_review(monkeypatch):
    calls = _mock_model(monkeypatch)
    checks = []
    async def authorize(): checks.append(True)
    result = asyncio.run(p.extract_document(b"synthetic", authorize))
    assert len(calls) == 1 and len(checks) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in calls[0]
    assert result["items"][0]["id"] == "p1-i1" and result["items"][0]["page"] == 1
    assert len(result["items"]) == 1 and len(result["warnings"]) == 2
    assert "reviewed" not in result


def test_authorization_failure_prevents_any_cloud_request(monkeypatch):
    calls = _mock_model(monkeypatch)
    async def revoked(): raise ValueError("authorization revoked")
    with pytest.raises(ValueError, match="authorization revoked"):
        asyncio.run(p.extract_document(b"synthetic", revoked))
    assert calls == []


def test_invalid_model_output_is_rejected(monkeypatch):
    _mock_model(monkeypatch, malformed=True)
    async def allowed(): pass
    with pytest.raises(ValueError, match="有效草稿"):
        asyncio.run(p.extract_document(b"synthetic", allowed))
