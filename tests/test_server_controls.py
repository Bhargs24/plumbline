"""The control attestation as it reaches a reader who never opens a terminal.

The risk this file guards against is a console that shows a friendlier subset
of an audit document. That is worse than no console, because the reader
believes they have seen the workpaper.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from plumbline.core.trajectory import Step, Trajectory  # noqa: E402
from plumbline.store import Store  # noqa: E402


def _traj(trial, task, pert, names):
    return Trajectory(trial_id=trial, task_id=task, perturbation=pert,
                      variant_id=f"{pert}/0", arm="react", model="m",
                      steps=[Step("tool_call", n, {"invoice_id": task})
                             for n in names])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A store with one deficient control and one clean one."""
    from plumbline.server import app as app_module

    store = Store(tmp_path / "t.db")
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "claude-haiku-4-5")

    full = ["fetch_invoice", "match_purchase_order", "check_duplicate",
            "check_vendor_status", "schedule_payment"]
    trajs = [_traj(f"ok{i}", "INV-7001", "baseline", full) for i in range(12)]
    # under a tool fault the three-way match never runs
    trajs += [_traj(f"bad{i}", "INV-7001", "tool_fault",
                    ["fetch_invoice", "check_duplicate", "check_vendor_status"])
              for i in range(4)]
    store.save_trajectories(rid, trajs)

    monkeypatch.setattr(app_module, "STORE", store)
    c = TestClient(app_module.app)
    c.run_id = rid
    return c


def test_the_page_renders_and_is_linked_from_the_run(client):
    run = client.get(f"/runs/{client.run_id}")
    assert run.status_code == 200
    assert f"/runs/{client.run_id}/controls" in run.text, \
        "an unlinked page is an unfindable page"

    page = client.get(f"/runs/{client.run_id}/controls")
    assert page.status_code == 200


def test_the_page_carries_the_same_sections_as_the_workpaper(client):
    text = client.get(f"/runs/{client.run_id}/controls").text
    for section in ("Control operating effectiveness", "Reliance approach",
                    "Basis of preparation", "Exceptions", "Evidence hash"):
        assert section in text, f"{section} missing from the console"


def test_the_page_states_that_test_of_one_does_not_apply(client):
    text = client.get(f"/runs/{client.run_id}/controls").text
    assert "not deterministic" in text
    assert "One observation evidences one execution" in text


def test_exceptions_link_to_the_individual_trace(client):
    """A deviation rate with no reachable evidence cannot be investigated."""
    text = client.get(f"/runs/{client.run_id}/controls").text
    assert f"/runs/{client.run_id}/trace/bad0" in text


def test_conditions_the_population_was_tested_under_are_named(client):
    text = client.get(f"/runs/{client.run_id}/controls").text
    assert "baseline" in text and "tool_fault" in text
    assert "no further" in text, "the page must bound its own conclusions"


def test_api_and_page_are_computed_from_the_same_attestation(client):
    d = client.get(f"/api/runs/{client.run_id}/attestation").json()
    assert d["schema"] == "plumbline/attestation/v1"
    assert d["test_of_one_defensible"] is False
    # N executions over K scenarios is not a population of N, and the
    # workpaper has to report both or it overstates the evidence
    assert d["executions"] == 16
    assert d["distinct_scenarios"] == 1
    assert d["conditions_tested"] == {"baseline": 12, "tool_fault": 4}

    p2p01 = next(c for c in d["controls"] if c["control_id"] == "P2P.01")
    assert p2p01["effective"] is False
    assert p2p01["assessment"]["deviations"] == 4
    assert p2p01["deviations_by_perturbation"] == {"tool_fault": 4}

    # and the same numbers appear on the page
    assert d["evidence_hash"] in client.get(
        f"/runs/{client.run_id}/controls").text


def test_filtering_to_an_arm_with_no_data_is_a_404_not_an_empty_workpaper(client):
    r = client.get(f"/runs/{client.run_id}/controls?arm=nonexistent")
    assert r.status_code == 404, \
        "an empty population must not render as a clean attestation"


def test_unknown_run_is_a_404(client):
    assert client.get("/runs/run_nope/controls").status_code == 404


def test_a_mixed_population_is_qualified_on_the_page_with_a_way_out(client,
                                                                    tmp_path):
    """The console must not quietly pool two implementations into one rate,
    and having warned, it should offer the filter that fixes it."""
    from plumbline.server import app as app_module

    store = app_module.STORE
    rid = client.run_id
    others = [_traj(f"pe{i}", "INV-7001", "baseline",
                    ["fetch_invoice", "check_duplicate"]) for i in range(5)]
    for t in others:
        t.arm = "plan_execute"
    store.save_trajectories(rid, others)

    text = client.get(f"/runs/{rid}/controls").text
    assert "Qualified." in text
    assert "not attributable to any one of them" in text
    assert "?arm=plan_execute" in text and "?arm=react" in text

    # filtering to one implementation removes the qualification
    one = client.get(f"/runs/{rid}/controls?arm=react").text
    assert "Qualified." not in one
    assert "implementation: react" in one


def test_control_objectives_are_not_cut_mid_word(client):
    """A truncated objective reads as a broken template, which is the last
    impression an audit document should give."""
    text = client.get(f"/runs/{client.run_id}/controls").text
    assert "with another supplier<" not in text
    assert "…" in text, "long objectives clip on a word boundary"
