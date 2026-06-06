from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any

from services.ld_ai.intent_parser import IntentResult


@dataclass
class CaseAnalysis:
    primary_case: str
    signals: list[str]
    confidence: float
    decision: str
    conditions: list[str]
    missing_evidence: list[str]
    protected_facts: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(text: str) -> str:
    value = (text or "").lower().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _doc_hint(chunks: list[dict[str, Any]], terms: tuple[str, ...]) -> bool:
    haystack = normalize_text(" ".join(str(chunk.get("text") or "") for chunk in chunks[:4]))
    return _has_any(haystack, terms)


def _base_missing_evidence(text: str) -> list[str]:
    missing: list[str] = []
    if not _has_any(text, ("hinh", "anh", "frame", "camera", "lidar")):
        missing.append("Cần đối chiếu ảnh/frame trước và sau để xác nhận phần bị mờ, bị che hoặc đoạn chuyển tiếp.")
    if not _has_any(text, ("huong", "cung chieu", "doi huong", "mũi tên", "mui ten")):
        missing.append("Cần xác nhận hướng xe chạy hoặc hướng tuyến nếu quyết định phụ thuộc cùng chiều/đối hướng.")
    return missing


def _general_case(intent: IntentResult, text: str) -> CaseAnalysis:
    return CaseAnalysis(
        primary_case="general_ld_case",
        signals=[intent.marking_type or "default"],
        confidence=0.35 if intent.marking_type == "default" else 0.55,
        decision=(
            "Chưa đủ tín hiệu để kết luận một case chuyên biệt; xử lý theo quy tắc LD chung: xác định object, "
            "đối chiếu frame, xác nhận type/màu/hình học rồi mới sửa."
        ),
        conditions=[
            "Nếu phát hiện có đổi type, đổi màu, đổi line cũ/line mới hoặc đổi topology thì phải tách xử lý tại điểm thay đổi.",
            "Nếu không có thay đổi thật và object liên tục thì không tự cắt đoạn chỉ vì câu hỏi mơ hồ.",
        ],
        missing_evidence=_base_missing_evidence(text),
        protected_facts=[
            "Khi câu hỏi mơ hồ, core chỉ được trả lời theo điều kiện và dấu hiệu kiểm tra, không phán chắc nếu thiếu ảnh/frame.",
        ],
    )


def analyze_case(
    message: str,
    intent: IntentResult,
    chunks: list[dict[str, Any]] | None = None,
) -> CaseAnalysis:
    chunks = chunks or []
    text = normalize_text(message)
    signals: list[str] = []

    has_construction = _has_any(text, ("cao toc", "sua chua", "thi cong", "line cu", "line moi")) or _doc_hint(
        chunks, ("line cu", "line moi", "khu vuc thi cong")
    )
    has_centerline = _has_any(text, ("centerline", "linecenter", "duong tim", "tim duong"))
    has_break = _has_any(text, ("break", "cat", "tach", "ngat doan"))
    has_color_transition = (
        _has_any(text, ("trang", "white"))
        and _has_any(text, ("vang", "yellow"))
        and _has_any(text, ("giua", "dau", "cuoi", "truoc", "sau"))
    )
    has_old_new = _has_any(text, ("line cu", "line moi", "duong cu", "duong moi"))
    has_intersection = _has_any(text, ("giao nhau", "cat nhau", "intersect", "cross"))
    if has_construction:
        signals.append("construction_or_old_new_line")
    if has_centerline:
        signals.append("centerline")
    if has_break:
        signals.append("break_or_split_question")
    if has_color_transition:
        signals.append("white_yellow_transition")

    if has_old_new and has_intersection:
        return CaseAnalysis(
            primary_case="old_new_line_topology",
            signals=signals + ["old_new_line", "topology_question"],
            confidence=0.86,
            decision=(
                "Không kết luận giao nhau là lỗi ngay lập tức. Với line cũ/line mới trong vùng sửa chữa, cần xem đó là chuyển tiếp hợp lệ "
                "hay là một đoạn bị gán sai. Nếu có thay đổi thuộc tính hoặc chuyển từ line cũ sang line mới thì phải tách đoạn; nếu chỉ là hình học "
                "giao nhau hợp lệ trong vùng chuyển tiếp thì giữ đúng từng line và kiểm tra topology."
            ),
            conditions=[
                "Break khi đổi line cũ/line mới, đổi màu, đổi type hoặc đổi logic làn.",
                "Không xóa một line chỉ vì nó giao line khác nếu tài liệu/ảnh cho thấy cả hai centerline đều cần tồn tại.",
                "Sau khi sửa, kiểm tra mỗi đoạn có đúng nhóm old/new và đúng hướng tuyến không.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Line cũ và line mới phải được tách khi thuộc tính thay đổi.",
                "Không tự xóa centerline cần tồn tại chỉ vì có giao nhau trong vùng chuyển tiếp.",
            ],
        )

    if has_construction and has_centerline and (has_break or has_color_transition):
        return CaseAnalysis(
            primary_case="construction_break_centerline",
            signals=signals,
            confidence=0.9,
            decision=(
                "Khả năng cao là phải break/cắt centerline tại vị trí thuộc tính thay đổi. Với case cao tốc sửa chữa, "
                "nếu trước/sau là line trắng còn đoạn giữa là line vàng hoặc chuyển giữa line cũ và line mới, core phải tách đoạn "
                "để gán đúng thuộc tính cho từng phần; không kéo một centerline xuyên suốt qua điểm đổi thuộc tính."
            ),
            conditions=[
                "Chỉ break tại điểm có thay đổi thật về màu, loại, line cũ/line mới hoặc topology.",
                "Nếu đoạn trước, giữa và sau cùng thuộc tính và cùng topology thì giữ liên tục, không cắt chỉ vì nhìn mơ hồ.",
                "Sau khi break, kiểm tra lại hướng tuyến và liên kết topology để đoạn sau không bị đảo hướng hoặc đứt logic.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Trong cao tốc sửa chữa, khi centerline đổi thuộc tính hoặc đổi line cũ/line mới thì phải break tại điểm thay đổi.",
                "Không được kéo một centerline xuyên qua đoạn có màu/type/old-new status khác nhau.",
            ],
        )

    has_post_separator = _has_any(text, ("coc tieu", "coc phan cach", "phan cach", "dai phan cach"))
    has_same_opposite = _has_any(text, ("cung chieu", "doi huong", "nguoc chieu"))
    if has_post_separator and (has_centerline or has_same_opposite):
        return CaseAnalysis(
            primary_case="traffic_post_separator_centerline",
            signals=signals + ["traffic_post_or_separator"],
            confidence=0.86,
            decision=(
                "Với cọc tiêu hoặc vật phân cách ở giữa, không xử lý như một vạch làn bình thường. Cần coi nó là ranh giới phân cách "
                "để xác định centerline cùng chiều hay đối hướng theo phía xe chạy. Không gom centerline xuyên qua vùng phân cách nếu hai phía "
                "thuộc hai luồng/hướng khác nhau."
            ),
            conditions=[
                "Nếu hai bên cọc tiêu là hai chiều xe khác nhau thì gán theo logic đối hướng.",
                "Nếu cọc tiêu chỉ chia làn cùng chiều trong cùng hướng tuyến thì giữ logic cùng chiều.",
                "Cần nhìn hướng mũi tên, dòng xe hoặc frame liền kề để tránh nhầm cùng chiều/đối hướng.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Cọc tiêu/vật phân cách phải được dùng để xác định ranh giới hướng xe trước khi vẽ hoặc sửa centerline.",
                "Không gom centerline qua vùng phân cách nếu hai phía là hai luồng hoặc hai hướng khác nhau.",
            ],
        )

    if has_old_new and (has_intersection or has_break or has_centerline):
        return CaseAnalysis(
            primary_case="old_new_line_topology",
            signals=signals + ["old_new_line", "topology_question"],
            confidence=0.84,
            decision=(
                "Không kết luận giao nhau là lỗi ngay lập tức. Với line cũ/line mới trong vùng sửa chữa, cần xem đó là chuyển tiếp hợp lệ "
                "hay là một đoạn bị gán sai. Nếu có thay đổi thuộc tính hoặc chuyển từ line cũ sang line mới thì phải tách đoạn; nếu chỉ là hình học "
                "giao nhau hợp lệ trong vùng chuyển tiếp thì giữ đúng từng line và kiểm tra topology."
            ),
            conditions=[
                "Break khi đổi line cũ/line mới, đổi màu, đổi type hoặc đổi logic làn.",
                "Không xóa một line chỉ vì nó giao line khác nếu tài liệu/ảnh cho thấy cả hai centerline đều cần tồn tại.",
                "Sau khi sửa, kiểm tra mỗi đoạn có đúng nhóm old/new và đúng hướng tuyến không.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Line cũ và line mới phải được tách khi thuộc tính thay đổi.",
                "Không tự xóa centerline cần tồn tại chỉ vì có giao nhau trong vùng chuyển tiếp.",
            ],
        )

    if intent.marking_type == "fishbone" or _has_any(text, ("xuong ca", "fishbone", "vung dan huong")):
        return CaseAnalysis(
            primary_case="fishbone_construction_area",
            signals=signals + ["fishbone"],
            confidence=0.92,
            decision=(
                "Vùng xương cá phải vẽ bình thường nếu nhìn thấy trong dữ liệu. Với case line cũ/line mới ở cao tốc sửa chữa, "
                "không được tự bỏ qua vùng xương cá và không biến nó thành vạch đứt thông thường; tài liệu đang nhấn mạnh không phân biệt line cũ hay line mới cho vùng này."
            ),
            conditions=[
                "Vẽ đủ xương sống và các nhánh chéo nhìn thấy.",
                "Nếu ảnh/frame cho thấy vùng xương cá thuộc khu thi công, vẫn vẽ theo hình dạng nhìn thấy thay vì bỏ qua vì là line cũ.",
                "Chỉ thay đổi cách gán nếu tài liệu dự án hoặc QA case cụ thể yêu cầu rõ khác đi.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Vùng xương cá trong cao tốc sửa chữa vẽ bình thường.",
                "Không phân biệt line cũ hay line mới cho vùng xương cá nếu tài liệu không yêu cầu khác.",
                "Vùng xương cá không phải vạch đứt thông thường.",
            ],
        )

    edge_vs_lane = (
        _has_any(text, ("road edge", "edge", "le duong", "mep duong"))
        and _has_any(text, ("lane line", "lane", "vach lan", "line"))
        and _has_any(text, ("mo", "khong ro", "che", "khuat", "phan biet"))
    )
    if edge_vs_lane:
        return CaseAnalysis(
            primary_case="ambiguous_edge_vs_lane_line",
            signals=signals + ["road_edge_vs_lane_line", "ambiguous_visibility"],
            confidence=0.78,
            decision=(
                "Không nên chốt ngay là road edge hay lane line chỉ từ một frame mờ. Core nên xử lý theo best-effort: nếu đường nằm ở biên ngoài phần xe chạy "
                "và nối với mép đường/lề thì ưu tiên road edge; nếu nằm giữa hai làn xe chạy và tạo lane topology thì ưu tiên lane line."
            ),
            conditions=[
                "Dùng frame trước/sau để xem đường đó tiếp tục như biên đường hay như vạch phân làn.",
                "Kiểm tra có đủ bề rộng làn ở hai phía không; nếu không đủ bề rộng làn thì không ép thành centerline.",
                "Nếu vẫn không chắc, đánh dấu cần review thay vì tự đổi type chắc chắn.",
            ],
            missing_evidence=_base_missing_evidence(text),
            protected_facts=[
                "Road edge là biên ngoài phần đường có thể chạy; lane line tạo topology giữa các làn.",
                "Khi ảnh mờ, phải đối chiếu frame trước/sau trước khi chốt type.",
            ],
        )

    return _general_case(intent, text)
