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

DISCLAIMER: This tool implements the qualitative decision logic described in
API RP 90-2 to aid diagnostics and documentation. It does not replace sound
engineering judgment, applicable regulations, or a qualified person's review.
"""

import io
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --------------------------------------------------------------------------------------
# Page config
# --------------------------------------------------------------------------------------
st.set_page_config(
    page_title="ACP Bleed-down/Build-up Test — API RP 90-2",
    page_icon="🛢️",
    layout="wide",
)

TOLERANCE_DEFAULT = 0.0  # psig, "near-zero" tolerance for "bled to 0 psig"
MAX_HOURS_DEFAULT = 24

# --------------------------------------------------------------------------------------
# Session state initialization
# --------------------------------------------------------------------------------------
def _default_series(n=13):
    hours = list(range(0, n))
    return pd.DataFrame(
        {
            "Hour": hours,
            "Annulus_Pressure_psig": [None] * n,
            "Adjacent_Annulus_psig": [None] * n,
            "Tubing_Pressure_psig": [None] * n,
        }
    )


if "bleed_df" not in st.session_state:
    st.session_state.bleed_df = _default_series(13)

if "buildup_df" not in st.session_state:
    st.session_state.buildup_df = _default_series(13)

if "thermal_df" not in st.session_state:
    st.session_state.thermal_df = _default_series(13)

# --------------------------------------------------------------------------------------
# Sidebar — Well / Annulus / Test Administration (Section 11.3.2 fields)
# --------------------------------------------------------------------------------------
st.sidebar.title("🛢️ Well & Test Information")
st.sidebar.caption("Fields align with API RP 90-2 §11.3.2 diagnostic-test documentation requirements.")

with st.sidebar.expander("Facility / Well Identification", expanded=True):
    facility_id = st.text_input("Facility identification", "")
    well_name = st.text_input("Well name", "")
    well_api = st.text_input("Well API number (optional)", "")
    lease_name = st.text_input("Lease name (optional)", "")
    well_type = st.selectbox("Well type", ["Producing", "Injection", "Observation/Monitoring", "Storage"])
    well_status = st.selectbox("Well status at test time", ["Flowing", "Shut-in", "Gas lift", "Injection"])

with st.sidebar.expander("Test Administration", expanded=True):
    test_date = st.date_input("Test date", datetime.now().date())
    tester_name = st.text_input("Person conducting test", "")
    test_procedure_ref = st.text_input(
        "Test procedure reference", "API RP 90-2 §10.2 Bleed-down/Build-up Test"
    )
    reason_for_test = st.text_area(
        "Reason for conducting the test",
        "Annular casing pressure (ACP) observed above the Upper Diagnostic Threshold (DT).",
        height=70,
    )

with st.sidebar.expander("Annulus Being Evaluated", expanded=True):
    annulus_id = st.selectbox("Annulus identification", ["A", "B", "C", "D", "Other"])
    pressure_type_suspected = st.selectbox(
        "Suspected annular casing pressure (ACP) type (§4)",
        [
            "Unknown / To be determined",
            "Sustained Casing Pressure (SCP)",
            "Thermally Induced Casing Pressure",
            "Operator-imposed Pressure",
            "Combination (Thermal + SCP)",
        ],
    )
    mawop = st.number_input("MAWOP for this annulus (psig) — §7", min_value=0.0, value=1000.0, step=50.0)
    upper_dt = st.number_input("Upper Diagnostic Threshold (psig) — §8", min_value=0.0, value=200.0, step=25.0)
    lower_dt_enabled = st.checkbox("Lower DT applicable (e.g., operator-imposed pressure present)?", value=False)
    lower_dt = st.number_input("Lower Diagnostic Threshold (psig)", min_value=0.0, value=0.0, step=25.0, disabled=not lower_dt_enabled)

with st.sidebar.expander("Applied / Operator-imposed Pressure", expanded=False):
    applied_pressure_present = st.checkbox("Operator-imposed pressure applied to this annulus?", value=False)
    applied_reason = st.text_input("Reason/purpose for applied pressure", "", disabled=not applied_pressure_present)
    applied_rate = st.text_input("Applied pressure medium (N2, produced gas, liquid, etc.)", "", disabled=not applied_pressure_present)

with st.sidebar.expander("Pre-test Conditions", expanded=True):
    pre_test_pressure = st.number_input("Annulus pressure prior to bleed-down (psig)", min_value=0.0, value=300.0, step=10.0)
    tubing_pressure_flowing = st.number_input("Flowing tubing pressure (psig, if applicable)", min_value=0.0, value=0.0, step=10.0)
    tubing_pressure_shutin = st.number_input("Last shut-in tubing pressure (psig)", min_value=0.0, value=0.0, step=10.0)
    production_rate = st.text_input("Production/injection rate (oil, gas, water)", "")

with st.sidebar.expander("Tolerances / Test Limits", expanded=False):
    zero_tolerance = st.number_input(
        "‘Near-zero’ tolerance (psig) used to judge whether pressure bled to 0 psig",
        min_value=0.0, value=TOLERANCE_DEFAULT, step=1.0,
    )
    max_hours = st.number_input("Maximum cumulative bleed-down time (hours, §10.2.1(k))", min_value=1, value=MAX_HOURS_DEFAULT, step=1)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Reference: API RP 90-2, First Edition, April 2016 — "
    "§8 Diagnostic Thresholds, §10 Annular Casing Pressure Evaluation Tests, "
    "§11 Documentation."
)

# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------
st.title("Annular Casing Pressure — Bleed-down / Build-up Test")
st.caption(
    "A diagnostic-test workbook implementing the qualitative logic of **API RP 90-2 §10** "
    "for onshore well annular casing pressure (ACP) evaluation."
)

if pre_test_pressure > mawop:
    st.error(
        f"⚠️ Pre-test pressure ({pre_test_pressure:.0f} psig) exceeds MAWOP "
        f"({mawop:.0f} psig) for this annulus. Address per the ACP management "
        f"process (§7/§12) before proceeding with routine diagnostics."
    )
elif pre_test_pressure > upper_dt:
    st.warning(
        f"Pre-test pressure ({pre_test_pressure:.0f} psig) is **above the Upper DT** "
        f"({upper_dt:.0f} psig) — diagnostics are warranted per §8/§10.1."
    )
elif lower_dt_enabled and pre_test_pressure < lower_dt:
    st.warning(
        f"Pre-test pressure ({pre_test_pressure:.0f} psig) is **below the Lower DT** "
        f"({lower_dt:.0f} psig) — this can indicate a barrier failure or communication "
        f"path per §8.1/§10.1(d)."
    )
else:
    st.info("Pre-test pressure is currently within the established Diagnostic Thresholds.")

tabs = st.tabs(
    [
        "1️⃣ Bleed-down Data",
        "2️⃣ Build-up Data",
        "3️⃣ Analysis (§10.2.2)",
        "4️⃣ Thermal Screening (§10.3)",
        "5️⃣ Chart",
        "6️⃣ Report / Export",
    ]
)

# --------------------------------------------------------------------------------------
# Tab 1 — Bleed-down data entry
# --------------------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Bleed-down Test Data")
    st.markdown(
        "Per **§10.2.1**, bleed-down should be conducted through an appropriately sized "
        "valve/choke, with pressures recorded hourly (or continuously) until pressure "
        "reaches 0 psig, a maximum liquid volume is recovered, or the maximum cumulative "
        "time (default 24 h) is reached."
    )
    col1, col2 = st.columns(2)
    with col1:
        bleed_start = st.text_input("Bleed-down start time (e.g. 08:00)", "")
        fluid_type = st.selectbox("Type of fluid recovered", ["None", "Gas only", "Oil", "Water", "Mixed/Emulsion", "Mud", "Unknown"])
    with col2:
        bleed_end = st.text_input("Bleed-down end time", "")
        fluid_volume = st.number_input("Volume of fluid bled off (bbl)", min_value=0.0, value=0.0, step=0.1)

    st.markdown("**Hourly pressure readings during bleed-down** (add/remove rows as needed):")
    st.session_state.bleed_df = st.data_editor(
        st.session_state.bleed_df,
        num_rows="dynamic",
        use_container_width=True,
        key="bleed_editor",
        column_config={
            "Hour": st.column_config.NumberColumn("Hour", help="Elapsed hours since bleed-down start", step=1),
            "Annulus_Pressure_psig": st.column_config.NumberColumn(f"'{annulus_id}' Annulus (psig)", step=1.0),
            "Adjacent_Annulus_psig": st.column_config.NumberColumn("Adjacent Annulus (psig)", step=1.0, help="Monitor per §10.2.1(d) to detect casing-to-casing communication"),
            "Tubing_Pressure_psig": st.column_config.NumberColumn("Tubing Pressure (psig)", step=1.0, help="Monitor per §10.2.1(e)/(f)"),
        },
    )

# --------------------------------------------------------------------------------------
# Tab 2 — Build-up data entry
# --------------------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Build-up Test Data")
    st.markdown(
        "Per **§10.2.1(l)**, immediately following bleed-down, monitor and document the "
        "rate of pressure build-up (typically up to 24 h, or shorter if pressure stabilizes)."
    )
    st.markdown("**Hourly pressure readings during build-up** (Hour = 0 at the moment bleed-down stopped):")
    st.session_state.buildup_df = st.data_editor(
        st.session_state.buildup_df,
        num_rows="dynamic",
        use_container_width=True,
        key="buildup_editor",
        column_config={
            "Hour": st.column_config.NumberColumn("Hour", help="Elapsed hours since bleed-down stopped", step=1),
            "Annulus_Pressure_psig": st.column_config.NumberColumn(f"'{annulus_id}' Annulus (psig)", step=1.0),
            "Adjacent_Annulus_psig": st.column_config.NumberColumn("Adjacent Annulus (psig)", step=1.0),
            "Tubing_Pressure_psig": st.column_config.NumberColumn("Tubing Pressure (psig)", step=1.0),
        },
    )
    fluids_replaced = st.checkbox("Were bled fluids replaced (e.g., with brine)? (§10.2.1(m))", value=False)
    replacement_notes = st.text_area("Replacement fluid notes", "", disabled=not fluids_replaced, height=60)

# --------------------------------------------------------------------------------------
# Analysis helper functions (encoding §10.2.2 logic)
# --------------------------------------------------------------------------------------
def clean_series(df):
    d = df.dropna(subset=["Annulus_Pressure_psig"]).copy()
    d = d.sort_values("Hour")
    return d


def analyze_bleed_buildup(bleed_df, buildup_df, pre_pressure, tol, max_h):
    bleed = clean_series(bleed_df)
    build = clean_series(buildup_df)

    result = {
        "bled_to_zero": None,
        "bleed_min_pressure": None,
        "bleed_time_to_zero": None,
        "buildup_final_pressure": None,
        "buildup_occurred": None,
        "leak_size": None,
        "diagnosis_code": None,
        "diagnosis_title": "",
        "diagnosis_detail": "",
        "recommendation": "",
        "communication_adjacent": False,
        "communication_tubing": False,
    }

    if bleed.empty:
        result["diagnosis_title"] = "Insufficient data"
        result["diagnosis_detail"] = "No bleed-down pressure readings have been entered yet."
        return result

    min_row = bleed.loc[bleed["Annulus_Pressure_psig"].idxmin()]
    result["bleed_min_pressure"] = float(min_row["Annulus_Pressure_psig"])
    result["bled_to_zero"] = result["bleed_min_pressure"] <= tol
    if result["bled_to_zero"]:
        zero_rows = bleed[bleed["Annulus_Pressure_psig"] <= tol]
        if not zero_rows.empty:
            result["bleed_time_to_zero"] = float(zero_rows["Hour"].min())

    bleed_within_time = (bleed["Hour"].max() <= max_h) if not bleed.empty else False

    # Adjacent annulus communication check across both phases
    for d in (bleed, build):
        adj = d["Adjacent_Annulus_psig"].dropna()
        if len(adj) >= 2 and (adj.max() - adj.min()) > tol + 1e-6:
            result["communication_adjacent"] = True
        tub = d["Tubing_Pressure_psig"].dropna()
        if len(tub) >= 2 and (tub.max() - tub.min()) > tol + 1e-6:
            # Only flag if annulus & tubing move together; a light heuristic
            if annulus_id == "A":
                result["communication_tubing"] = True

    if not result["bled_to_zero"] and result["bleed_min_pressure"] is not None:
        # §10.2.2.3 — Pressure Does Not Bleed Down
        result["diagnosis_code"] = "NO_BLEED"
        result["diagnosis_title"] = "Pressure Does Not Bleed Down (§10.2.2.3)"
        result["diagnosis_detail"] = (
            f"The annulus pressure did not reach 0 psig (within tolerance ±{tol:g} psig); "
            f"minimum observed pressure was {result['bleed_min_pressure']:.0f} psig "
            f"within the recorded bleed-down window. This indicates the leak rate may "
            f"exceed the bleed rate, and the pressure barrier may not be effective."
        )
        if annulus_id == "A":
            result["recommendation"] = (
                "Conduct further investigation to determine the communication path and "
                "leak source; develop repair plans as needed (§10.2.2.3)."
            )
        else:
            result["recommendation"] = (
                "Recognize that correction options on outer annuli are very limited. "
                "Evaluate magnitude of consequences and probability of complete barrier "
                "failure to determine whether repairs or other future actions are needed. "
                "Do not attempt additional bleeds until this well is further evaluated (§10.2.2.3)."
            )
        return result

    # Bled to zero (or near-zero)
    if build.empty:
        result["diagnosis_code"] = "NO_BUILDUP_DATA"
        result["diagnosis_title"] = "Bled to zero — awaiting build-up data"
        result["diagnosis_detail"] = "Pressure was successfully bled to 0 psig, but no build-up monitoring data has been entered yet."
        result["recommendation"] = "Monitor and document build-up for up to 24 consecutive hours (or until stabilized) per §10.2.1(l)."
        return result

    final_row = build.loc[build["Hour"].idxmax()]
    result["buildup_final_pressure"] = float(final_row["Annulus_Pressure_psig"])
    build_within_time = build["Hour"].max() <= 24
    result["buildup_occurred"] = result["buildup_final_pressure"] > tol

    if not result["buildup_occurred"]:
        # §10.2.2.1 — Pressure Bleeds Down without Build-up
        result["diagnosis_code"] = "THERMAL_OR_NEGLIGIBLE"
        result["diagnosis_title"] = "Pressure Bleeds Down without Build-up (§10.2.2.1)"
        result["diagnosis_detail"] = (
            f"The annulus bled to 0 psig and did not build up within "
            f"{build['Hour'].max():.0f} hour(s) (max. 24 h expected). The source of "
            f"pressure is either thermal in origin or results from a leak with a very "
            f"low rate; the pressure containment barriers can be considered effective."
        )
        result["recommendation"] = "Continue routine monitoring per the operator's ACP management plan (§9)."
    else:
        # §10.2.2.2 — Pressure Bleeds Down with Build-up
        result["diagnosis_code"] = "BUILDUP_LEAK"
        if pre_pressure and result["buildup_final_pressure"] >= 0.95 * pre_pressure:
            result["leak_size"] = "large"
            leak_note = (
                "The pressure returned to (or exceeded) the original pre-bleed pressure "
                "within the monitored window, indicating a **probably large** leak rate."
            )
        else:
            result["leak_size"] = "small"
            leak_note = (
                "The pressure built up to a level **lower** than the original pre-bleed "
                "pressure, indicating a **probably small** leak rate."
            )
        result["diagnosis_title"] = "Pressure Bleeds Down with Build-up (§10.2.2.2)"
        result["diagnosis_detail"] = (
            f"The annulus bled to 0 psig and built back up to {result['buildup_final_pressure']:.0f} psig "
            f"(pre-test pressure was {pre_pressure:.0f} psig) within {build['Hour'].max():.0f} hour(s). "
            f"{leak_note} Because the pressure can be bled to zero, the leak rate is "
            f"generally considered acceptable and the pressure containment barrier(s) may "
            f"be considered adequate."
        )
        result["recommendation"] = (
            "Monitor for changing conditions and re-evaluate this annulus periodically "
            "to confirm the pressure containment barriers remain acceptable (§10.2.2.2). "
            "Note: a build-up higher than the original pressure is possible if the well "
            "was not stabilized at test start, or if hydrostatic pressure was reduced by "
            "replacing denser annular fluid with lighter formation fluid."
        )

    if not bleed_within_time:
        result["diagnosis_detail"] += (
            f" (Note: recorded bleed-down duration exceeds the configured maximum of "
            f"{max_h:.0f} h — confirm this against §10.2.1(k).)"
        )

    return result


# --------------------------------------------------------------------------------------
# Tab 3 — Analysis
# --------------------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Automated Diagnostic Analysis")
    st.caption("Implements the qualitative decision logic of API RP 90-2 §10.2.2 (Analysis of the Bleed-down/Build-up Test).")

    analysis = analyze_bleed_buildup(
        st.session_state.bleed_df, st.session_state.buildup_df, pre_test_pressure, zero_tolerance, max_hours
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Min. pressure during bleed-down", f"{analysis['bleed_min_pressure']:.0f} psig" if analysis["bleed_min_pressure"] is not None else "—")
    c2.metric("Bled to ~0 psig?", "Yes" if analysis["bled_to_zero"] else ("No" if analysis["bled_to_zero"] is not None else "—"))
    c3.metric("Final build-up pressure", f"{analysis['buildup_final_pressure']:.0f} psig" if analysis["buildup_final_pressure"] is not None else "—")

    st.markdown("---")

    if analysis["diagnosis_code"] == "NO_BLEED":
        st.error(f"**{analysis['diagnosis_title']}**")
    elif analysis["diagnosis_code"] == "THERMAL_OR_NEGLIGIBLE":
        st.success(f"**{analysis['diagnosis_title']}**")
    elif analysis["diagnosis_code"] == "BUILDUP_LEAK":
        if analysis["leak_size"] == "large":
            st.warning(f"**{analysis['diagnosis_title']}**")
        else:
            st.info(f"**{analysis['diagnosis_title']}**")
    else:
        st.info(f"**{analysis['diagnosis_title']}**")

    if analysis["diagnosis_detail"]:
        st.write(analysis["diagnosis_detail"])
    if analysis["recommendation"]:
        st.markdown(f"**Recommended action:** {analysis['recommendation']}")

    if analysis["communication_adjacent"]:
        st.warning(
            "🔎 **Possible communication with an adjacent annulus detected** — the "
            "adjacent-annulus pressure changed materially during the test. Per §10.2.2.4, "
            "if the 'A' annulus is in communication with the 'B' annulus, the production "
            "casing should no longer be considered an effective barrier for reservoir "
            "pressure; evaluate further on a case-by-case basis."
        )
    if analysis["communication_tubing"]:
        st.warning(
            "🔎 **Possible tubing-to-'A'-annulus communication detected** — tubing "
            "pressure varied in a manner correlated with the annulus test. Per §10.2.2.4, "
            "if the leak rate is acceptable (bleeds to 0 psig) barriers may still be "
            "considered acceptable; otherwise further evaluation is warranted."
        )

    st.markdown("---")
    st.markdown("##### Reasons pressure may not fully rebuild within 24 h (§10.2.2.2, informational)")
    st.markdown(
        "- The leak rate is very small\n"
        "- A large gas cap exists at the top of the annulus\n"
        "- A portion of the original pressure was caused by thermal effects\n"
        "- The initial build-up has a full fluid column; higher pressure may develop "
        "later as gas bubbles migrate upward"
    )

# --------------------------------------------------------------------------------------
# Tab 4 — Thermal screening test (§10.3)
# --------------------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Thermally Induced Casing Pressure — Screening Test (§10.3)")
    st.markdown(
        "Use this tab if the observed pressure is believed to be **thermally induced** "
        "rather than SCP. Choose the method used and log the resulting behavior; the "
        "tool applies the qualitative logic of §10.3.2."
    )

    thermal_method = st.selectbox(
        "Test method used (§10.3.1)",
        [
            "a) Bleed 10–20% at constant rate, monitor 24 h for stability",
            "b) Increase pressure 10–20% at constant rate, monitor 24 h for stability",
            "c) Change production/injection rate, monitor correlation",
            "d) Compare annulus pressure to flowing/shut-in tubing pressure",
            "e) Shut in the well, monitor for pressure fall to ~0 psig",
        ],
    )

    st.markdown("**Pressure log for the selected method** (Hour = 0 at start of test action):")
    st.session_state.thermal_df = st.data_editor(
        st.session_state.thermal_df,
        num_rows="dynamic",
        use_container_width=True,
        key="thermal_editor",
        column_config={
            "Hour": st.column_config.NumberColumn("Hour", step=1),
            "Annulus_Pressure_psig": st.column_config.NumberColumn(f"'{annulus_id}' Annulus (psig)", step=1.0),
            "Adjacent_Annulus_psig": st.column_config.NumberColumn("Adjacent Annulus (psig)", step=1.0),
            "Tubing_Pressure_psig": st.column_config.NumberColumn("Tubing Pressure (psig)", step=1.0),
        },
    )

    tdf = clean_series(st.session_state.thermal_df)
    if not tdf.empty:
        p0 = float(tdf.iloc[0]["Annulus_Pressure_psig"])
        pf = float(tdf.iloc[-1]["Annulus_Pressure_psig"])
        stable = (tdf["Annulus_Pressure_psig"].max() - tdf["Annulus_Pressure_psig"].min()) <= max(2.0, 0.03 * max(p0, 1))

        st.markdown("---")
        if thermal_method.startswith("e)"):
            if pf <= zero_tolerance:
                st.success(
                    "**Thermally induced casing pressure indicated, not SCP** (§10.3.2.1) — "
                    "pressure fell to ~0 psig without bleeding during shut-in."
                )
                if pf < p0 and p0 > zero_tolerance:
                    pass
            else:
                st.warning(
                    "Pressure **stabilized above 0 psig** during shut-in — this indicates the "
                    "annular pressure is **not solely** associated with thermal effects; "
                    "additional SCP diagnostic testing (bleed-down/build-up) is warranted "
                    "(§10.3.2.1)."
                )
        elif thermal_method.startswith("a)") or thermal_method.startswith("b)"):
            if stable:
                st.success(
                    "Pressure **remained stable** for the monitored period at constant rate — "
                    "consistent with thermally induced pressure, not SCP."
                )
            else:
                direction = "increased" if pf > p0 else "decreased"
                st.warning(
                    f"Pressure **{direction}** rather than remaining stable — SCP diagnostic "
                    f"testing is warranted (§10.3.1 a/b)."
                )
        elif thermal_method.startswith("c)"):
            st.info(
                "Review whether the annular pressure change is **directly and promptly** "
                "related to the rate change (thermal) versus a **delayed / partial** "
                "movement toward the pre-change pressure (indicates communication; slow "
                "movement suggests a small leak, rapid full return suggests a large leak) "
                "— §10.3.2.2."
            )
        elif thermal_method.startswith("d)"):
            st.info(
                "If the 'A' annulus pressure is **significantly different** from both the "
                "flowing and shut-in tubing pressure, tubing-to-annulus communication is "
                "**unlikely** (§10.3.1 d). If it tracks tubing pressure changes (e.g., "
                "rises when rate is reduced and tubing pressure rises), communication "
                "between tubing and the 'A' annulus is indicated."
            )
    else:
        st.caption("Enter pressure readings above to see the qualitative assessment.")

# --------------------------------------------------------------------------------------
# Tab 5 — Chart
# --------------------------------------------------------------------------------------
with tabs[4]:
    st.subheader("Pressure–Time History")

    bleed = clean_series(st.session_state.bleed_df)
    build = clean_series(st.session_state.buildup_df)

    fig = go.Figure()

    if not bleed.empty:
        fig.add_trace(
            go.Scatter(
                x=bleed["Hour"],
                y=bleed["Annulus_Pressure_psig"],
                mode="lines+markers",
                name=f"Bleed-down — '{annulus_id}' Annulus",
                line=dict(color="#d62728"),
            )
        )
    if not build.empty:
        # offset build-up hours to continue after the bleed-down window for a single timeline
        offset = bleed["Hour"].max() if not bleed.empty else 0
        fig.add_trace(
            go.Scatter(
                x=build["Hour"] + offset,
                y=build["Annulus_Pressure_psig"],
                mode="lines+markers",
                name=f"Build-up — '{annulus_id}' Annulus",
                line=dict(color="#1f77b4"),
            )
        )
    if not bleed.empty and bleed["Adjacent_Annulus_psig"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=bleed["Hour"],
                y=bleed["Adjacent_Annulus_psig"],
                mode="lines+markers",
                name="Adjacent Annulus (bleed-down phase)",
                line=dict(color="#9467bd", dash="dot"),
            )
        )
    if not build.empty and build["Adjacent_Annulus_psig"].notna().any():
        offset = bleed["Hour"].max() if not bleed.empty else 0
        fig.add_trace(
            go.Scatter(
                x=build["Hour"] + offset,
                y=build["Adjacent_Annulus_psig"],
                mode="lines+markers",
                name="Adjacent Annulus (build-up phase)",
                line=dict(color="#9467bd", dash="dot"),
            )
        )

    # Reference lines
    fig.add_hline(y=mawop, line_dash="dash", line_color="black", annotation_text="MAWOP", annotation_position="top left")
    fig.add_hline(y=upper_dt, line_dash="dash", line_color="orange", annotation_text="Upper DT", annotation_position="top left")
    if lower_dt_enabled:
        fig.add_hline(y=lower_dt, line_dash="dash", line_color="blue", annotation_text="Lower DT", annotation_position="bottom left")
    fig.add_hline(y=0, line_color="gray")

    fig.update_layout(
        xaxis_title="Elapsed Time (hours) — bleed-down then build-up",
        yaxis_title="Pressure (psig)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=520,
        margin=dict(t=60),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "MAWOP and Diagnostic Threshold reference lines per API RP 90-2 §7/§8. "
        "The build-up phase is plotted immediately following the bleed-down phase on a single timeline."
    )

# --------------------------------------------------------------------------------------
# Tab 6 — Report / Export
# --------------------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Diagnostic Test Report")
    st.caption("Compiles the minimum documentation elements of API RP 90-2 §11.3.2.")

    analysis = analyze_bleed_buildup(
        st.session_state.bleed_df, st.session_state.buildup_df, pre_test_pressure, zero_tolerance, max_hours
    )

    report_lines = []
    report_lines.append("ANNULAR CASING PRESSURE — BLEED-DOWN / BUILD-UP TEST REPORT")
    report_lines.append("Reference: API RP 90-2 (First Edition, April 2016), Sections 8, 10, 11")
    report_lines.append("=" * 78)
    report_lines.append(f"Test date: {test_date}")
    report_lines.append(f"Person conducting test: {tester_name}")
    report_lines.append(f"Test procedure: {test_procedure_ref}")
    report_lines.append(f"Reason for test: {reason_for_test}")
    report_lines.append("")
    report_lines.append("-- Well / Facility --")
    report_lines.append(f"Facility identification: {facility_id}")
    report_lines.append(f"Well name: {well_name}")
    report_lines.append(f"Well API number: {well_api}")
    report_lines.append(f"Lease name: {lease_name}")
    report_lines.append(f"Well type: {well_type}")
    report_lines.append(f"Well status: {well_status}")
    report_lines.append("")
    report_lines.append("-- Annulus Under Evaluation --")
    report_lines.append(f"Annulus identification: {annulus_id}")
    report_lines.append(f"Suspected ACP type: {pressure_type_suspected}")
    report_lines.append(f"MAWOP: {mawop:.0f} psig")
    report_lines.append(f"Upper DT: {upper_dt:.0f} psig")
    if lower_dt_enabled:
        report_lines.append(f"Lower DT: {lower_dt:.0f} psig")
    if applied_pressure_present:
        report_lines.append(f"Applied pressure — reason: {applied_reason}; medium: {applied_rate}")
    report_lines.append("")
    report_lines.append("-- Pre-test Conditions --")
    report_lines.append(f"Pre-test annulus pressure: {pre_test_pressure:.0f} psig")
    report_lines.append(f"Flowing tubing pressure: {tubing_pressure_flowing:.0f} psig")
    report_lines.append(f"Last shut-in tubing pressure: {tubing_pressure_shutin:.0f} psig")
    report_lines.append(f"Production/injection rate: {production_rate}")
    report_lines.append("")
    report_lines.append("-- Bleed-down Phase --")
    report_lines.append(f"Start time: {bleed_start}   End time: {bleed_end}")
    report_lines.append(f"Fluid type recovered: {fluid_type}   Volume bled off: {fluid_volume:g} bbl")
    bleed_clean = clean_series(st.session_state.bleed_df)
    report_lines.append(bleed_clean.to_string(index=False) if not bleed_clean.empty else "(no data entered)")
    report_lines.append("")
    report_lines.append("-- Build-up Phase --")
    if fluids_replaced:
        report_lines.append(f"Fluids replaced: Yes — {replacement_notes}")
    else:
        report_lines.append("Fluids replaced: No")
    build_clean = clean_series(st.session_state.buildup_df)
    report_lines.append(build_clean.to_string(index=False) if not build_clean.empty else "(no data entered)")
    report_lines.append("")
    report_lines.append("-- Analysis (API RP 90-2 §10.2.2) --")
    report_lines.append(f"Minimum pressure during bleed-down: {analysis['bleed_min_pressure']}")
    report_lines.append(f"Bled to ~0 psig: {analysis['bled_to_zero']}")
    report_lines.append(f"Final build-up pressure: {analysis['buildup_final_pressure']}")
    report_lines.append(f"Diagnosis: {analysis['diagnosis_title']}")
    report_lines.append(f"Detail: {analysis['diagnosis_detail']}")
    report_lines.append(f"Recommendation: {analysis['recommendation']}")
    if analysis["communication_adjacent"]:
        report_lines.append("Flag: possible communication with adjacent annulus detected.")
    if analysis["communication_tubing"]:
        report_lines.append("Flag: possible tubing-to-annulus communication detected.")
    report_lines.append("")
    report_lines.append("=" * 78)
    report_lines.append(
        "This report was generated to assist documentation under API RP 90-2 §11.3 and "
        "does not substitute for engineering judgment, applicable regulations, or review "
        "by a qualified person."
    )

    report_text = "\n".join(str(x) for x in report_lines)
    st.text_area("Report preview", report_text, height=420)

    colA, colB = st.columns(2)
    with colA:
        st.download_button(
            "⬇️ Download report (.txt)",
            data=report_text.encode("utf-8"),
            file_name=f"ACP_test_report_{well_name or 'well'}_{annulus_id}_{test_date}.txt",
            mime="text/plain",
        )
    with colB:
        combined = pd.concat(
            [
                clean_series(st.session_state.bleed_df).assign(Phase="Bleed-down"),
                clean_series(st.session_state.buildup_df).assign(Phase="Build-up"),
            ],
            ignore_index=True,
        )
        csv_buf = io.StringIO()
        combined.to_csv(csv_buf, index=False)
        st.download_button(
            "⬇️ Download raw data (.csv)",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name=f"ACP_test_data_{well_name or 'well'}_{annulus_id}_{test_date}.csv",
            mime="text/csv",
        )
