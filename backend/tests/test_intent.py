import sys
sys.stdout.reconfigure(encoding='utf-8')
from services.ld_ai.intent_parser import parse_intent

cases = [
    've vach doi vang',
    've xuong ca',
    'vach ke duong la gi?',
    've vach dut',
    'giai thich loi',
    'sai mau vach',
    'missing lane annotation',
    'vach dung doi',
    'vẽ vạch đôi vàng',
    'vẽ xương cá',
]

print(f"{'Input':<45} {'Marking':<22} {'DrawKind':<22} {'ReqType':<10} {'Long':<5}")
print('-'*110)
for msg in cases:
    i = parse_intent(msg)
    long_str = "Y" if i.wants_long_explanation else "N"
    print(f"{msg[:45]:<45} {i.marking_type:<22} {i.drawing_kind:<22} {i.request_type:<10} {long_str:<5}")

print("\nOK")
