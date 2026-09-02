"""
Check the LinkedIn kit before posting.

The post body sits exactly on LinkedIn's 3,000 character limit, so any edit has
to remove as much as it adds. This recounts it, and checks the figures quoted
in the prose still match what the run data produces, because the slides
regenerate from the data and the prose does not.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "docs" / "social" / "LINKEDIN.md"
LIMIT = 3000


def blocks(md: str) -> list[str]:
    return [b.strip() for b in re.findall(r"```\n(.*?)```", md, re.S)]


def main() -> int:
    md = KIT.read_text(encoding="utf-8")
    bs = blocks(md)
    if not bs:
        print("no fenced blocks found in the kit", file=sys.stderr)
        return 2

    failed = False
    names = ["post body", "first comment", "short version"]
    for name, b in zip(names, bs, strict=False):
        n = len(b)
        over = n - LIMIT
        status = "OK" if n <= LIMIT else f"OVER by {over}"
        if n > LIMIT:
            failed = True
        print(f"  {name:<16}{n:>6} chars   {status}")

    # The runs came from a domain with no sanctions data. Saying "sanctions
    # screen" against this evidence is falsifiable by anyone who opens the
    # repo, so it is barred outright rather than left to judgement -- and it
    # is barred EVERYWHERE the project publishes, not only in the post: the
    # live landing page once carried it while this guard only read the kit.
    published = [("the kit", md),
                 ("docs/index.html", (ROOT / "docs" / "index.html")
                  .read_text(encoding="utf-8")),
                 ("README.md", (ROOT / "README.md").read_text(encoding="utf-8"))]
    for where, text in published:
        for phrase in ("sanctions screen", "sanctioned party"):
            if phrase in text.lower():
                print(f"\n  BARRED: {phrase!r} in {where} claims evidence "
                      "this study does not have", file=sys.stderr)
                failed = True

    if "—" in md:
        print("\n  em-dash found in the kit", file=sys.stderr)
        failed = True

    # the prose quotes figures the slides compute. If the data moves, the
    # slides follow and these do not, so they are checked rather than trusted.
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from make_social import (  # noqa: E402
        ALL_RUNS,
        BOUND1,
        BOUND30,
        CMP_A,
        CMP_B,
        N99,
        P2P03,
        PE_A,
        PE_B,
        RE_B,
        REACT_OUT,
        SKIP_N,
        SKIP_RUNS,
        TOTAL,
    )

    from plumbline.domains.ap.arms import POLICY_PROSE  # noqa: E402

    # the post quotes the policy verbatim. if the prompt is ever
    # reworded the quote silently becomes a misquote, so check it
    # against the string the agent is actually handed.
    POLICY_QUOTE = ('A control you skip because you predicted its '
                    'result is a control that did not run')
    def norm(t):
        return " ".join(t.split())

    assert norm(POLICY_QUOTE) in norm(POLICY_PROSE), \
        "the quoted policy clause is not in POLICY_PROSE"

    body = bs[0]

    # Figures the post cites and must match. Kept short deliberately: a check
    # for something the post no longer says fails for the wrong reason.
    checks = [
        (f"{SKIP_N} of {SKIP_RUNS} runs", "the skip rate on the duplicate"),
        (f"{REACT_OUT.pct:.1f}%", "agent outcome accuracy"),
        (f"{P2P03.assessment.deviation_rate * 100:.1f}%",
         "the base rate across all runs, not just the worst one"),
        (f"{N99} consecutive clean runs", "runs needed for 99% reliability"),
        (f"{ALL_RUNS:,} live runs", "live runs committed"),
    ]
    print()
    for text, what in checks:
        if text in body:
            print(f"  cites, verified   {text[:44]:<46}{what}")
        else:
            print(f"  MISSING           {text[:44]:<46}{what}",
                  file=sys.stderr)
            failed = True

    # Any number the post asserts that is not one we just verified is a
    # number nobody is checking. Surface it rather than trust it.
    known = {str(SKIP_N), str(SKIP_RUNS), str(N99), f"{PE_A.pct:.1f}",
             f"{REACT_OUT.pct:.1f}", f"{BOUND1:.0f}", f"{BOUND30:.1f}",
             f"{ALL_RUNS:,}", f"{TOTAL:,}", f"{P2P03.assessment.tested}",
             f"{P2P03.assessment.deviation_rate * 100:.1f}", "17",
             f"{PE_B.pct:.1f}", f"{RE_B.pct:.1f}", f"{TOTAL:,}",
             f"{CMP_B.p_value:.4f}", f"{CMP_A.p_value:.2f}",
             "2026", "1998", "2315", "2201", "30", "99", "4.5", "1", "2", "3"}
    found = set(re.findall(r"\d[\d,]*(?:\.\d+)?", body))
    unchecked = sorted(found - known)
    if unchecked:
        print("\n  unverified figures in the body: " + ", ".join(unchecked))
        print("  confirm each against the run data before posting.")

    print("\n  " + ("something is off, see above" if failed
                    else "kit is consistent with the run data"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
