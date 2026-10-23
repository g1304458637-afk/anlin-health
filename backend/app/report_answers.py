"""Validate confirmed report evidence and render factual answers without model prose."""
import json
import re

from .report_types import ReportItem, ReportReview


HEADER_LABELS = {
    "title": "报告标题",
    "patient_name": "报告姓名",
    "specimen_collected_at": "采样时间",
    "reported_at": "报告时间",
}
ITEM_FIELDS = ("name", "value", "unit", "reference_range", "flag")
MAX_EVIDENCE = 100
MAX_CONTEXT_CHARS = 40000
MAX_SELECTED = 8


def _compact(value: str) -> str:
    return "".join(value.split())


def _line_excerpt(text: str, needle: str) -> str | None:
    target = _compact(needle)
    if not target:
        return None
    pattern = re.compile(r"\s*".join(re.escape(ch) for ch in target))
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            line = line.strip()
            if len(line) <= 600:
                return line
            # Keep an exact substring around the match; do not invent text.
            start = max(0, match.start() - 120)
            return line[start:start + 600]
    return None


def build_evidence(report, resident, candidate: dict, reviewed: dict) -> tuple[dict, list[dict]]:
    """Fail closed if a reviewed field has lost its immutable original source."""
    if not isinstance(candidate, dict) or not isinstance(reviewed, dict):
        raise ValueError("报告核对数据无效")
    try:
        reviewed = ReportReview.model_validate(reviewed).model_dump(mode="json")
    except ValueError as exc:
        raise ValueError("报告核对数据无效") from exc
    if reviewed["revision"] != report.revision or reviewed["identity_confirmed"] is not True:
        raise ValueError("报告核对版本或身份确认无效")
    original_items, reviewed_items = candidate.get("items"), reviewed.get("items")
    pages = candidate.get("pages")
    if not isinstance(original_items, list) or not isinstance(reviewed_items, list) or not isinstance(pages, list):
        raise ValueError("报告出处数据不完整")
    if len(original_items) > MAX_EVIDENCE or len(reviewed_items) > 200:
        raise ValueError("报告项目数量无效")
    if len(pages) > 5 or not all(isinstance(p, dict) and type(p.get("page")) is int and
               isinstance(p.get("text"), str) and len(p["text"]) <= 25000 and
               1 <= p["page"] <= report.page_count for p in pages):
        raise ValueError("报告页出处无效")
    page_text = {p["page"]: p["text"] for p in pages}
    if len(page_text) != len(pages):
        raise ValueError("报告页出处重复")
    originals = {}
    for item in original_items:
        try:
            item = ReportItem.model_validate(item).model_dump(mode="json")
        except ValueError as exc:
            raise ValueError("报告项目出处无效") from exc
        if item["id"] in originals:
            raise ValueError("报告项目出处重复")
        originals[item["id"]] = item
    evidence = {}
    excluded = set(reviewed.get("excluded_item_ids", []))
    all_ids = [item["id"] for item in reviewed_items]
    if len(all_ids) != len(set(all_ids)) or not excluded.issubset(all_ids):
        raise ValueError("报告排除项目无效")
    source_kind = ("pdf_text" if report.mime_type == "application/pdf" and
                   candidate.get("extraction_method") in {"deepseek-text", "fixture-pdf-text"} else "model_transcription")
    for item in reviewed_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("报告核对项目无效")
        item_id = item["id"]
        if item.get("source_kind") == "manual":
            if (item_id in originals or not re.fullmatch(r"m-[0-9a-f]{32}", item_id) or item.get("quote") or
                    (item.get("page") is not None and not 1 <= item["page"] <= report.page_count)):
                raise ValueError("人工补录项目来源无效")
            # Human page locations are not extractor evidence. Never send these
            # additions as if they had immutable text or model-generated citations.
            continue
        if item_id in evidence or item_id not in originals:
            raise ValueError("报告核对项目无出处")
        original = originals[item_id]
        if (type(item.get("page")) is not int or item.get("page") != original.get("page") or
                item.get("quote") != original.get("quote") or
                item["page"] not in page_text or not isinstance(item.get("quote"), str) or
                not _compact(item["quote"]) or
                _compact(item["quote"]) not in _compact(page_text[item["page"]])):
            raise ValueError("报告项目原文与页码不一致")
        if any(not isinstance(item.get(field), str) or not isinstance(original.get(field), str)
               for field in ITEM_FIELDS):
            raise ValueError("报告项目核对值无效")
        if item_id in excluded:
            continue
        evidence[item_id] = {
            "id": item_id, "report_id": report.id, "revision": report.revision,
            "filename": report.filename, "title": reviewed.get("title") or "报告",
            "page": item["page"], "quote": item["quote"],
            "confirmed_fields": {field: item[field] for field in ITEM_FIELDS},
            "edited_fields": [field for field in ITEM_FIELDS if item[field] != original[field]],
            "source_kind": source_kind,
        }
    if set(evidence) != set(originals) - excluded or not set(originals).issubset(all_ids):
        raise ValueError("报告核对项目不完整")
    for field, label in HEADER_LABELS.items():
        confirmed = reviewed.get(field)
        original = candidate.get(field)
        if not isinstance(confirmed, str) or not confirmed.strip():
            continue
        needles = [confirmed]
        if isinstance(original, str) and 0 < len(original) <= 150 and original.strip() and original != confirmed:
            needles.append(original)
        for page, text in sorted(page_text.items()):
            quote = next((found for needle in needles if (found := _line_excerpt(text, needle))), None)
            if quote is None:
                continue
            evidence[f"meta:{field}"] = {
                "id": f"meta:{field}", "report_id": report.id, "revision": report.revision,
                "filename": report.filename, "title": reviewed.get("title") or "报告",
                "page": page, "quote": quote,
                "confirmed_fields": {"field": field, "label": label, "value": confirmed},
                "edited_fields": [field] if confirmed != original else [],
                "source_kind": source_kind,
            }
            break
    context = [{"id": source["id"], **source["confirmed_fields"]} for source in evidence.values()]
    context_chars = len(json.dumps(context, ensure_ascii=False))
    if context_chars > MAX_CONTEXT_CHARS:
        raise ValueError("报告项目较多，请先缩小报告范围")
    return evidence, context


def validate_selection(selection: dict, evidence: dict) -> tuple[str, list[dict]]:
    if not isinstance(selection, dict) or set(selection) != {"intent", "evidence_ids"}:
        raise ValueError("问答服务选择无效")
    intent, ids = selection["intent"], selection["evidence_ids"]
    if intent not in {"lookup", "summary", "reference", "missing", "medical"}:
        raise ValueError("问答意图无效")
    if (not isinstance(ids, list) or len(ids) > MAX_SELECTED or
            any(not isinstance(item_id, str) for item_id in ids) or
            len(ids) != len(set(ids)) or any(item_id not in evidence for item_id in ids)):
        raise ValueError("问答服务引用了无效出处")
    return intent, [evidence[item_id] for item_id in ids]


_MEDICAL = re.compile(r"正常|异常|诊断|治疗|用药|药量|剂量|严重|危险|是不是病|高吗|低吗|怎么办|该吃")


def render_answer(message: str, intent: str, sources: list[dict], total_items: int) -> tuple[str, str]:
    if _MEDICAL.search(message):
        intent = "medical"
    if intent == "missing" or not sources:
        suffix = " 我不能据此判断疾病或用药，请咨询专业医护人员。" if intent == "medical" else ""
        return "已确认报告中没有足够的可定位依据回答这个问题，请对照原件或询问专业人员。" + suffix, "insufficient_evidence"
    details = []
    for source in sources:
        fields = source["confirmed_fields"]
        if "field" in fields:
            details.append(f"第{source['page']}页已确认的{fields['label']}：{fields['value']}。")
            continue
        value = fields["value"] + ((" " + fields["unit"]) if fields["unit"] and fields["value"] else "")
        if not fields["value"]:
            value = "未填写结果"
        sentence = f"第{source['page']}页已确认的{fields['name']}：{value}"
        if intent in {"reference", "lookup", "medical"}:
            if fields["reference_range"]:
                sentence += f"；已确认参考范围：{fields['reference_range']}"
            if fields["flag"]:
                sentence += f"；已确认标记：{fields['flag']}"
        details.append(sentence + "。")
    if intent == "reference" and not any(s["confirmed_fields"].get("reference_range") or
                                         s["confirmed_fields"].get("flag") for s in sources):
        return "所选项目在已确认报告中没有可核对的参考范围或标记；请对照原件。", "insufficient_evidence"
    answer = "".join(details)
    if intent == "summary":
        selected_items = sum("field" not in source["confirmed_fields"] for source in sources)
        if selected_items < total_items:
            answer += f"本次列出{selected_items}项，报告共{total_items}项；如需其他项目，请指定名称继续提问。"
    if intent == "medical":
        answer += "这些仅是报告中的已确认记录；我不能判断是否正常、诊断疾病或给出治疗和用药建议，请咨询专业医护人员。"
        return answer, "medical_guidance_limited"
    return answer, "supported"
