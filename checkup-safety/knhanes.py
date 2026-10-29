"""국민건강통계(국민건강영양조사, 2024) 엑셀 표 → 성·연령별 분포와 유병률 추출.

두 가지 표 형식을 읽는다.
- 분포 표(24년현황 시트): 성별 × 연령군마다 n, 평균, 백분위수(5·10·25·50·75·90·95)
- 추이 표(연도별 시트): 성별 × 연령군마다 연도별 n, 분율(%) → 2024년 값만 사용
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import openpyxl

DIR = Path(__file__).parent / "data" / "knhanes"
AGE_GROUPS = ["19-29", "30-39", "40-49", "50-59", "60-69", "70+"]
SEX = {"남자": "M", "여자": "F", "전체": "T"}
PCTS = [5, 10, 25, 50, 75, 90, 95]


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("*", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _rows(file: str, sheet: str):
    wb = openpyxl.load_workbook(DIR / file, read_only=True, data_only=True)
    return [list(r) for r in wb[sheet].iter_rows(values_only=True)]


def parse_distribution(file: str, sheet: str) -> dict:
    """{sex: {age: {"n", "mean", "pcts": [7개]}}} — 성·연령군별 첫 번째 블록만 사용."""
    out: dict = {}
    sex = None
    for r in _rows(file, sheet):
        if r[1] in SEX:
            sex = SEX[r[1]]
        age = str(r[2]).strip() if r[2] is not None else ""
        if sex in ("M", "F") and age in AGE_GROUPS and age not in out.get(sex, {}):
            pcts = [_num(v) for v in r[6:13]]
            if None not in pcts:
                out.setdefault(sex, {})[age] = {"n": _num(r[3]), "mean": _num(r[4]), "pcts": pcts}
    return out


def parse_trend(file: str, sheet: str, year: str = "’24") -> dict:
    """{sex: {age: 분율(0~1)}} — 추이 표에서 해당 연도 열을 찾아 읽는다."""
    rows = _rows(file, sheet)
    col = None
    for r in rows[:12]:
        for i, c in enumerate(r):
            if isinstance(c, str) and c.startswith(year):
                col = i
        if col is not None:
            break
    if col is None:
        raise ValueError(f"{file}/{sheet}: {year} 열을 찾을 수 없음")
    out: dict = {}
    sex = None
    for r in rows:
        if r[1] in SEX:
            sex = SEX[r[1]]
        age = str(r[2]).strip() if r[2] is not None else ""
        if sex and age in AGE_GROUPS and age not in out.get(sex, {}):
            v = _num(r[col + 1])
            if v is not None:
                out.setdefault(sex, {})[age] = v / 100
    return out


@lru_cache
def load_params() -> dict:
    """앱·시뮬레이터가 쓰는 2024년 국민건강통계 값 묶음."""
    p = {
        "source": "질병관리청 국민건강통계 2024 (국민건강영양조사 제9기 3차년도)",
        "dist": {
            "bmi": parse_distribution("15_비만.xlsx", "4.체질량지수분포"),
            "sbp": parse_distribution("16_고혈압.xlsx", "1.수축기혈압분포"),
            "dbp": parse_distribution("16_고혈압.xlsx", "2.이완기혈압분포"),
            "hb": parse_distribution("20_빈혈.xlsx", "1.헤모글로빈"),
        },
        "prev": {
            "underweight": parse_trend("15_비만.xlsx", "3.저체중(BMI)"),
            "diabetes": parse_trend("17_당뇨병.xlsx", "1.당뇨병"),
            "dm_treated": parse_trend("17_당뇨병.xlsx", "3.치료", year="’22-’24"),  # 9기 통합값
            "anemia": parse_trend("20_빈혈.xlsx", "1.빈혈"),
            "ckd": parse_trend("21_만성콩팥병.xlsx", "1.만성콩팥병(중등도이상)"),
            "stroke": parse_trend("27_의사진단경험.xlsx", "1.뇌졸중"),
            "ihd": parse_trend("27_의사진단경험.xlsx", "2.협심증,심근경색증"),
        },
    }
    return p


if __name__ == "__main__":
    print(json.dumps(load_params(), ensure_ascii=False, indent=1)[:3000])
