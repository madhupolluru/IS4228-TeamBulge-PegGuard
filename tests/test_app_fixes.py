# App fixes: share links, state kept across pages, Treasury defaults and the command-line script.

import subprocess
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_scenario.py"


def _app() -> AppTest:
    return AppTest.from_file("app.py", default_timeout=300)


def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT,
                          timeout=300)


def test_nan_in_share_link_is_ignored():
    at = _app()
    for k in ("sc_rate", "sc_run", "sc_stress"):
        at.query_params[k] = "nan"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sc_rate"] == at.session_state["sc_rate"]  # not NaN


def test_out_of_range_share_link_is_clamped():
    at = _app()
    at.query_params["sc_rate"] = "1000"
    at.query_params["sc_run"] = "-20"
    at.query_params["sc_wait"] = "4"  # not one of the deadline options
    at.query_params["sc_signal"] = "rumour"
    at.query_params["sc_days"] = "abc"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sc_rate"] == 500.0 and at.session_state["sc_run"] == 0.0
    assert at.session_state["sc_wait"] == 0 and at.session_state["sc_signal"] == "coverage"
    assert at.session_state["sc_days"] == 5


def test_share_link_round_trips_direct_access():
    at = _app()
    at.run()
    assert "direct" not in at.query_params  # default (on) is left out of the link
    at.toggle(key="direct_access").set_value(False).run()
    assert at.query_params["direct"] == ["0"]  # AppTest keeps each parameter as a list

    shared = _app()
    for k, v in at.query_params.items():
        shared.query_params[k] = v[0]
    shared.run()
    assert not shared.exception
    assert shared.session_state["direct_access"] is False


def test_settings_survive_a_page_switch():
    at = _app()
    at.run()
    at.select_slider(key="mc_n").set_value(1000).run()
    at.switch_page("app_pages/treasury.py").run()
    at.slider(key="tr_budget").set_value(100).run()
    at.select_slider(key="tr_days").set_value(5).run()
    at.toggle(key="tr_direct").set_value(False).run()
    at.switch_page("app_pages/stress_lab.py").run()
    assert at.select_slider(key="mc_n").value == 1000
    at.switch_page("app_pages/treasury.py").run()
    assert not at.exception
    assert at.slider(key="tr_budget").value == 100
    assert at.select_slider(key="tr_days").value == 5
    assert at.toggle(key="tr_direct").value is False


def test_treasury_default_holdings():
    at = _app()
    at.run()
    at.switch_page("app_pages/treasury.py").run()
    assert not at.exception
    mix = at.session_state["treasury_mix"]
    assert mix["Holding ($m)"].tolist() == [60.0, 30.0, 5.0]
    assert next(m for m in at.metric if m.label == "Treasury").value == "$95.0m"


def _cli_value(out: str, label: str) -> str:
    line = next(l for l in out.splitlines() if l.strip().startswith(label))
    return line.split()[len(label.split())]


def test_cli_full_panic_matches_the_app():
    from pegguard.analysis import STANDARD_SCENARIOS
    from pegguard.engine import run_stress
    from pegguard.presets import load_preset
    from app_pages.common import SC_FIELDS, fmt_bn

    r = _cli("--preset", "usdt_2026q2", "--scenario", "Full panic")
    assert r.returncode == 0, r.stderr
    panic = next(sc for sc in STANDARD_SCENARIOS if sc.name.startswith("Full panic"))
    p = load_preset("usdt_2026q2")
    s = run_stress(p.lines, p.supply_bn, panic).summary
    assert _cli_value(r.stdout, "Value per token") == f"${s['min_token_value']:.4f}"
    assert _cli_value(r.stdout, "Paid late") == fmt_bn(s["late_amount"])

    # the Stress Lab with the same crisis loaded (same-day deadline) shows the same numbers
    at = _app()
    at.run()
    for k, f in SC_FIELDS.items():
        if k != "sc_wait":
            at.session_state[k] = getattr(panic, f)
    at.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Paid late"] == _cli_value(r.stdout, "Paid late")
    assert metrics["Lowest coverage"] == f"{s['min_coverage']:.2%}"


def test_cli_negative_rate_shock_is_signed():
    r = _cli("--preset", "narrow", "--rate-shock", "-100")
    assert r.returncode == 0, r.stderr
    assert "-100bp" in r.stdout and "+-" not in r.stdout


def test_cli_rejects_unknown_preset_and_scenario():
    r = _cli("--preset", "not_a_coin")
    assert r.returncode == 2 and "invalid choice" in r.stderr and "Traceback" not in r.stderr
    r = _cli("--scenario", "Meteor strike")
    assert r.returncode == 2 and "Full panic" in r.stderr and "Traceback" not in r.stderr
