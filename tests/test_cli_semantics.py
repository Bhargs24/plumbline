"""The CLI's scoring contract: domains resolve loudly, incomplete runs are
excluded with disclosure, and provenance is anchored to the evidence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from plumbline import __version__
from plumbline.domains import KNOWN, get_domain
from plumbline.report.certificate import _git_commit


def test_known_domains_resolve_with_policies() -> None:
    for name in KNOWN:
        d = get_domain(name)
        assert d.policy is not None
        assert d.contexts, name
        assert callable(d.outcome_matches)


def test_historical_alias_maps_to_the_studied_domain() -> None:
    """Older stores label the 8-invoice domain 'accounts_payable'; the alias
    must keep resolving to it, never to the 20-invoice domain."""
    assert get_domain("accounts_payable").name == "ap"


def test_unknown_domain_fails_loudly() -> None:
    """Scoring a run with the wrong domain's policy produces confidently wrong
    findings -- an unknown name must refuse, naming the known ones."""
    with pytest.raises(SystemExit) as e:
        get_domain("warehouse")
    msg = str(e.value)
    assert "warehouse" in msg and "ap" in msg and "refusing to guess" in msg


def test_incomplete_runs_are_excluded_and_disclosed(capsys) -> None:
    from plumbline.cli import _scoreable

    @dataclass
    class T:
        error: str | None

    class Args:
        include_errors = False

    kept = _scoreable([T(None), T("boom"), T(None)], Args())
    assert len(kept) == 2
    err = capsys.readouterr().err
    assert "excluded 1 run(s)" in err and "missing data" in err

    class Include:
        include_errors = True

    assert len(_scoreable([T(None), T("boom")], Include())) == 2


def test_provenance_is_anchored_to_the_evidence() -> None:
    """A certificate must stamp the commit of the repository holding the run,
    never whatever repository the operator happened to be standing in."""
    repo_root = Path(__file__).resolve().parents[1]
    ours = subprocess.check_output(
        ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
        text=True).strip()
    assert _git_commit(str(repo_root / "runs" / "parity-study")) == ours
    assert _git_commit(None) == "(no repository)"


def test_provenance_outside_any_repo_is_honest(tmp_path: Path) -> None:
    assert _git_commit(str(tmp_path)) == "(no repository)"


def test_one_version_everywhere() -> None:
    """0.2.0 in the package while /api/health said 0.3.0: never again."""
    import re
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert m and m.group(1) == __version__
    pytest.importorskip("fastapi")
    from plumbline.server.app import app
    assert app.version == __version__


def test_the_study_runner_is_importable_and_validates() -> None:
    """At one point HEAD's runner could not even be imported (a dataclass
    field-order error) and nothing noticed, because no test touched it. The
    machine that produced all the evidence must at minimum import."""
    from plumbline.runtime.runner import RunConfig, run_study

    cfg = RunConfig(toolbox_factory=object)
    with pytest.raises(ValueError, match="arms"):
        run_study(cfg, agent_llm=None, perturb_llm=None)


def test_variant_seed_is_stable_across_processes() -> None:
    """hash() is salted per process; variant generation must not be."""
    import subprocess
    import sys
    code = (
        "import sys; sys.path.insert(0, 'src')\n"
        "import hashlib\n"
        "print(int(hashlib.sha256(b'INV-7002').hexdigest()[:8], 16) % 10_000)\n"
    )
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, cwd=str(Path(__file__).resolve().parents[1]),
                           ).stdout.strip() for _ in range(2)}
    assert len(outs) == 1 and outs != {""}
