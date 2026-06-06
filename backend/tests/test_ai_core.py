from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from services.ld_ai.intent_parser import parse_intent
from services.ld_ai.knowledge_retriever import retrieve_doc_chunks
from services.ld_ai.ld_brain import LDBrain
from services.ld_ai.response_builder import build_response


def test_local_core_returns_long_vietnamese_answer_without_llm():
    intent = parse_intent("giải thích lỗi missing lane annotation chi tiết")
    answer = build_response("giải thích lỗi missing lane annotation chi tiết", intent, None).answer

    assert len(answer) > 500
    assert "Missing lane" in answer or "missing lane" in answer
    assert "sửa" in answer.lower()


def test_retriever_reads_uploaded_docs():
    cao_toc = retrieve_doc_chunks("cao tốc sửa chữa xương cá line cũ line mới", limit=5)
    ld_train = retrieve_doc_chunks("quy tắc gán nhãn lane center line vạch làn đường cong", limit=5)

    assert any("Cao tốc sửa chữa" in item["name"] for item in cao_toc)
    assert any("LD train original" in item["name"] for item in ld_train)


def test_brain_falls_back_when_llm_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", None)
    intent = parse_intent("vạch xương cá là gì")

    result = LDBrain().answer(
        message="vạch xương cá là gì",
        intent=intent,
        references=[],
        vision_note=None,
    )

    assert result.used_llm is False
    assert "xương cá" in result.answer.lower()


def test_brain_falls_back_when_llm_raises(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    monkeypatch.setattr(brain.client, "chat", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    intent = parse_intent("cao tốc sửa chữa vẽ xương cá thế nào")

    result = brain.answer(
        message="cao tốc sửa chữa vẽ xương cá thế nào",
        intent=intent,
        references=[],
        vision_note=None,
    )

    assert result.used_llm is False
    assert result.error == "boom"
    assert "cao tốc" in result.answer.lower() or "xương cá" in result.answer.lower()


def test_brain_accepts_valid_polish(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    query = "sai màu vạch vàng thành trắng thì sửa ra sao"
    intent = parse_intent(query)
    core = build_response(query, intent, None).answer
    polished = (
        core
        + "\n\nTóm lại, labeler cần xác định đúng vai trò của vạch, đổi lại thuộc tính màu, "
        "rồi rà các đoạn cùng topology để không còn đoạn nào bị lệch quy tắc."
    )
    monkeypatch.setattr(brain.client, "chat", lambda **_: polished)

    result = brain.answer(message=query, intent=intent, references=[], vision_note=None)

    assert result.used_llm is True
    assert result.polish_status == "accepted"
    assert result.answer == polished
    assert result.length_ratio and result.length_ratio > 1
    assert result.validation_warnings == []


def test_brain_rejects_too_short_polish(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    query = "missing lane annotation trên cao tốc sửa chữa xử lý thế nào"
    intent = parse_intent(query)
    core = build_response(query, intent, None).answer
    monkeypatch.setattr(brain.client, "chat", lambda **_: "Sửa lại vạch bị thiếu.")

    result = brain.answer(message=query, intent=intent, references=[], vision_note=None)

    assert result.used_llm is False
    assert result.polish_status == "rejected"
    assert result.answer == core
    assert any("too_short" in warning for warning in (result.validation_warnings or []))


def test_brain_rejects_fishbone_old_new_line_contradiction(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    query = "vạch xương cá là gì và lỗi thường gặp"
    intent = parse_intent(query)
    core = build_response(query, intent, None).answer
    bad = (
        core
        + "\n\nLưu ý sai: Cần phân biệt rõ line cũ và line mới trong vùng xương cá, "
        "nếu không phân biệt thì QA sẽ tính lỗi."
    )
    monkeypatch.setattr(brain.client, "chat", lambda **_: bad)

    result = brain.answer(message=query, intent=intent, references=[], vision_note=None)

    assert result.used_llm is False
    assert result.polish_status == "rejected"
    assert result.answer == core
    assert any(
        "fishbone_old_new_line_contradiction" in warning
        for warning in (result.validation_warnings or [])
    )


def test_brain_repairs_invalid_polish(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    query = "sai màu vạch vàng thành trắng thì sửa ra sao"
    intent = parse_intent(query)
    core = build_response(query, intent, None).answer
    repaired = (
        core
        + "\n\nNói thực tế, hãy sửa thuộc tính màu trước, sau đó rà lại các đoạn liên quan "
        "để bảo đảm toàn bộ object thống nhất."
    )
    replies = iter(["Quá ngắn.", repaired])
    monkeypatch.setattr(brain.client, "chat", lambda **_: next(replies))

    result = brain.answer(message=query, intent=intent, references=[], vision_note=None)

    assert result.used_llm is True
    assert result.polish_status == "repaired"
    assert result.answer == repaired
    assert any("too_short" in warning for warning in (result.validation_warnings or []))


def test_core_handles_construction_break_centerline_case():
    query = "đoạn này đầu trắng giữa vàng cuối trắng trong cao tốc sửa chữa thì có cần break centerline không"
    intent = parse_intent(query)
    payload = build_response(query, intent, None)

    assert payload.case_analysis.primary_case == "construction_break_centerline"
    assert payload.case_analysis.confidence >= 0.8
    assert "break" in payload.answer.lower() or "cắt" in payload.answer.lower()
    assert "centerline" in payload.answer.lower()


def test_core_handles_traffic_post_separator_case():
    query = "cọc tiêu ở giữa phân cách thì centerline cùng chiều và đối hướng xử lý sao"
    intent = parse_intent(query)
    payload = build_response(query, intent, None)

    assert payload.case_analysis.primary_case == "traffic_post_separator_centerline"
    assert payload.case_analysis.confidence >= 0.8
    assert "cọc tiêu" in payload.answer.lower()
    assert "cùng chiều" in payload.answer.lower() or "đối hướng" in payload.answer.lower()


def test_core_handles_old_new_line_topology_case():
    query = "line cũ với line mới giao nhau có được không hay phải tách"
    intent = parse_intent(query)
    payload = build_response(query, intent, None)

    assert payload.case_analysis.primary_case == "old_new_line_topology"
    assert payload.case_analysis.confidence >= 0.8
    assert "không kết luận giao nhau là lỗi" in payload.answer.lower()
    assert "tách" in payload.answer.lower() or "break" in payload.answer.lower()


def test_core_handles_fishbone_old_line_case():
    query = "vùng xương cá line cũ thì vẽ bình thường hay bỏ qua"
    intent = parse_intent(query)
    payload = build_response(query, intent, None)

    assert payload.case_analysis.primary_case == "fishbone_construction_area"
    assert payload.case_analysis.confidence >= 0.9
    assert "vẽ bình thường" in payload.answer.lower()
    assert "không phân biệt line cũ" in payload.answer.lower()


def test_core_handles_ambiguous_edge_vs_lane_case():
    query = "không rõ đây là road edge hay lane line vì bị mờ thì nên xử lý thế nào"
    intent = parse_intent(query)
    payload = build_response(query, intent, None)

    assert payload.case_analysis.primary_case == "ambiguous_edge_vs_lane_line"
    assert payload.case_analysis.confidence >= 0.7
    assert "best-effort" in payload.answer.lower()
    assert "frame trước/sau" in payload.answer.lower()


def test_brain_rejects_case_analysis_decision_contradiction(monkeypatch):
    monkeypatch.setattr(settings, "LD_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LD_LLM_PROVIDER", "siliconflow")
    monkeypatch.setattr(settings, "LD_LLM_API_KEY", "test-key")
    brain = LDBrain()
    query = "đoạn này đầu trắng giữa vàng cuối trắng trong cao tốc sửa chữa thì có cần break centerline không"
    intent = parse_intent(query)
    core = build_response(query, intent, None).answer
    bad = core + "\n\nKết luận sai: không cần break, cứ giữ nguyên centerline xuyên suốt."
    monkeypatch.setattr(brain.client, "chat", lambda **_: bad)

    result = brain.answer(message=query, intent=intent, references=[], vision_note=None)

    assert result.used_llm is False
    assert result.polish_status == "rejected"
    assert result.answer == core
    assert result.case_analysis
    assert result.case_analysis["primary_case"] == "construction_break_centerline"
    assert any(
        "case_analysis_break_decision_contradiction" in warning
        for warning in (result.validation_warnings or [])
    )
