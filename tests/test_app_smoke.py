# Every page of the app renders without an exception (headless, offline).

import pytest
from streamlit.testing.v1 import AppTest

PAGES = ["app_pages/stress_lab.py", "app_pages/overview.py", "app_pages/reserve_quality.py",
         "app_pages/treasury.py", "app_pages/validation.py", "app_pages/methodology.py"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]


def test_every_preset_renders_in_the_stress_lab():
    from pegguard.presets import preset_ids
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    for pid in preset_ids():
        at.session_state["pending_preset"] = pid
        at.run()
        assert not at.exception, (pid, [e.value for e in at.exception])


def test_share_link_loads_a_scenario():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.query_params["coin"] = "svb_style"
    at.query_params["sc_rate"] = "350.0"
    at.query_params["sc_closed"] = "2"
    at.run()
    assert not at.exception
    assert at.session_state["preset_id"] == "svb_style"
    assert at.session_state["sc_rate"] == 350.0 and at.session_state["sc_closed"] == 2
