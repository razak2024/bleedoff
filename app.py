"""
Annular Casing Pressure — Bleed-down / Build-up Test Tool
Based on API Recommended Practice 90-2 (Annular Casing Pressure Management
for Onshore Wells), First Edition, April 2016 — principally Sections 8, 10, and 11.

This tool assists field/engineering personnel in:
  - Recording well & annulus information and Diagnostic Thresholds (Section 8)
  - Logging a pressure bleed-down/build-up test (Section 10.2)
  - Logging a thermally-induced-pressure screening test (Section 10.3)
  - Automatically applying the qualitative diagnostic logic of 10.2.2 / 10.3.2
  - Visualizing the pressure-time history against MAWOP / DT reference lines
  - Producing a documentation-ready test report (Section 11.3)
  - Screening a multi-well pressure survey (Excel upload) and classifying wells
    by annular pressure severity
  - Classifying bleed-off results as SCP or thermal effect and comparing them
    with the current survey pressure of each annulus

DISCLAIMER: This tool implements the qualitative decision logic described in
API RP 90-2 to aid diagnostics and documentation. It does not replace sound
engineering judgment, applicable regulations, or a qualified person's review.
"""

import io
import re
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# --------------------------------------------------------------------------------------
# Page config
# --------------------------------------------------------------------------------------
st.set_page_config(
    page_title="Pressure Survey — ACP Screening",
    page_icon="🛢️",
    layout="wide",
)

# --------------------------------------------------------------------------------------
# Pressure Survey — helper functions
# --------------------------------------------------------------------------------------
# Canonical field -> list of normalized aliases that may appear in the uploaded workbook's
# header row (oil template and gas template use the same field set in a different column
# order, plus some accented/French variants).
SURVEY_COLUMN_ALIASES = {
    "wellbore": ["wellbore", "puits", "well"],
    "date": ["date"],
    "redacteur": ["redacteur", "author", "redactor"],
    "nature": ["nature"],
    "state": ["state", "etat", "situation"],
    "choke": ["choke"],
    "hrs_sce": ["hrssce", "hrs", "hoursonstream", "heuresce"],
    "whp": ["whp"],
    "paval": ["paval"],
    "wht": ["wht"],
    "anp1": ["anp1", "eaa"],  # "EA-A(PSIG)" -> normalized "eaa"
    "anp2": ["anp2", "eab"],  # "EA-B(PSIG)" -> normalized "eab"
    "anp3": ["anp3", "eac"],  # "EA-C(PSIG)" -> normalized "eac"
    "comment": ["comment", "commentaire", "commentaires", "observation", "observations"],
    # UTM coordinates are matched via _detect_utm_column() (below) rather than the
    # generic alias matcher, since the "(X)"/"(Y)" suffix is stripped by
    # _normalize_header's parenthetical-removal step and would otherwise collide.
    "utm_x": [],
    "utm_y": [],
}

# Codes used in some field survey templates in place of a numeric psig reading, e.g.
# "V.B" (vanne bloquée / valve blocked), "M.P" (manque de pression / no pressure gauge
# or reading), "ENS" (ennoyé / flooded-out gauge), etc. These are qualitative status
# notes, not pressure values, so they are preserved as-is rather than coerced to a number.
SEVERITY_ORDER = ["Not Monitored", "Status", "Normal", "Low", "Moderate", "High", "Severe", "Critical"]
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}

SEVERITY_COLORS = {
    "Not Monitored": "background-color: #eeeeee; color: #888888",
    "Status": "background-color: #cfe2ff; color: #084298",
    "Normal": "background-color: #d4edda; color: #155724",
    "Low": "background-color: #fff3cd; color: #7a5b00",
    "Moderate": "background-color: #ffe0b2; color: #7a3e00",
    "High": "background-color: #f8d7da; color: #7a1c24",
    "Severe": "background-color: #f1948a; color: #4a0d0d",
    "Critical": "background-color: #dc3545; color: #ffffff; font-weight: 700",
}


def _normalize_header(value):
    """Normalize a raw header cell to a bare lowercase alnum token for matching."""
    s = str(value).strip().lower()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    s = re.sub(r"\(.*?\)", "", s)  # drop parenthetical units, e.g. "Whp (Psig)" -> "whp "
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _parse_numeric(val):
    """Parse a cell that may be a real number, blank, or French-formatted text
    (e.g. '1 578,00', '800,00') into a float, else NaN."""
    if val is None:
        return np.nan
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val) if not pd.isna(val) else np.nan
    s = str(val).strip()
    if s == "" or s.lower() in ("nan", "none", "-", "n/a"):
        return np.nan
    s = s.replace("\xa0", " ").replace(" ", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _parse_numeric_or_code(val):
    """Parse a pressure cell that may be a real number, blank/placeholder, or a
    qualitative status code (e.g. 'V.B', 'M.P', 'ENS') used in some field survey
    templates instead of a psig reading. Returns (numeric_value_or_nan, code_or_none).
    Blank/placeholder tokens ('', '-', 'n/a', etc.) are treated as simply missing
    (no code); anything else that isn't parseable as a number is kept as a code."""
    if val is None:
        return np.nan, None
    if isinstance(val, (int, float, np.integer, np.floating)):
        return (float(val) if not pd.isna(val) else np.nan), None
    s = str(val).strip()
    if s == "" or s.lower() in ("nan", "none", "-", "n/a"):
        return np.nan, None
    s_num = s.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(s_num), None
    except ValueError:
        return np.nan, s.upper()


def _detect_utm_column(raw_col):
    """Identify 'Coordonnées UTM (X)' / 'Coordonnées UTM (Y)' style headers, which the
    generic _normalize_header() can't distinguish because it strips parenthetical
    content (used elsewhere to drop units like '(psig)')."""
    s = str(raw_col).strip().lower()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    if "utm" not in s:
        return None
    if re.search(r"\(\s*x\s*\)", s) or re.search(r"\bx\s*$", s):
        return "utm_x"
    if re.search(r"\(\s*y\s*\)", s) or re.search(r"\by\s*$", s):
        return "utm_y"
    return None


def _has_code(code):
    """True if `code` is an actual status-code string (not NaN/None/empty).
    NaN is truthy in plain Python, so a bare `if code:` check is not safe here —
    pandas stores a missing object-column value as NaN, not None, in many cases."""
    return pd.notna(code) and str(code).strip() != ""


def _fmt_pressure_display(value, code):
    """Format a pressure cell for table display: the status code if present
    (e.g. 'V.B'), else the numeric psig value, else blank."""
    if _has_code(code):
        return code
    if pd.isna(value):
        return ""
    return f"{value:.0f}"


def _fmt_date_display(value):
    if pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%d/%m/%Y")
    return str(value)


def find_header_row(raw_df, max_scan_rows=10):
    """Scan the first few rows of a headerless read for the row that looks like the
    real header (contains a token like 'wellbore' or 'whp')."""
    for i in range(min(max_scan_rows, len(raw_df))):
        row_tokens = {_normalize_header(v) for v in raw_df.iloc[i].tolist()}
        if "wellbore" in row_tokens or "whp" in row_tokens:
            return i
    return 0


def load_survey_workbook(uploaded_file, sheet_name=0):
    """Load an oil- or gas-template survey workbook and return a normalized DataFrame
    with canonical columns: wellbore, date, state, whp, paval, wht, anp1, anp2, anp3, comment."""
    uploaded_file.seek(0)
    raw = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=None, dtype=object)
    header_row = find_header_row(raw)

    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=header_row)
    df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed")]

    colmap = {}
    used_canonical = set()

    # UTM X/Y need special-case matching first (see _detect_utm_column docstring)
    for col in df.columns:
        utm_field = _detect_utm_column(col)
        if utm_field and utm_field not in used_canonical:
            colmap[col] = utm_field
            used_canonical.add(utm_field)

    for col in df.columns:
        if col in colmap:
            continue
        norm = _normalize_header(col)
        for canonical, aliases in SURVEY_COLUMN_ALIASES.items():
            if canonical in used_canonical or not aliases:
                continue
            if norm in aliases or any(norm.startswith(a) for a in aliases):
                colmap[col] = canonical
                used_canonical.add(canonical)
                break

    df = df.rename(columns=colmap)
    keep_cols = [c for c in SURVEY_COLUMN_ALIASES if c in df.columns]
    df = df[keep_cols].copy()

    # Ensure every canonical field exists even if the workbook omitted it
    for canonical in SURVEY_COLUMN_ALIASES:
        if canonical not in df.columns:
            df[canonical] = np.nan

    if "wellbore" in df.columns:
        df = df.dropna(subset=["wellbore"])
        df["wellbore"] = df["wellbore"].astype(str).str.strip()

    # Pressure fields may contain either a numeric psig value or a qualitative status
    # code (e.g. 'V.B', 'M.P', 'ENS'); keep both so the code can still be displayed.
    for numcol in ("whp", "paval", "wht", "anp1", "anp2", "anp3", "choke", "hrs_sce"):
        parsed = df[numcol].apply(_parse_numeric_or_code)
        df[numcol] = parsed.apply(lambda t: t[0])
        df[f"{numcol}_code"] = parsed.apply(lambda t: t[1])

    for coordcol in ("utm_x", "utm_y"):
        df[coordcol] = df[coordcol].apply(_parse_numeric)

    if "state" in df.columns:
        df["state"] = df["state"].apply(
            lambda v: str(v).strip().title() if pd.notna(v) and str(v).strip() != "" else "Unknown"
        )

    if "nature" in df.columns:
        df["nature"] = df["nature"].apply(
            lambda v: str(v).strip().upper() if pd.notna(v) and str(v).strip() != "" else ""
        )

    if "comment" in df.columns:
        df["comment"] = df["comment"].apply(lambda v: str(v).strip() if pd.notna(v) else "")

    # Parse the date column to real datetimes where possible, so "most recent day"
    # can be determined reliably regardless of how Excel stored/typed the cell.
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)

    return df.reset_index(drop=True)


def latest_reading_per_well(df):
    """Collapse a (possibly multi-date, daily-survey) DataFrame down to one row per
    wellbore — the most recently dated reading. Wells with no usable date keep their
    last row in file order (best-effort fallback). Also flags, per well, whether more
    than one date was seen (i.e., this is genuinely a time-series upload)."""
    if df.empty or "wellbore" not in df.columns:
        return df.copy(), False

    has_dates = df["date"].notna().any() if "date" in df.columns else False
    n_dates_per_well = df.groupby("wellbore")["date"].nunique() if has_dates else pd.Series(dtype=int)
    is_history_upload = bool(has_dates and (n_dates_per_well.max() > 1))

    if has_dates:
        # keep the max-date row per well; ties broken by original row order
        df = df.copy()
        df["_orig_order"] = range(len(df))
        idx = (
            df.sort_values(["wellbore", "date", "_orig_order"])
            .groupby("wellbore", as_index=False)
            .tail(1)
            .index
        )
        latest = df.loc[idx].drop(columns="_orig_order").reset_index(drop=True)
    else:
        latest = df.groupby("wellbore", as_index=False).tail(1).reset_index(drop=True)

    return latest, is_history_upload


def classify_annulus_pressure(pressure, code, normal_thr, low_thr, mod_thr, high_thr):
    """Classify a single annulus reading into a severity band based on absolute pressure.
    If a qualitative status code was recorded instead of a number (e.g. 'V.B', 'M.P',
    'ENS'), the reading is flagged 'Status' rather than banded numerically, since its
    magnitude isn't known."""
    if _has_code(code):
        return "Status"
    if pd.isna(pressure):
        return "Not Monitored"
    if pressure <= normal_thr:
        return "Normal"
    elif pressure <= low_thr:
        return "Low"
    elif pressure <= mod_thr:
        return "Moderate"
    elif pressure <= high_thr:
        return "High"
    else:
        return "Severe"


def classify_survey(df, normal_thr, low_thr, mod_thr, high_thr, near_whp_ratio, near_ab_ratio, anp_c_critical_thr):
    """Apply per-annulus severity classification plus three Critical override rules to a
    normalized survey DataFrame:
      - A-annulus at/near WHP (possible tubing-to-A-annulus communication / barrier failure)
      - A-annulus at/near B-annulus pressure (possible A/B annulus communication)
      - C-annulus pressure above a fixed threshold (possible outer-barrier failure)
    """
    out = df.copy()

    for label, col in (("A", "anp1"), ("B", "anp2"), ("C", "anp3")):
        code_col = f"{col}_code"
        out[f"sev_{label}"] = out.apply(
            lambda row: classify_annulus_pressure(
                row[col], row.get(code_col), normal_thr, low_thr, mod_thr, high_thr
            ),
            axis=1,
        )
        out[f"{col}_disp"] = out.apply(
            lambda row: _fmt_pressure_display(row[col], row.get(code_col)), axis=1
        )

    out["whp_disp"] = out.apply(
        lambda row: _fmt_pressure_display(row["whp"], row.get("whp_code")), axis=1
    )

    def a_near_whp(row):
        whp = row.get("whp")
        anp_a = row.get("anp1")
        if pd.isna(whp) or pd.isna(anp_a) or whp <= 0:
            return False
        return anp_a >= near_whp_ratio * whp

    def a_near_b(row):
        anp_a = row.get("anp1")
        anp_b = row.get("anp2")
        if pd.isna(anp_a) or pd.isna(anp_b):
            return False
        hi, lo = max(anp_a, anp_b), min(anp_a, anp_b)
        # Both readings near-zero and roughly equal is normal (properly bled annuli),
        # not evidence of A/B communication — only apply once pressure is above "Normal".
        if hi <= normal_thr:
            return False
        return lo >= near_ab_ratio * hi

    def c_critical(row):
        anp_c = row.get("anp3")
        if pd.isna(anp_c):
            return False
        return anp_c > anp_c_critical_thr

    out["A_near_WHP"] = out.apply(a_near_whp, axis=1)
    out["A_near_B"] = out.apply(a_near_b, axis=1)
    out["C_Critical"] = out.apply(c_critical, axis=1)

    def critical_reasons(row):
        reasons = []
        if row["A_near_WHP"]:
            reasons.append("A≈WHP")
        if row["A_near_B"]:
            reasons.append("A≈B")
        if row["C_Critical"]:
            reasons.append("C-annulus")
        return reasons

    out["Critical_Reasons"] = out.apply(critical_reasons, axis=1)

    def overall(row):
        sevs = [row["sev_A"], row["sev_B"], row["sev_C"]]
        ranked = [SEVERITY_RANK[s] for s in sevs if s not in ("Not Monitored", "Status")]
        if ranked:
            base = SEVERITY_ORDER[max(ranked)]
        elif "Status" in sevs:
            base = "Status"
        else:
            base = "Not Monitored"
        if row["Critical_Reasons"]:
            return "Critical"
        return base

    out["Well_Classification"] = out.apply(overall, axis=1)

    def flag_text(row):
        flags = []
        if row["A_near_WHP"]:
            whp = row.get("whp")
            anp_a = row.get("anp1")
            ratio_pct = (anp_a / whp * 100.0) if whp else float("nan")
            flags.append(
                f"A-annulus ({anp_a:.0f} psig) is ≥{near_whp_ratio*100:.0f}% of WHP "
                f"({whp:.0f} psig) — {ratio_pct:.0f}% — possible tubing-to-A-annulus "
                f"communication / barrier failure."
            )
        if row["A_near_B"]:
            anp_a = row.get("anp1")
            anp_b = row.get("anp2")
            flags.append(
                f"A-annulus ({anp_a:.0f} psig) is at/near B-annulus pressure "
                f"({anp_b:.0f} psig) — possible A/B annulus communication / barrier failure."
            )
        if row["C_Critical"]:
            anp_c = row.get("anp3")
            flags.append(
                f"C-annulus pressure ({anp_c:.0f} psig) exceeds the "
                f"{anp_c_critical_thr:.0f} psig critical threshold — possible outer "
                f"barrier failure."
            )
        return " ".join(flags)

    out["Flags"] = out.apply(flag_text, axis=1)
    return out


# --------------------------------------------------------------------------------------
# Bleed-off test results — SCP vs thermal classification
# Rule: the bleed-down must reduce pressure by >= bleed_frac of the start pressure.
#   - Pressure returns to its original value (any time)  -> SCP
#       (within window_h hours = significant leak; later = minor leak)
#   - Pressure does NOT return, observed >= window_h hours -> Effet thermique
#   - Pressure does NOT return, observed < window_h hours  -> Indéterminé
# --------------------------------------------------------------------------------------
BLEEDOFF_SECTION_TO_ANP = {"eaa": "anp1", "eab": "anp2", "eac": "anp3", "a": "anp1", "b": "anp2", "c": "anp3"}
ANP_TO_LABEL = {"anp1": "A", "anp2": "B", "anp3": "C"}
ORIGIN_RANK = {"SCP": 3, "Indéterminé": 2, "Effet thermique": 1, "Données insuffisantes": 0}
ORIGIN_COLORS = {
    "SCP": "background-color: #f8d7da; color: #7a1c24; font-weight: 600",
    "Effet thermique": "background-color: #d1ecf1; color: #0c5460",
    "Indéterminé": "background-color: #e2e3e5; color: #383d41",
    "Données insuffisantes": "background-color: #f5f5f5; color: #888888",
}


def _wkey(name):
    """Well key for cross-file matching: uppercase alphanumerics only (TOU-9 == TOU9)."""
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _find_col(columns, keywords):
    """First column whose normalized header contains every keyword."""
    for col in columns:
        norm = _normalize_header(col)
        if all(k in norm for k in keywords):
            return col
    return None


def _normalize_ref_type(v):
    """Map the workbook's 'Annular Casing Pressure Type' label to SCP / Effet thermique."""
    s = _normalize_header(v) if pd.notna(v) else ""
    if "scp" in s:
        return "SCP"
    if "therm" in s:
        return "Effet thermique"
    return "—"


def load_bleedoff_workbook(uploaded_file, sheet_name=0):
    """Read a bleed-off / build-up results workbook into a normalized DataFrame."""
    uploaded_file.seek(0)
    raw = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=object)
    cols = list(raw.columns)

    colmap = {
        "well": _find_col(cols, ["well"]),
        "date": _find_col(cols, ["date"]),
        "section": _find_col(cols, ["section"]),
        "p0": _find_col(cols, ["start", "pressure"]),
        "pbleed": _find_col(cols, ["bleed"]),
        "dp": _find_col(cols, ["drop"]),
        "bup": _find_col(cols, ["build"]),
        "hours": _find_col(cols, ["hours"]),
        "obs": _find_col(cols, ["observation"]),
        "ref_type": _find_col(cols, ["type"]),
    }
    if colmap["well"] is None or colmap["section"] is None or colmap["p0"] is None:
        raise ValueError("Colonnes requises introuvables : Well, Section, Start Pressure.")

    def col(key):
        if colmap[key] is None:
            return pd.Series([np.nan] * len(raw), index=raw.index)
        return raw[colmap[key]]

    df = pd.DataFrame(index=raw.index)
    df["wellbore"] = col("well").astype(str).str.strip()
    df["date"] = pd.to_datetime(col("date"), errors="coerce", dayfirst=True)
    df["anp"] = col("section").map(_normalize_header).map(BLEEDOFF_SECTION_TO_ANP)
    df["p0"] = col("p0").apply(_parse_numeric)
    df["pbleed"] = col("pbleed").apply(_parse_numeric)
    df["dp"] = col("dp").apply(_parse_numeric)
    df["bup"] = col("bup").apply(_parse_numeric)
    df["hours"] = col("hours").apply(_parse_numeric)
    df["obs"] = col("obs").apply(lambda v: str(v).strip() if pd.notna(v) else "")
    df["ref_type"] = col("ref_type").apply(_normalize_ref_type)

    df = df[df["anp"].notna() & df["wellbore"].ne("") & df["wellbore"].ne("nan")]
    return df.reset_index(drop=True)


def classify_bleedoff_test(p0, dp, bup, hours, bleed_frac, window_h, return_frac, no_bleed_pct=1.0):
    """Bleed-off classification rule.

    0. Pressure does not bleed at all (drop <= no_bleed_pct of start pressure)
       -> SCP (leak rate greater than bleed rate).
    1. Partial bleed (drop < bleed_frac of start pressure) -> Indéterminé (not conclusive).
    2. Bled below bleed_frac of start pressure:
         a) build-up returns to >= return_frac of start pressure
              - within window_h hours -> SCP (significant leak)
              - after window_h hours  -> SCP (minor leak)
         b) build-up does NOT return to >= return_frac of start pressure -> Effet thermique

    Returns (origin, bled_pct, recov_pct, basis)."""
    if pd.isna(p0) or p0 <= 0:
        return "Données insuffisantes", np.nan, np.nan, "Pression initiale non renseignée"
    if pd.isna(dp):
        return "Données insuffisantes", np.nan, np.nan, "Chute de pression non renseignée"

    bled_pct = dp / p0 * 100.0
    if bled_pct <= no_bleed_pct:
        return (
            "SCP", bled_pct, np.nan,
            f"Pression ne purge pas ({bled_pct:.1f} %) — débit de fuite > débit de purge",
        )

    if dp < bleed_frac * p0:
        return (
            "Indéterminé", bled_pct, np.nan,
            f"Purge partielle ({bled_pct:.0f} % < {bleed_frac * 100:.0f} %) — test non concluant",
        )

    if pd.isna(bup):
        return "Données insuffisantes", bled_pct, np.nan, "Remontée (build-up) non renseignée"

    recov_pct = bup / p0 * 100.0
    ret_label = f"{return_frac * 100:.0f} %"

    if bup >= return_frac * p0:
        if pd.isna(hours) or hours <= window_h:
            return (
                "SCP", bled_pct, recov_pct,
                f"Retour au-dessus de {ret_label} ({recov_pct:.0f} %) en ≤ {window_h:.0f} h — fuite significative",
            )
        return (
            "SCP", bled_pct, recov_pct,
            f"Retour au-dessus de {ret_label} ({recov_pct:.0f} %) après {hours:.0f} h — fuite mineure",
        )

    return (
        "Effet thermique", bled_pct, recov_pct,
        f"Pas de retour au-dessus de {ret_label} ({recov_pct:.0f} %) après purge",
    )


def build_bleedoff_comparison(bleed_df, survey_latest, survey_history, bleed_frac, window_h,
                              return_frac, pct_tol, abs_tol, no_bleed_pct=1.0):
    """Classify the most recent bleed-off test per well/annulus and compare its start
    pressure with the latest NUMERIC value recorded for the same annulus in the survey
    history (falls back to a status code / 'no reading' if none exists)."""
    d = bleed_df.copy()
    d["_wkey"] = d["wellbore"].map(_wkey)
    d = d.sort_values(["_wkey", "anp", "date"], na_position="first")
    n_tests = d.groupby(["_wkey", "anp"]).size().rename("n_tests").reset_index()
    latest = d.groupby(["_wkey", "anp"], as_index=False).tail(1).merge(n_tests, on=["_wkey", "anp"])

    s = survey_latest.copy()
    s["_wkey"] = s["wellbore"].map(_wkey)
    s = s.drop_duplicates("_wkey", keep="last").set_index("_wkey")

    h = survey_history.copy()
    h["_wkey"] = h["wellbore"].map(_wkey)

    rows = []
    for _, r in latest.iterrows():
        origin, bled_pct, recov_pct, basis = classify_bleedoff_test(
            r["p0"], r["dp"], r["bup"], r["hours"], bleed_frac, window_h, return_frac, no_bleed_pct
        )

        actual, survey_date, actual_disp = np.nan, pd.NaT, None
        delta, delta_pct, comp = np.nan, np.nan, "Puits absent du relevé"

        if r["_wkey"] in s.index:
            hist = h[(h["_wkey"] == r["_wkey"]) & h[r["anp"]].notna()]
            if not hist.empty:
                hist = hist.sort_values("date", na_position="first")
                hrow = hist.iloc[-1]
                actual = float(hrow[r["anp"]])
                actual_disp = actual
                survey_date = hrow.get("date", pd.NaT)
                if pd.isna(r["p0"]):
                    comp = "Pression d'essai manquante"
                else:
                    delta = r["p0"] - actual
                    delta_pct = (delta / actual * 100.0) if actual > 0 else np.nan
                    tol = max(abs_tol, pct_tol / 100.0 * actual)
                    comp = "Concordant" if abs(delta) <= tol else "Écart"
            else:
                srow = s.loc[r["_wkey"]]
                survey_date = srow.get("date", pd.NaT)
                code = srow.get(f"{r['anp']}_code")
                if _has_code(code):
                    actual_disp = code
                    comp = "Non comparable (code de statut)"
                else:
                    comp = "Pas de lecture actuelle"

        ref = r["ref_type"]
        if ref == "—" or origin not in ("SCP", "Effet thermique"):
            agree = "—"
        else:
            agree = "Oui" if ref == origin else "Non"

        rows.append({
            "_wkey": r["_wkey"],
            "Well": r["wellbore"],
            "Annulus": ANP_TO_LABEL[r["anp"]],
            "Test date": r["date"],
            "Tests on file": int(r["n_tests"]),
            "Start pressure (psi)": r["p0"],
            "Bleed-to (psi)": r["pbleed"],
            "Pressure drop (psi)": r["dp"],
            "Bled (%)": bled_pct,
            "Build-up (psi)": r["bup"],
            "Recovery (%)": recov_pct,
            "Hours of BUP": r["hours"],
            "Computed origin": origin,
            "Basis": basis,
            "Latest recorded (psi)": actual_disp,
            "Recorded date": survey_date,
            "Δ test − recorded (psi)": delta,
            "Δ %": delta_pct,
            "Comparison": comp,
            "Reference type": ref,
            "Agrees with reference": agree,
            "_actual_num": actual,
        })
    return pd.DataFrame(rows)


def summarize_bleedoff_wells(cmp_df):
    """One row per well: worst origin across its annuli."""
    out = []
    for well, g in cmp_df.groupby("Well", sort=True):
        worst = max(g["Computed origin"], key=lambda o: ORIGIN_RANK.get(o, 0))
        scp_annuli = g.loc[g["Computed origin"] == "SCP", "Annulus"].tolist()
        out.append({
            "Well": well,
            "Annuli tested": ", ".join(sorted(g["Annulus"].unique())),
            "Well origin": worst,
            "SCP annuli": ", ".join(scp_annuli) if scp_annuli else "—",
        })
    return pd.DataFrame(out)



# --------------------------------------------------------------------------------------
# Page title
# --------------------------------------------------------------------------------------
st.title("Multi-Well Pressure Survey — ACP Severity & Bleed-off Classification")
st.caption("Standalone survey screening per API RP 90-2: severity bands, Critical rules, and SCP vs thermal classification from bleed-off tests.")

with st.container():
    st.subheader("Multi-Well Pressure Survey — Severity Screening")
    st.markdown(
        "Upload a well survey workbook — a **daily/periodic survey** (oil or gas template) "
        "or a **monthly survey** (Puits / Coordonnées UTM / Nature / Situation / WHP / "
        "EA-A / EA-B / EA-C / Date de relevé / Observations) — to automatically screen "
        "every well's annuli (A/B/C = ANP1/ANP2/ANP3, or EA-A/EA-B/EA-C) against WHP and "
        "severity thresholds. Column order doesn't matter and both templates are handled "
        "automatically; columns are matched by name."
    )
    st.caption(
        "Rule of thumb applied: three independent rules can each flag a well **Critical**, "
        "regardless of the absolute severity bands below — (1) A-annulus at/near WHP, "
        "(2) A-annulus at/near B-annulus pressure, and (3) C-annulus pressure above a "
        "fixed threshold."
    )
    st.caption(
        "The monthly template often records a field status instead of a psig value "
        "(e.g. **V.B**, **M.P**, **ENS**) when a gauge is blocked, missing, or flooded-out. "
        "These are preserved and shown as-is rather than treated as a pressure reading — "
        "such cells are flagged **Status** (blue) instead of a numeric severity band."
    )
    st.caption(
        "📅 If your file has **daily readings** (several dates per well), classification "
        "and the Critical flags below are always based on each well's **most recent day**. "
        "Pick a well further down to see its full pressure-over-time history."
    )

    st.markdown("##### 📂 Survey workbooks — upload the oil template, the gas template, or both")
    up_oil, up_gas = st.columns(2)
    with up_oil:
        oil_file = st.file_uploader(
            "Oil template survey (.xlsx / .xls)", type=["xlsx", "xls"], key="oil_uploader"
        )
    with up_gas:
        gas_file = st.file_uploader(
            "Gas template survey (.xlsx / .xls)", type=["xlsx", "xls"], key="gas_uploader"
        )

    with st.expander("⚙️ Classification thresholds", expanded=False):
        st.markdown("**Critical rule 1 — Annulus-A “near WHP”** (possible tubing-to-A communication)")
        near_whp_ratio_pct = st.slider(
            "Flag Critical when ANP1 (A-annulus) ≥ this % of WHP",
            min_value=50, max_value=100, value=80, step=5,
        )
        st.markdown("**Critical rule 2 — Annulus-A “near” Annulus-B** (possible A/B communication)")
        near_ab_ratio_pct = st.slider(
            "Flag Critical when A-annulus and B-annulus pressures are within this % of each other "
            "(the smaller ≥ this % of the larger, both > 0 psig)",
            min_value=50, max_value=100, value=80, step=5,
        )
        st.markdown("**Critical rule 3 — Annulus-C pressure detected** (possible outer-barrier failure)")
        anp_c_critical_thr = st.number_input(
            "Flag Critical when C-annulus (ANP3 / EA-C) pressure exceeds (psig)",
            min_value=0.0, value=100.0, step=10.0,
        )
        st.markdown("**Absolute severity bands for any annulus (A/B/C), psig**")
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            normal_thr = st.number_input("Normal ≤", min_value=0.0, value=50.0, step=10.0)
        with b2:
            low_thr = st.number_input("Low ≤", min_value=0.0, value=150.0, step=10.0)
        with b3:
            mod_thr = st.number_input("Moderate ≤", min_value=0.0, value=400.0, step=10.0)
        with b4:
            high_thr = st.number_input("High ≤ (Severe above)", min_value=0.0, value=700.0, step=10.0)

    if oil_file is None and gas_file is None:
        st.info("Upload the oil-template and/or gas-template survey workbook to run the screening.")
    else:
        survey_frames = []
        for template_label, f in (("Huile", oil_file), ("Gaz", gas_file)):
            if f is None:
                continue
            try:
                xls = pd.ExcelFile(f)
                sheet = xls.sheet_names[0]
                if len(xls.sheet_names) > 1:
                    sheet = st.selectbox(
                        f"Sheet to use — {template_label} template", xls.sheet_names,
                        key=f"sheet_{template_label}",
                    )
                part = load_survey_workbook(f, sheet_name=sheet)
                part["template"] = template_label
                survey_frames.append(part)
            except Exception as e:
                st.error(f"Could not read the {template_label} workbook: {e}")

        survey_df_full = pd.concat(survey_frames, ignore_index=True) if survey_frames else None

        if survey_df_full is not None:
            if survey_df_full.empty:
                st.warning("No well rows were recognized in this file. Check that a 'Wellbore' column exists.")
            else:
                # Collapse to one row per well (most recent date) for classification,
                # while keeping the full history (survey_df_full) around for the chart.
                survey_df, is_history_upload = latest_reading_per_well(survey_df_full)

                if is_history_upload:
                    n_days_total = survey_df_full["date"].nunique()
                    st.success(
                        f"📅 Daily pressure data detected — **{n_days_total} distinct date(s)** "
                        f"across the file. Classification below uses each well's **most recent** "
                        f"reading; use the well picker further down for the full history plot."
                    )

                classified = classify_survey(
                    survey_df, normal_thr, low_thr, mod_thr, high_thr,
                    near_whp_ratio_pct / 100.0, near_ab_ratio_pct / 100.0, anp_c_critical_thr,
                )

                n_total = len(classified)
                n_critical = int((classified["Well_Classification"] == "Critical").sum())
                n_high_severe = int(classified["Well_Classification"].isin(["High", "Severe"]).sum())
                n_normal = int(classified["Well_Classification"].isin(["Normal", "Not Monitored"]).sum())
                n_status = int((classified["Well_Classification"] == "Status").sum())

                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Wells screened", n_total)
                m2.metric("🔴 Critical", n_critical)
                m3.metric("🟠 High / Severe", n_high_severe)
                m4.metric("🟢 Normal / Not monitored", n_normal)
                m5.metric("🔵 Status code only", n_status)

                st.markdown("---")

                tmpl_counts = survey_df.groupby("template")["wellbore"].nunique().to_dict()
                st.caption(
                    "Wells analysed — " + " | ".join(f"{k}: {v}" for k, v in tmpl_counts.items())
                    + f" | total: {n_total}"
                    + (" — wells present in both templates use their most recent reading." if (survey_df_full.groupby("wellbore")["template"].nunique() > 1).any() else "")
                )

                # Well classification distribution chart
                counts = (
                    classified["Well_Classification"]
                    .value_counts()
                    .reindex(SEVERITY_ORDER)
                    .dropna()
                    .reset_index()
                )
                counts.columns = ["Classification", "Count"]
                fig_counts = px.bar(
                    counts,
                    x="Classification",
                    y="Count",
                    color="Classification",
                    color_discrete_map={
                        "Not Monitored": "#bdbdbd",
                        "Status": "#4a86e8",
                        "Normal": "#28a745",
                        "Low": "#ffc107",
                        "Moderate": "#fd7e14",
                        "High": "#dc3545",
                        "Severe": "#b21f2d",
                        "Critical": "#7b0000",
                    },
                    category_orders={"Classification": SEVERITY_ORDER},
                    text="Count",
                )
                fig_counts.update_layout(showlegend=False, height=350, margin=dict(t=30))
                st.plotly_chart(fig_counts, use_container_width=True)

                if n_critical > 0:
                    st.error(
                        f"⚠️ {n_critical} well(s) flagged **Critical** under one or more rules: "
                        f"A-annulus at/near WHP, A-annulus at/near B-annulus pressure, or "
                        f"C-annulus pressure above threshold. These should be prioritized for "
                        f"diagnostic testing per API RP 90-2 §10."
                    )

                    # Breakdown of WHICH critical rule(s) fired, per annulus case (A/B/C)
                    reason_labels = {
                        "A≈WHP": "A-annulus ≈ WHP<br>(tubing↔A communication)",
                        "A≈B": "A-annulus ≈ B-annulus<br>(A↔B communication)",
                        "C-annulus": f"C-annulus > {anp_c_critical_thr:.0f} psig<br>(outer barrier)",
                    }
                    reason_counts = {k: 0 for k in reason_labels}
                    for reasons in classified["Critical_Reasons"]:
                        for r in reasons:
                            reason_counts[r] = reason_counts.get(r, 0) + 1
                    reasons_df = pd.DataFrame(
                        {
                            "Case": [reason_labels[k] for k in reason_labels],
                            "Wells flagged": [reason_counts[k] for k in reason_labels],
                            "Annulus": ["A", "A/B", "C"],
                        }
                    )
                    fig_reasons = px.bar(
                        reasons_df,
                        x="Case",
                        y="Wells flagged",
                        color="Annulus",
                        color_discrete_map={"A": "#dc3545", "A/B": "#fd7e14", "C": "#7b0000"},
                        text="Wells flagged",
                        title="Critical flags by annulus case (A, A/B, C)",
                    )
                    fig_reasons.update_layout(height=380, margin=dict(t=50), xaxis_title=None)
                    st.plotly_chart(fig_reasons, use_container_width=True)
                    st.caption(
                        "A well can be flagged under more than one rule at once, so the bars "
                        "above can sum to more than the total Critical well count."
                    )

                # WHP vs A-annulus scatter to visualize how close wells are to the critical line
                scatter_df = classified.dropna(subset=["whp", "anp1"]).copy()
                if not scatter_df.empty:
                    fig_scatter = go.Figure()
                    fig_scatter.add_trace(
                        go.Scatter(
                            x=scatter_df["whp"],
                            y=scatter_df["anp1"],
                            mode="markers",
                            text=scatter_df["wellbore"],
                            marker=dict(
                                size=10,
                                color=[
                                    "#7b0000" if c == "Critical" else "#1f77b4"
                                    for c in scatter_df["Well_Classification"]
                                ],
                            ),
                            hovertemplate="%{text}<br>WHP=%{x:.0f} psig<br>A-Annulus=%{y:.0f} psig<extra></extra>",
                            name="Wells",
                        )
                    )
                    max_axis = float(max(scatter_df["whp"].max(), scatter_df["anp1"].max())) * 1.05 + 1
                    fig_scatter.add_trace(
                        go.Scatter(
                            x=[0, max_axis],
                            y=[0, max_axis * (near_whp_ratio_pct / 100.0)],
                            mode="lines",
                            line=dict(color="gray", dash="dash"),
                            name=f"Critical threshold ({near_whp_ratio_pct}% of WHP)",
                        )
                    )
                    fig_scatter.update_layout(
                        xaxis_title="WHP (psig)",
                        yaxis_title="A-Annulus / ANP1 (psig)",
                        height=450,
                        margin=dict(t=30),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                    )
                    st.plotly_chart(fig_scatter, use_container_width=True)
                    st.caption(
                        "Wells above/near the dashed critical threshold line have an A-annulus "
                        "pressure approaching WHP. Red markers are flagged Critical."
                    )

                # ------------------------------------------------------------------
                # Pressure-over-time plot for a single, selected well
                # ------------------------------------------------------------------
                st.markdown("---")
                st.markdown("##### 📈 Pressure monitoring over time — single well")

                wells_with_history = []
                if is_history_upload:
                    hist_counts = survey_df_full.dropna(subset=["date"]).groupby("wellbore")["date"].nunique()
                    wells_with_history = sorted(hist_counts[hist_counts > 1].index.tolist())

                if not is_history_upload:
                    st.caption(
                        "This file has a single reading per well (no repeated dates), so there is "
                        "no time history to plot yet. Upload a file with multiple dated readings "
                        "per well to see a well's pressure trend over time."
                    )
                else:
                    well_options = wells_with_history if wells_with_history else sorted(survey_df_full["wellbore"].unique().tolist())
                    selected_well = st.selectbox("Select a well", well_options, key="history_well_picker")

                    well_hist = (
                        survey_df_full[survey_df_full["wellbore"] == selected_well]
                        .dropna(subset=["date"])
                        .sort_values("date")
                        .copy()
                    )

                    if well_hist.empty:
                        st.caption("No dated readings available for this well.")
                    else:
                        fig_hist = go.Figure()
                        series_specs = [
                            ("whp", "WHP", "#000000"),
                            ("anp1", "A-Annulus (ANP1 / EA-A)", "#dc3545"),
                            ("anp2", "B-Annulus (ANP2 / EA-B)", "#fd7e14"),
                            ("anp3", "C-Annulus (ANP3 / EA-C)", "#1f77b4"),
                        ]
                        for col, label, color in series_specs:
                            s = well_hist[["date", col]].dropna(subset=[col])
                            if s.empty:
                                continue
                            fig_hist.add_trace(
                                go.Scatter(
                                    x=s["date"],
                                    y=s[col],
                                    mode="lines+markers",
                                    name=label,
                                    line=dict(color=color),
                                )
                            )
                        fig_hist.add_hline(
                            y=anp_c_critical_thr, line_dash="dot", line_color="gray",
                            annotation_text="C-annulus critical threshold", annotation_position="bottom right",
                        )
                        fig_hist.update_layout(
                            xaxis_title="Date",
                            yaxis_title="Pressure (psig)",
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                            height=450,
                            margin=dict(t=60),
                        )
                        st.plotly_chart(fig_hist, use_container_width=True)

                        last_row = well_hist.iloc[-1]
                        st.caption(
                            f"Last recorded reading for **{selected_well}**: "
                            f"{_fmt_date_display(last_row['date'])} — "
                            f"WHP={_fmt_pressure_display(last_row.get('whp'), last_row.get('whp_code'))} psig, "
                            f"A={_fmt_pressure_display(last_row.get('anp1'), last_row.get('anp1_code'))}, "
                            f"B={_fmt_pressure_display(last_row.get('anp2'), last_row.get('anp2_code'))}, "
                            f"C={_fmt_pressure_display(last_row.get('anp3'), last_row.get('anp3_code'))} "
                            f"(this is the reading used for classification above)."
                        )

                st.markdown("---")
                st.markdown("##### Classified well table")

                filt_col, extra_col = st.columns([1, 2])
                with filt_col:
                    present_classes = [c for c in SEVERITY_ORDER if c in classified["Well_Classification"].unique()]
                    severity_filter = st.selectbox(
                        "Filter by classification",
                        ["All"] + present_classes,
                        index=0,
                    )
                with extra_col:
                    show_extra_cols = st.checkbox(
                        "Show additional columns (Nature, Date, UTM coordinates, Observations)",
                        value=False,
                    )

                classified_disp = classified.copy()
                if severity_filter != "All":
                    classified_disp = classified_disp[classified_disp["Well_Classification"] == severity_filter]
                if "date" in classified_disp.columns:
                    classified_disp["date"] = classified_disp["date"].apply(_fmt_date_display)

                display_cols = {
                    "wellbore": "Wellbore",
                    "template": "Template",
                    "nature": "Nature",
                    "state": "Situation / State",
                    "whp_disp": "WHP (psig)",
                    "anp1_disp": "ANP1 / A (psig)",
                    "sev_A": "A Severity",
                    "anp2_disp": "ANP2 / B (psig)",
                    "sev_B": "B Severity",
                    "anp3_disp": "ANP3 / C (psig)",
                    "sev_C": "C Severity",
                    "Well_Classification": "Well Classification",
                    "Flags": "Flags",
                }
                extra_cols = {
                    "date": "Date" + (" (latest)" if is_history_upload else ""),
                    "utm_x": "UTM X",
                    "utm_y": "UTM Y",
                    "comment": "Observations",
                }
                if show_extra_cols:
                    display_cols = {**display_cols, **extra_cols}

                table_df = classified_disp[[c for c in display_cols if c in classified_disp.columns]].rename(columns=display_cols)
                st.caption(f"Showing {len(table_df)} of {n_total} well(s)" + (f" — filtered to **{severity_filter}**." if severity_filter != "All" else "."))

                sev_cols_present = [display_cols[c] for c in ("sev_A", "sev_B", "sev_C", "Well_Classification") if c in display_cols]

                def _style_severity(val):
                    return SEVERITY_COLORS.get(val, "")

                styled = table_df.style.map(_style_severity, subset=sev_cols_present)
                st.dataframe(styled, use_container_width=True, height=480)

                csv_out = table_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Download classified survey (.csv)",
                    data=csv_out,
                    file_name=f"pressure_survey_classified_{datetime.now().date()}.csv",
                    mime="text/csv",
                )

                # ==========================================================
                # Bleed-off test results — SCP vs thermal classification
                # ==========================================================
                st.markdown("---")
                st.markdown("##### 🧪 Bleed-off test results — SCP vs thermal classification")
                st.caption(
                    "Rules: no bleed at all → **SCP** (leak rate > bleed rate). Bled below 50 % of start pressure "
                    "and build-up returns above 50 % → **SCP** (within 24 h = significant; later = minor). "
                    "Bled below 50 % but does not return above 50 % → **Effet thermique**. "
                    "Partial bleed (between no-bleed limit and 50 %) → Indéterminé."
                )
                bleed_file = st.file_uploader(
                    "Upload bleed-off results Excel file (.xlsx / .xls)",
                    type=["xlsx", "xls"],
                    key="bleedoff_uploader",
                )

                with st.expander("⚙️ Bleed-off rule & comparison thresholds", expanded=False):
                    r1, r2, r3 = st.columns(3)
                    with r1:
                        no_bleed_pct = st.number_input(
                            "No-bleed limit (% of start pressure; at or below = leak rate > bleed rate → SCP)",
                            min_value=0.0, value=1.0, step=0.5,
                        )
                        bleed_frac_pct = st.slider(
                            "Minimum bleed-down (% of start pressure)", 10, 100, 50, 5
                        )
                        window_h = st.number_input(
                            "Return window (hours) for SCP", min_value=1.0, value=24.0, step=1.0
                        )
                    with r2:
                        return_pct = st.number_input(
                            "Return threshold (% of start pressure)", min_value=1.0, max_value=100.0, value=50.0, step=5.0
                        )
                    with r3:
                        pct_tol = st.number_input(
                            "Match tolerance vs survey (% of survey pressure)", min_value=0.0, value=15.0, step=5.0
                        )
                        abs_tol = st.number_input(
                            "Match tolerance vs survey (psi, minimum)", min_value=0.0, value=20.0, step=5.0
                        )

                if bleed_file is not None:
                    try:
                        bleed_df_full = load_bleedoff_workbook(bleed_file)
                    except Exception as e:
                        bleed_df_full = None
                        st.error(f"Could not read bleed-off workbook: {e}")

                    if bleed_df_full is not None and bleed_df_full.empty:
                        st.warning("No bleed-off rows were recognized. Check the Well and Section columns.")
                    elif bleed_df_full is not None:
                        cmp_df = build_bleedoff_comparison(
                            bleed_df_full, survey_df, survey_df_full,
                            bleed_frac_pct / 100.0, window_h, return_pct / 100.0, pct_tol, abs_tol,
                            no_bleed_pct,
                        )
                        sel_sections = st.multiselect(
                            "Filter by annulus section",
                            ["A", "B", "C"],
                            default=["A", "B", "C"],
                            key="bo_section_filter",
                        )
                        cmp_df = cmp_df[cmp_df["Annulus"].isin(sel_sections)]
                        if cmp_df.empty:
                            st.info("No annulus selected in the section filter.")
                        n_cmp = len(cmp_df)
                        n_scp = int((cmp_df["Computed origin"] == "SCP").sum())
                        n_th = int((cmp_df["Computed origin"] == "Effet thermique").sum())
                        n_ind = int((cmp_df["Computed origin"] == "Indéterminé").sum())
                        n_conc = int((cmp_df["Comparison"] == "Concordant").sum())
                        decided = cmp_df[cmp_df["Agrees with reference"] != "—"]
                        agree_rate = (
                            f"{(decided['Agrees with reference'] == 'Oui').mean() * 100:.0f}%"
                            if len(decided) else "—"
                        )

                        b1, b2, b3, b4, b5 = st.columns(5)
                        b1.metric("Annuli classified", n_cmp)
                        b2.metric("🔴 SCP", n_scp)
                        b3.metric("🔵 Thermal effect", n_th)
                        b4.metric("⚪ Indeterminate", n_ind)
                        b5.metric("Agreement with file labels", agree_rate)

                        st.caption(
                            f"Current survey vs test start pressure: {n_conc} of {n_cmp} annulus(es) "
                            f"match within tolerance. Agreement is computed against the "
                            f"'Annular Casing Pressure Type' column of the uploaded file where it is set."
                        )

                        # Scatter: current survey pressure vs bleed-off start pressure
                        sc = cmp_df.dropna(subset=["_actual_num", "Start pressure (psi)"]).copy()
                        if not sc.empty:
                            fig_bo = go.Figure()
                            for origin_name, color in [
                                ("SCP", "#dc3545"), ("Effet thermique", "#0dcaf0"), ("Indéterminé", "#6c757d"),
                            ]:
                                g = sc[sc["Computed origin"] == origin_name]
                                if g.empty:
                                    continue
                                fig_bo.add_trace(go.Scatter(
                                    x=g["_actual_num"],
                                    y=g["Start pressure (psi)"],
                                    mode="markers",
                                    name=origin_name,
                                    text=g["Well"] + " — " + g["Annulus"],
                                    marker=dict(size=11, color=color),
                                    hovertemplate="%{text}<br>Recorded=%{x:.0f} psi<br>Test start=%{y:.0f} psi<extra></extra>",
                                ))
                            max_v = float(max(sc["_actual_num"].max(), sc["Start pressure (psi)"].max())) * 1.05 + 1
                            fig_bo.add_trace(go.Scatter(
                                x=[0, max_v], y=[0, max_v], mode="lines",
                                line=dict(color="gray", dash="dash"), name="Equal pressure",
                            ))
                            fig_bo.update_layout(
                                xaxis_title="Latest recorded pressure (psi)",
                                yaxis_title="Bleed-off test start pressure (psi)",
                                height=440, margin=dict(t=30),
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                            )
                            st.plotly_chart(fig_bo, use_container_width=True)
                            st.caption("Points on the dashed line: test start pressure equals the latest recorded pressure.")

                        # Well-level summary
                        st.markdown("**Well-level origin** (worst annulus governs)")
                        well_sum = summarize_bleedoff_wells(cmp_df)
                        st.dataframe(
                            well_sum.style.map(lambda v: ORIGIN_COLORS.get(v, ""), subset=["Well origin"]),
                            use_container_width=True, height=300,
                        )

                        # Annulus-level detail
                        st.markdown("**Annulus-level detail**")
                        detail_cols = [
                            "Well", "Annulus", "Test date", "Tests on file", "Start pressure (psi)",
                            "Bleed-to (psi)", "Pressure drop (psi)", "Bled (%)", "Build-up (psi)",
                            "Recovery (%)", "Hours of BUP", "Computed origin", "Basis",
                            "Latest recorded (psi)", "Recorded date", "Δ test − recorded (psi)", "Δ %",
                            "Comparison", "Reference type", "Agrees with reference",
                        ]
                        detail = cmp_df[detail_cols].copy()
                        for c in ("Test date", "Recorded date"):
                            detail[c] = detail[c].apply(_fmt_date_display)
                        for c in ("Bled (%)", "Recovery (%)", "Δ %"):
                            detail[c] = detail[c].round(1)
                        st.dataframe(
                            detail.style.map(lambda v: ORIGIN_COLORS.get(v, ""), subset=["Computed origin"]),
                            use_container_width=True, height=420,
                        )

                        st.download_button(
                            "⬇️ Download bleed-off classification (.csv)",
                            data=detail.to_csv(index=False).encode("utf-8"),
                            file_name=f"bleedoff_origin_classification_{datetime.now().date()}.csv",
                            mime="text/csv",
                        )

    st.markdown("---")
    st.caption(
        "This screening uses simple, configurable rules (absolute severity bands + an "
        "A-annulus-vs-WHP proximity check) to triage a well population for further review. "
        "It is not a substitute for the formal API RP 90-2 §8 Diagnostic Threshold "
        "methodology or engineering judgment on a per-well basis."
    )
