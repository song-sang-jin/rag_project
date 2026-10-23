"""사용자가 제공한 공식 코드표를 서비스용 JSON으로 변환 (실행 필수 아님)."""
import json
import sys
from pathlib import Path
import openpyxl

source = Path(sys.argv[1])
workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
codes, field = {}, None
for row in list(workbook["코드정보"].values)[1:]:
    if row[0]:
        field = str(row[0]).strip()
    if field and row[3] and row[4]:
        codes.setdefault(field, {})[str(row[3]).strip()] = str(row[4]).strip()
target = Path(__file__).resolve().parents[1] / "youth_rag" / "codebook.json"
target.write_text(json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8")
print("code_groups", len(codes))
