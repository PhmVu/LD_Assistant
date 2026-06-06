"""
Test stream endpoint: kiểm tra thứ tự text/drawing và chất lượng text.
"""
import sys
import requests
import json

sys.stdout.reconfigure(encoding='utf-8')

BASE = "http://localhost:8000"

def test_stream(message: str, label: str = "") -> dict:
    url = f"{BASE}/api/ld/chat/stream"
    data = {"message": message, "history": "[]"}
    events = []
    acc_text = ""
    drawing_data = None

    try:
        with requests.post(url, data=data, stream=True, timeout=90) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]" or not raw:
                    continue
                evt = json.loads(raw)
                events.append(evt["type"])
                if evt["type"] == "token":
                    acc_text += evt.get("text", "")
                    print(".", end="", flush=True)
                elif evt["type"] == "drawing":
                    drawing_data = evt.get("drawing")
                    token_count = sum(1 for e in events if e == "token")
                    print(f"\n  [DRAWING @ token={token_count}]", end="", flush=True)
                elif evt["type"] == "done":
                    print("\n  [DONE]")
                    break
    except Exception as ex:
        print(f"\n  ERROR: {ex}")
        return {}

    token_count = sum(1 for e in events if e == "token")
    drawing_pos = next((i for i, e in enumerate(events) if e == "drawing"), -1)
    text_before_drawing = drawing_pos > 5 if drawing_pos >= 0 else False

    result = {
        "label": label or message[:40],
        "total_tokens": token_count,
        "drawing_at_position": drawing_pos,
        "text_before_drawing": text_before_drawing,
        "text_length": len(acc_text),
        "drawing_scene": (drawing_data or {}).get("scene", "N/A"),
        "drawing_layers": len((drawing_data or {}).get("layers", [])),
        "text_preview": acc_text[:200],
    }
    return result


def run_tests():
    cases = [
        ("vach ke duong la gi?", "Q: vạch kẻ đường là gì"),
        ("ve vach doi vang", "Q: vẽ vạch đôi vàng"),
        ("ve xuong ca", "Q: vẽ xương cá"),
        ("giai thich loi missing lane annotation chi tiet", "Q: giải thích lỗi (long)"),
    ]

    results = []
    for msg, label in cases:
        print(f"\n{'='*60}")
        print(f"TEST: {label}")
        print(f"{'='*60}")
        r = test_stream(msg, label)
        results.append(r)

    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Test':<40} {'Tokens':>7} {'DrawPos':>8} {'TextFirst':>10} {'TextLen':>8} {'Scene':<20}")
    print("-" * 100)
    for r in results:
        if not r:
            continue
        text_first = "✓ YES" if r["text_before_drawing"] else "✗ NO"
        print(
            f"{r['label']:<40} {r['total_tokens']:>7} {r['drawing_at_position']:>8} "
            f"{text_first:>10} {r['text_length']:>8} {r['drawing_scene']:<20}"
        )

    print(f"\n{'='*60}")
    print("TEXT PREVIEW SAMPLES")
    print(f"{'='*60}")
    for r in results:
        if not r:
            continue
        print(f"\n[{r['label']}]")
        print(r.get("text_preview", "N/A"))
        print()


if __name__ == "__main__":
    run_tests()
