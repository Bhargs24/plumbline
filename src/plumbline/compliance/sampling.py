"""
Attribute sampling, in the form a control tester uses it.

The measurement layer reports conformance as a proportion with a Wilson
interval. An auditor does not think in those terms. They think in **deviation
rates** against a **tolerable rate**, at a sample size chosen for a stated
confidence level, and they need to know whether the sample was large enough to
support the conclusion before they read the conclusion.

This module restates the same evidence in that vocabulary. It is arithmetic,
not interpretation: it says what a sample of this size can and cannot support.
Whether the control is effective remains a judgement its owner and their
auditor make.

WHY THE SAMPLE SIZES ARE WHAT THEY ARE

PCAOB standards do not codify sample sizes; AS 2315 requires the auditor to
consider the tolerable deviation rate, the expected deviation rate and the
desired confidence. Applying the standard attribute-sampling formula for zero
expected deviations gives the numbers practitioners recognise:

    n  >=  ln(1 - confidence) / ln(1 - tolerable_rate)

At 95% confidence and a 5% tolerable rate that is 59. At 90% it is 45. Those
are the figures in every sampling table, and this derives rather than hardcodes
them so a different risk appetite produces a defensible number rather than a
number somebody remembered.

THE PART THAT MATTERS FOR AI-OPERATED CONTROLS

For a conventional automated control, PCAOB staff guidance permits a single
test to support reliance for the period, because the automation is
deterministic. `assess_test_of_one()` returns False whenever the control is
operated by a non-deterministic system, and says why. That is the whole
compliance argument for this tool in one function.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), by direct summation.

    n here is a sample size in the hundreds at most, so summation is exact and
    fast enough; no dependency is worth adding for it.
    """
    if p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    total, term = 0.0, (1 - p) ** n
    for i in range(0, k + 1):
        if i:
            term *= (n - i + 1) / i * (p / (1 - p))
        total += term
    return min(1.0, total)


def clopper_pearson_upper(deviations: int, n: int,
                          confidence: float = 0.95) -> float:
    """Exact one-sided upper confidence bound on a deviation rate.

    This is the bound audit sampling uses, and using it here is not a detail.
    The required-sample formula is derived from the same distribution, so the
    two agree by construction: a clean sample of exactly the required size
    lands exactly on the tolerable rate. A Wilson bound, which is what the
    measurement layer reports, is a different statistic and would make a sample
    of the prescribed size appear to fail the test it was sized for.
    """
    if n <= 0:
        return 1.0
    if deviations >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):                      # bisection to ~1e-24
        mid = (lo + hi) / 2
        if _binom_cdf(deviations, n, mid) > 1 - confidence:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

#: Zero-expected-deviation sample sizes, derived rather than tabulated.
CONFIDENCE_95 = 0.95
CONFIDENCE_90 = 0.90


def required_sample_size(tolerable_rate: float, confidence: float = CONFIDENCE_95,
                         expected_deviations: int = 0) -> int:
    """Sample size for an attribute test.

    The closed form holds for zero expected deviations. Where deviations are
    expected, the sample must grow; the Poisson expansion below is the standard
    practitioner adjustment and is approximate, which is stated rather than
    hidden.
    """
    if not 0 < tolerable_rate < 1:
        # A 0% tolerable rate cannot be satisfied by sampling at all: no finite
        # sample proves zero deviations in a population. Auditors handle these
        # by testing the full population, which is exactly what this tool does.
        return 0
    n = math.log(1 - confidence) / math.log(1 - tolerable_rate)
    if expected_deviations:
        n *= 1 + expected_deviations * 0.6      # practitioner expansion
    return int(math.ceil(n))


@dataclass
class SampleAssessment:
    """Whether the evidence supports a conclusion, before anyone draws one."""
    control_id: str
    population: int
    tested: int
    deviations: int
    tolerable_rate: float
    confidence: float
    required: int
    full_population: bool

    @property
    def deviation_rate(self) -> float:
        return (self.deviations / self.tested) if self.tested else 0.0

    @property
    def upper_deviation_rate(self) -> float:
        """The upper bound on the true deviation rate.

        An auditor concludes against the UPPER bound, never the observed rate.
        Zero deviations in 20 items does not evidence a 0% deviation rate; it
        evidences a rate that could plausibly be as high as 14%.

        For a full-population test there is no inference to make: every item
        was examined, so the deviation rate is observed rather than estimated.
        """
        if self.full_population:
            return self.deviation_rate
        return clopper_pearson_upper(self.deviations, self.tested,
                                     self.confidence)

    @property
    def sufficient(self) -> bool:
        """Full-population testing always suffices. Otherwise the sample must
        reach the size the stated confidence and tolerable rate demand."""
        if self.full_population:
            return True
        return self.required > 0 and self.tested >= self.required

    @property
    def passes(self) -> bool:
        """Effective only if the sample supports a conclusion AND the upper
        bound sits within tolerance. Both halves are required: a clean result
        on an inadequate sample concludes nothing."""
        if not self.sufficient:
            return False
        if self.tolerable_rate == 0:
            return self.deviations == 0
        return self.upper_deviation_rate <= self.tolerable_rate

    def conclusion(self) -> str:
        if not self.sufficient:
            return (f"INCONCLUSIVE — {self.tested} items tested; "
                    f"{self.required} required at {self.confidence:.0%} "
                    f"confidence against a {self.tolerable_rate:.0%} tolerable "
                    f"rate. Extend the sample before concluding.")
        if self.tolerable_rate == 0 and self.deviations:
            return (f"DEFICIENCY — {self.deviations} deviation(s) in "
                    f"{self.tested} items on a zero-tolerance control. A single "
                    f"deviation here is a loss event, not a rate.")
        if not self.passes:
            return (f"DEFICIENCY — upper deviation rate "
                    f"{self.upper_deviation_rate:.1%} exceeds the tolerable "
                    f"{self.tolerable_rate:.0%}.")
        if self.full_population:
            return (f"EFFECTIVE — {self.deviations} deviation(s) across the "
                    f"full population of {self.population}. No inference "
                    f"required: every item was examined.")
        return (f"EFFECTIVE — {self.deviations} deviation(s) in {self.tested} "
                f"of {self.population}; upper deviation rate "
                f"{self.upper_deviation_rate:.1%} within the tolerable "
                f"{self.tolerable_rate:.0%} at {self.confidence:.0%} "
                f"confidence.")

    def to_dict(self) -> dict:
        return {"control_id": self.control_id, "population": self.population,
                "tested": self.tested, "deviations": self.deviations,
                "deviation_rate": round(self.deviation_rate, 4),
                "upper_deviation_rate": round(self.upper_deviation_rate, 4),
                "tolerable_rate": self.tolerable_rate,
                "confidence": self.confidence, "required_sample": self.required,
                "full_population": self.full_population,
                "sufficient": self.sufficient, "passes": self.passes,
                "conclusion": self.conclusion()}


def assess(control_id: str, *, tested: int, deviations: int,
           tolerable_rate: float, population: int | None = None,
           confidence: float = CONFIDENCE_95) -> SampleAssessment:
    population = population if population is not None else tested
    return SampleAssessment(
        control_id=control_id, population=population, tested=tested,
        deviations=deviations, tolerable_rate=tolerable_rate,
        confidence=confidence,
        required=required_sample_size(tolerable_rate, confidence),
        full_population=(tested >= population and population > 0))


# --------------------------------------------------------------------------
def assess_test_of_one(control_type: str, operator: str,
                           itgc_effective: bool) -> tuple[bool, str]:
    """Can a single test of this control support reliance for the period?

    This is the crux of the argument for measuring AI-operated controls.

    PCAOB staff guidance permits "test once, rely broadly" for automated
    controls when IT general controls are effective. The concession rests on
    determinism: conventional automation given the same input behaves the same
    way in January and in December, so one observation generalises to the
    population.

    An LLM-operated control breaks that premise. Sampling introduces run-to-run
    variation, behaviour is sensitive to input phrasing that a control owner
    would call immaterial, and the model itself is a moving dependency. One
    observation evidences one execution.

    Returns (defensible, reasoning) so a workpaper can quote the reasoning
    rather than the boolean.
    """
    if operator == "llm_agent":
        return False, (
            "Not defensible. Test-of-one for automated controls rests on the "
            "automation being deterministic, so that one observation "
            "generalises across the period. A control operated by a language "
            "model is not deterministic: output varies with sampling, with "
            "immaterial changes in input phrasing, and with model version. One "
            "observation evidences one execution. Operating effectiveness "
            "requires testing across the input variation the control will "
            "actually meet, which is what a perturbation suite provides.")
    if not itgc_effective:
        return False, (
            "Not defensible. Test-of-one is contingent on IT general controls "
            "operating effectively. Where ITGC testing has not passed, "
            "automated control reliance cannot be taken and substantive "
            "testing is required.")
    if control_type == "automated":
        return True, (
            "Defensible. A deterministic automated control with effective ITGC "
            "may be tested once and relied upon for the period, per PCAOB staff "
            "guidance.")
    return False, (
        f"Not defensible for a {control_type} control. Human judgement in the "
        f"control's operation requires sample-based testing across the period.")
