"""국민건강통계 2024 분포 + 가정값으로 '한국인 검진 수검자' 합성 코호트를 만든다.

- 키·체중 대신 BMI, 혈압, 혈색소는 성·연령군별 백분위수에서 표본 추출(가우시안 코퓰라로 BMI·혈압 상관 반영)
- 당뇨·치료·만성콩팥병·뇌졸중/심근경색 진단은 성·연령군별 유병률로 추출
- 통계표에 없는 항목(채혈 실신 경험, 생리 중 등)은 data/assumptions.yaml 가정값 사용
결과는 '사람 데이터'가 아니라 통계표를 재현하는 가상 인구이며, 변수 간 상관은 가정에 의존한다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm

from engine import CORE_EXAMS, assess, max_level
from knhanes import AGE_GROUPS, load_params

ASSUMPTIONS = Path(__file__).parent / "data" / "assumptions.yaml"
AGE_RANGE = {"19-29": (19, 29), "30-39": (30, 39), "40-49": (40, 49), "50-59": (50, 59),
             "60-69": (60, 69), "70+": (70, 84)}
P = np.array([5, 10, 25, 50, 75, 90, 95]) / 100


def load_assumptions() -> dict:
    with open(ASSUMPTIONS, encoding="utf-8") as f:
        a = yaml.safe_load(f)
    return {k: v["value"] for k, v in a["items"].items()}


def assumption_meta() -> dict:
    with open(ASSUMPTIONS, encoding="utf-8") as f:
        return yaml.safe_load(f)["items"]


def quantile(pcts: list[float], u: np.ndarray) -> np.ndarray:
    """백분위수 7개로 역누적분포를 만든다. 5~95% 사이는 선형 보간, 바깥 꼬리는 정규분포로 연장."""
    v = np.asarray(pcts, float)
    sd = (v[-1] - v[0]) / (2 * norm.ppf(0.95))
    out = np.interp(u, P, v)
    lo, hi = u < P[0], u > P[-1]
    out[lo] = v[0] + sd * (norm.ppf(u[lo]) - norm.ppf(P[0]))
    out[hi] = v[-1] + sd * (norm.ppf(u[hi]) - norm.ppf(P[-1]))
    return out


def _prev(table: dict, sex: str, age: str, fallback_age: str | None = None) -> float:
    t = table.get(sex, {})
    return t.get(age, t.get(fallback_age, 0.0) if fallback_age else 0.0)


def generate(n: int = 5000, seed: int = 0, assume: dict | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    kp = load_params()
    a = {**load_assumptions(), **(assume or {})}
    # 성·연령군 비율: 국민건강통계 BMI 표의 표본 수
    cells = [(s, g, kp["dist"]["bmi"][s][g]["n"]) for s in ("M", "F") for g in AGE_GROUPS]
    w = np.array([c[2] for c in cells], float)
    idx = rng.choice(len(cells), size=n, p=w / w.sum())
    rows = []
    # 상관 구조: z = [BMI, SBP, DBP, Hb]
    r1, r2 = a["corr_bmi_sbp"], a["corr_sbp_dbp"]
    cov = np.array([[1, r1, r1 * r2, 0], [r1, 1, r2, 0], [r1 * r2, r2, 1, 0], [0, 0, 0, 1]])
    z = rng.multivariate_normal(np.zeros(4), cov, size=n)
    u = norm.cdf(z)
    for i, k in enumerate(idx):
        sex, g, _ = cells[k]
        lo, hi = AGE_RANGE[g]
        age = int(rng.integers(lo, hi + 1))
        d = kp["dist"]
        rec = {"sex": sex, "age": age, "age_group": g}
        rec["bmi"] = float(quantile(d["bmi"][sex][g]["pcts"], u[i, 0:1])[0])
        rec["sbp"] = round(float(quantile(d["sbp"][sex][g]["pcts"], u[i, 1:2])[0]))
        rec["dbp"] = round(float(quantile(d["dbp"][sex][g]["pcts"], u[i, 2:3])[0]))
        rec["hb"] = round(float(quantile(d["hb"][sex][g]["pcts"], u[i, 3:4])[0]), 1)
        pr = kp["prev"]
        dm = rng.random() < _prev(pr["diabetes"], sex, g)
        treated = dm and rng.random() < _prev(pr["dm_treated"], sex, g, "30-39")
        if dm:
            rec["diabetes"] = True
        if treated and rng.random() < a["dm_insulin_or_su"]:
            rec["insulin" if rng.random() < 0.4 else "sulfonylurea"] = True
        if rng.random() < _prev(pr["ckd"], sex, g):
            rec["ckd"] = True
        cvd = rng.random() < _prev(pr["stroke"], sex, g) + _prev(pr["ihd"], sex, g)
        if cvd:
            rec["cardiac_disease"] = True
            if rng.random() < a["antiplatelet_if_cvd"]:
                rec["antiplatelet"] = True
        if rng.random() < (a["anticoagulant_65plus"] if age >= 65 else a["anticoagulant_under65"]):
            rec["anticoagulant"] = True
        for key, field in [("prior_syncope_blood", "prior_syncope_blood"), ("needle_fear", "needle_fear"),
                           ("dehydration", "dehydration"), ("fever_today", "fever_today")]:
            if rng.random() < a[key]:
                rec[field] = True
        rec["fasting_hours"] = float(rng.uniform(2, 7.5)) if rng.random() < a["fasting_under8h"] else float(rng.uniform(9, 14))
        if sex == "F":
            if age <= 49:
                rec["pregnancy"] = "unknown" if rng.random() < a["pregnancy_unknown_f19_49"] else "no"
            else:
                rec["pregnancy"] = "no"
            if age <= 50 and rng.random() < a["menstruating_f19_50"]:
                rec["menstruating"] = True
        if age >= 65 and rng.random() < a["fall_history_65plus"]:
            rec["fall_history"] = True
        if rng.random() < (a["mobility_aid_75plus"] if age >= 75 else a["mobility_aid_65_74"] if age >= 65 else 0):
            rec["mobility_aid"] = True
        if age >= 70 and rng.random() < a["hearing_aid_70plus"]:
            rec["hearing_aid"] = True
        if age >= 75 and rng.random() < a["cognitive_75plus"]:
            rec["cognitive_issue"] = True
        rows.append(rec)
    return pd.DataFrame(rows)


def to_patient(row: pd.Series) -> dict:
    """엔진 입력으로 변환. BMI는 키 170cm 기준 체중으로 환산(엔진이 키·체중에서 BMI를 계산하므로)."""
    p = {k: v for k, v in row.items() if k not in ("age_group", "bmi") and not (isinstance(v, float) and np.isnan(v))}
    p["height_cm"] = 170.0
    p["weight_kg"] = round(row["bmi"] * 1.7 ** 2, 2)
    return p


def run(df: pd.DataFrame, exams: list[str] = CORE_EXAMS) -> pd.DataFrame:
    out = []
    for _, r in df.iterrows():
        al = assess(to_patient(r), exams)
        out.append({"level": max_level(al), "alerts": [x.id for x in al],
                    "n_red": sum(x.level == "red" for x in al), "n_yellow": sum(x.level == "yellow" for x in al)})
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(out)], axis=1)


def check_against_tables(df: pd.DataFrame) -> pd.DataFrame:
    """생성기 검증: 합성 인구의 저체중·빈혈 비율이 국민건강통계 값과 맞는지 비교."""
    kp = load_params()
    rows = []
    for (sex, g), sub in df.groupby(["sex", "age_group"]):
        anemia_cut = 13 if sex == "M" else 12
        rows.append({"성별": {"M": "남", "F": "여"}[sex], "연령": g, "n": len(sub),
                     "저체중_합성": (sub.bmi < 18.5).mean(), "저체중_통계": _prev(kp["prev"]["underweight"], sex, g),
                     "빈혈_합성": (sub.hb < anemia_cut).mean(), "빈혈_통계": _prev(kp["prev"]["anemia"], sex, g),
                     "당뇨_합성": sub.get("diabetes", pd.Series(dtype=bool)).fillna(False).astype(bool).mean(),
                     "당뇨_통계": _prev(kp["prev"]["diabetes"], sex, g)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate(5000)
    res = run(df)
    print(check_against_tables(df).round(3).to_string())
    print(res.level.value_counts(normalize=True).round(3))
    print("1인당 평균 경고:", (res.n_red + res.n_yellow).mean().round(2))
    print(res.explode("alerts").alerts.value_counts().head(20))
