"""
LD AI built-in response engine.

This layer must work without an external model. SiliconFlow/Ollama can polish
or deepen the response, but the local core still provides the answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.ld_ai.case_reasoner import CaseAnalysis, analyze_case
from services.ld_ai.intent_parser import IntentResult
from services.ld_ai.knowledge_retriever import retrieve_doc_chunks
from services.ld_ai.vision_stub import VisionResult


@dataclass
class ResponsePayload:
    answer: str
    protected_facts: list[str]
    case_analysis: CaseAnalysis


_KB: dict[str, dict[str, str]] = {
    "dashed": {
        "name": "vạch đứt",
        "rule": "Vạch đứt thường dùng để phân chia làn cùng chiều hoặc phần đường có thể chuyển làn khi an toàn. Khi gán nhãn, polyline phải bám theo tâm vạch, các đoạn đứt phải đều, không kéo liền thành một vạch dài.",
        "fix": "Nếu QA báo sai, hãy kiểm tra xem vị trí đó có thật sự cho phép chuyển làn hay không, đoạn đứt có bị nối liền quá dài không, và các điểm có đi đúng tâm vạch trong camera/LiDAR không.",
    },
    "solid": {
        "name": "vạch liền",
        "rule": "Vạch liền biểu thị ranh giới không được lấn qua hoặc cần giữ liên tục. Annotation phải là một đường liên tục, mượt, không ngắt đoạn nếu kiểu vạch không đổi.",
        "fix": "Khi sửa, nối lại các đoạn bị đứt sai, tăng điểm ở đoạn cong, và bảo đảm đầu cuối các đoạn liền nhau khớp nếu bắt buộc phải tách do đổi loại vạch.",
    },
    "edge": {
        "name": "vạch mép đường/road edge",
        "rule": "Road edge hoặc vạch mép đường đánh dấu biên ngoài của phần đường có thể chạy. Đường này cần bám mép thực tế, không lấn vào lòng đường và không trôi ra ngoài lề.",
        "fix": "Sửa bằng cách đối chiếu mép đường trên nhiều frame, đặt lại điểm ở phần cong và giữ topology liên tục với đoạn trước/sau.",
    },
    "double_yellow": {
        "name": "vạch đôi vàng",
        "rule": "Vạch đôi vàng gồm hai vạch vàng song song ở tim đường, thường dùng để phân chia hai chiều và cấm vượt. Hai polyline phải song song, khoảng cách đều, không hội tụ hoặc tách xa bất thường.",
        "fix": "Nếu thiếu một nhánh hoặc nhầm thành vạch đơn, cần vẽ đủ hai đường vàng, giữ cùng hướng và cùng nhóm logic nếu quy định dự án yêu cầu.",
    },
    "yellow_solid_dash": {
        "name": "vạch vàng liền và đứt song song",
        "rule": "Tổ hợp vàng liền + vàng đứt có ý nghĩa khác nhau theo phía làn xe: phía cạnh vạch liền bị hạn chế vượt, phía cạnh vạch đứt có thể vượt khi an toàn.",
        "fix": "Khi annotate, phải tách thành hai polyline riêng: một liền, một đứt. Không đổi thứ tự hai bên nếu hình thực tế cho thấy rõ phía liền và phía đứt.",
    },
    "fishbone": {
        "name": "vùng xương cá/diversion area",
        "rule": "Vùng xương cá là khu vực dẫn hướng hoặc phân luồng, thường xuất hiện ở cao tốc, điểm nhập làn, tách làn hoặc khu thi công. Không được coi nó là một vạch đứt thông thường.",
        "fix": "Cần vẽ đủ xương sống và các nhánh chéo nhìn thấy. Với cảnh cao tốc sửa chữa, tài liệu dự án nhấn mạnh vùng xương cá vẫn vẽ bình thường, không phân biệt line cũ hay line mới nếu hình ảnh yêu cầu thể hiện vùng đó.",
    },
    "crosswalk": {
        "name": "vạch qua đường/zebra",
        "rule": "Crosswalk gồm nhiều dải trắng song song, vuông góc hoặc gần vuông góc với hướng xe chạy. Các dải phải thẳng, đều và bao phủ đúng bề rộng vùng qua đường.",
        "fix": "Nếu thiếu dải hoặc lệch góc, hãy căn lại toàn bộ cụm theo trục qua đường thay vì sửa từng dải rời rạc.",
    },
    "stop_line": {
        "name": "vạch dừng",
        "rule": "Vạch dừng là vạch ngang liền, đặt trước đèn tín hiệu, biển dừng hoặc vị trí phải dừng xe. Nó phải vuông góc với hướng di chuyển và nằm đúng trước vùng giao cắt.",
        "fix": "Sửa bằng cách đưa vạch về đúng vị trí dừng, không đặt quá sâu vào nút giao và không lùi quá xa so với tín hiệu/biển báo.",
    },
    "arrow": {
        "name": "mũi tên chỉ hướng",
        "rule": "Mũi tên phải nằm theo tim làn và chỉ đúng hướng di chuyển. Đây là đối tượng ảnh hưởng trực tiếp đến hiểu biết hướng đi của hệ thống tự lái.",
        "fix": "Nếu QA báo sai hướng, hãy đối chiếu luồng xe, biển báo và thứ tự frame; đầu mũi tên phải trùng chiều xe đi, không vẽ ngược.",
    },
    "missing_lane": {
        "name": "lỗi thiếu vạch làn",
        "rule": "Missing lane xảy ra khi một vạch lẽ ra phải được gán nhãn nhưng bị bỏ sót. Với dữ liệu LD, nếu đoạn vạch bị mòn hoặc che nhẹ nhưng có thể suy ra từ trước/sau, thường cần bổ sung để topology liên tục.",
        "fix": "Cách sửa là tìm đoạn trước và sau còn nhìn thấy, nối theo hướng tuyến, đặt điểm vào tâm vạch ước lượng và tránh tạo gãy góc lớn.",
    },
    "wrong_color": {
        "name": "lỗi sai màu vạch",
        "rule": "Wrong color là khi chọn trắng thay vì vàng hoặc ngược lại. Quy tắc thực hành: vạch phân làn cùng chiều thường là trắng, vạch phân chia hai chiều hoặc vùng cảnh báo đặc thù thường là vàng, nhưng phải ưu tiên quy định dự án.",
        "fix": "Sửa bằng cách xác định vai trò của vạch trong cảnh, đổi đúng màu thuộc tính và kiểm tra các đoạn cùng topology để không còn đoạn đổi màu bất thường.",
    },
    "wrong_type": {
        "name": "lỗi sai loại vạch",
        "rule": "Wrong type thường là nhầm liền/đứt, nhầm road edge với lane line, hoặc nhầm vùng xương cá với lane marking thường. Sai loại làm model học sai luật giao thông.",
        "fix": "Cần nhìn lại chức năng của vạch: có cho chuyển làn không, có phải biên đường không, có thuộc vùng phân luồng không. Sau đó đổi đúng type và tách đoạn tại điểm kiểu vạch thay đổi.",
    },
    "wrong_arrow": {
        "name": "lỗi mũi tên sai hướng",
        "rule": "Mũi tên sai hướng là lỗi nghiêm trọng vì nó đảo logic di chuyển. Không chỉ nhìn hình mũi tên đơn lẻ, phải đối chiếu cả hướng làn và dòng xe.",
        "fix": "Sửa bằng cách đảo hướng đối tượng hoặc vẽ lại mũi tên theo chiều xe chạy; nếu hướng không chắc, kiểm tra các frame liên tiếp và biển chỉ dẫn.",
    },
    "offset": {
        "name": "lỗi lệch vị trí",
        "rule": "Offset là khi polyline không bám tâm đối tượng thật, bị trôi sang bên hoặc gãy khỏi hình dạng vạch. Lỗi này làm dữ liệu hình học sai dù type và màu có thể đúng.",
        "fix": "Sửa bằng cách kéo các điểm về đúng tim vạch, tăng mật độ điểm ở khúc cua và kiểm tra liên tục trước/sau để không tạo drift.",
    },
    "default": {
        "name": "vạch kẻ đường LD",
        "rule": "Trong LD, cần gán nhãn chính xác hình học, màu, loại vạch và quan hệ topology. Điểm phải theo hướng di chuyển hoặc theo quy định dự án, không ngắt đoạn nếu kiểu vạch không đổi.",
        "fix": "Nếu câu hỏi chưa nêu rõ loại vạch, hãy xác định trước đó là lane line, road edge, arrow, stop line, crosswalk hay diversion area rồi áp quy tắc riêng.",
    },
}


def _clean_text(text: str, limit: int = 520) -> str:
    clean = " ".join((text or "").replace("\t", " ").split())
    return clean[:limit].rstrip() + ("..." if len(clean) > limit else "")


def _doc_context(message: str, intent: IntentResult) -> list[dict]:
    query = f"{message} {intent.marking_type} {intent.drawing_kind}"
    return retrieve_doc_chunks(query, limit=3)


def _format_doc_context(chunks: list[dict]) -> str:
    if not chunks:
        return (
            "Trong dữ liệu tài liệu hiện có, mình chưa tìm được đoạn khớp thật mạnh với câu hỏi này. "
            "Vì vậy câu trả lời dưới đây dựa trên lõi quy tắc LD nội bộ và các lỗi QA phổ biến."
        )
    parts = []
    for idx, chunk in enumerate(chunks, start=1):
        name = chunk.get("name") or "tài liệu LD"
        excerpt = _clean_text(str(chunk.get("text") or ""))
        parts.append(f"{idx}. Từ {name}: {excerpt}")
    return "Các đoạn tri thức liên quan nhất trong data nội bộ là: " + " ".join(parts)


def _request_guidance(request_type: str) -> str:
    if request_type == "draw":
        return (
            "Khi cần minh họa, hệ thống nên chọn đúng scene vẽ theo loại vạch/lỗi, ưu tiên thể hiện hình học trước: "
            "vạch nằm ở đâu, hướng nào, đoạn nào sai và bản đúng phải sửa ra sao."
        )
    if request_type == "fix":
        return (
            "Quy trình sửa nên đi theo 4 bước: xác định object bị QA trả về, đối chiếu frame trước/sau, sửa type/màu/hình học, "
            "rồi kiểm tra lại topology để tránh sửa một điểm nhưng làm đứt mạch đường."
        )
    return (
        "Để hiểu đúng, hãy tách câu hỏi thành ba lớp: vai trò giao thông của vạch, cách biểu diễn trong annotation, "
        "và lỗi QA có thể phát sinh nếu biểu diễn sai."
    )


def _format_case_analysis(case: CaseAnalysis) -> str:
    if case.primary_case == "general_ld_case":
        return (
            "Vì câu hỏi chưa có đủ tín hiệu để chốt một case chuyên biệt, core sẽ trả lời theo hướng best-effort: "
            f"{case.decision} "
            + " ".join(case.conditions)
        )

    confidence = int(case.confidence * 100)
    signals = ", ".join(case.signals) if case.signals else "tín hiệu LD chung"
    conditions = " ".join(f"{idx}. {item}" for idx, item in enumerate(case.conditions, start=1))
    missing = " ".join(case.missing_evidence[:2])
    return (
        f"Phân tích case phức tạp: core nhận diện {case.primary_case} với độ tin cậy khoảng {confidence}% "
        f"dựa trên các tín hiệu: {signals}. Kết luận xử lý: {case.decision} "
        f"Điều kiện áp dụng: {conditions} "
        f"{'Nếu chưa có đủ ảnh/frame thì cần kiểm tra thêm: ' + missing if missing else ''}"
    )


def _protected_facts(
    marking: str,
    info: dict[str, str],
    chunks: list[dict],
    case_analysis: CaseAnalysis,
) -> list[str]:
    facts = [
        f"Nhóm câu hỏi là {info['name']}.",
        info["rule"],
        info["fix"],
        f"CaseAnalysis decision: {case_analysis.decision}",
    ]
    facts.extend(case_analysis.protected_facts)
    if marking == "fishbone":
        facts.append(
            "Vùng xương cá là vùng dẫn hướng hoặc phân luồng, không phải vạch đứt thông thường."
        )
        facts.append(
            "Với dữ liệu cao tốc sửa chữa, vùng xương cá vẽ bình thường; không phân biệt line cũ hay line mới nếu tài liệu không yêu cầu khác."
        )
    elif marking == "missing_lane":
        facts.append(
            "Missing lane là lỗi bỏ sót vạch cần gán nhãn; nếu có thể suy ra từ frame trước/sau thì cần bổ sung để giữ topology liên tục."
        )
    elif marking == "wrong_color":
        facts.append(
            "Sai màu là chọn trắng thay vì vàng hoặc ngược lại; phải ưu tiên vai trò của vạch và quy định dự án."
        )
    elif marking == "wrong_type":
        facts.append(
            "Sai loại cần sửa theo chức năng thật của vạch: lane line, road edge, arrow, stop line, crosswalk hoặc diversion area."
        )

    for chunk in chunks[:2]:
        name = chunk.get("name") or "tài liệu LD"
        excerpt = _clean_text(str(chunk.get("text") or ""), limit=240)
        if excerpt:
            facts.append(f"Từ {name}: {excerpt}")
    return facts


def build_response(message: str, intent: IntentResult, vision: Optional[VisionResult]) -> ResponsePayload:
    marking = intent.marking_type or "default"
    info = _KB.get(marking) or _KB["default"]
    chunks = _doc_context(message, intent)
    case_analysis = analyze_case(message, intent, chunks)

    vision_prefix = ""
    if vision and vision.summary:
        vision_prefix = f"Từ ảnh bạn gửi, hệ thống nhìn thấy: {vision.summary}. "

    answer = (
        f"{vision_prefix}Mình đang hiểu câu hỏi này thuộc nhóm {info['name']}. "
        f"{info['rule']} "
        f"{_format_case_analysis(case_analysis)} "
        f"{_format_doc_context(chunks)} "
        f"Về mặt thao tác, {info['fix']} "
        f"{_request_guidance(intent.request_type)} "
        "Nếu áp vào QA dashboard, hãy coi đây là một case cần vừa giải thích bằng chữ vừa có minh họa: bản sai nên làm nổi bật điểm bị thiếu/sai/lệch, "
        "bản đúng nên cho thấy vị trí hoặc kiểu vạch sau khi sửa. Như vậy labeler nhìn vào là hiểu ngay cần sửa thuộc tính nào, hình học nào và vì sao."
    )

    return ResponsePayload(
        answer=answer,
        protected_facts=_protected_facts(marking, info, chunks, case_analysis),
        case_analysis=case_analysis,
    )
