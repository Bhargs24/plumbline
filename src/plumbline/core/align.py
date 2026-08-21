"""
Sequence alignment for trajectories.

Comparing two paths position-by-position gets the diagnosis wrong in the most
common real case. If the reference is

    check_invoice -> match_po -> detect_duplicate -> approve

and a run omits `detect_duplicate`, positional comparison reports "at step 2,
expected detect_duplicate, got approve", which reads as though the agent
substituted one check for another. It did not. It SKIPPED a control. The
distinction matters: a skipped control is a policy violation, a substituted one
is a routing error, and they get fixed differently.

Needleman-Wunsch global alignment recovers the actual edit script, so a skip is
reported as a skip, an extra call as an insertion, and a genuine swap as a
substitution. Scores are chosen so that a single omission aligns as one gap
rather than cascading every downstream step into a mismatch.
"""
from __future__ import annotations

from dataclasses import dataclass

MATCH = "match"
SUBSTITUTE = "substitute"
SKIPPED = "skipped"     # in reference, absent from candidate
EXTRA = "extra"         # in candidate, absent from reference

_MATCH_SCORE = 2
_MISMATCH_SCORE = -1
_GAP_SCORE = -2


@dataclass
class EditOp:
    op: str
    ref_index: int | None      # position in the reference path
    cand_index: int | None     # position in the candidate path
    ref_item: object | None
    cand_item: object | None

    @property
    def is_divergence(self) -> bool:
        return self.op != MATCH

    def describe(self) -> str:
        ri = "-" if self.ref_index is None else self.ref_index
        if self.op == MATCH:
            return f"step {ri}: {_lbl(self.ref_item)} ok"
        if self.op == SKIPPED:
            return f"step {ri}: {_lbl(self.ref_item)} SKIPPED"
        if self.op == EXTRA:
            return f"after step {ri if ri != '-' else 0}: extra {_lbl(self.cand_item)}"
        return (f"step {ri}: expected {_lbl(self.ref_item)}, "
                f"got {_lbl(self.cand_item)}")


def _lbl(item) -> str:
    if item is None:
        return "(none)"
    if isinstance(item, tuple) and len(item) == 2:
        return f"{item[0]}:{item[1]}"
    return str(item)


def align(candidate: tuple, reference: tuple) -> list[EditOp]:
    """Global alignment of a candidate path against the reference path.

    Returns the edit script in reference order. An empty reference or candidate
    is handled as all-insertions or all-deletions respectively.
    """
    n, m = len(reference), len(candidate)
    # score[i][j] = best score aligning reference[:i] with candidate[:j]
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * _GAP_SCORE
    for j in range(1, m + 1):
        score[0][j] = j * _GAP_SCORE
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            hit = _MATCH_SCORE if reference[i - 1] == candidate[j - 1] else _MISMATCH_SCORE
            score[i][j] = max(
                score[i - 1][j - 1] + hit,   # align the pair
                score[i - 1][j] + _GAP_SCORE,  # reference item unmatched: skipped
                score[i][j - 1] + _GAP_SCORE,  # candidate item unmatched: extra
            )

    ops: list[EditOp] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            hit = _MATCH_SCORE if reference[i - 1] == candidate[j - 1] else _MISMATCH_SCORE
            if score[i][j] == score[i - 1][j - 1] + hit:
                op = MATCH if reference[i - 1] == candidate[j - 1] else SUBSTITUTE
                ops.append(EditOp(op, i - 1, j - 1, reference[i - 1], candidate[j - 1]))
                i, j = i - 1, j - 1
                continue
        if i > 0 and score[i][j] == score[i - 1][j] + _GAP_SCORE:
            ops.append(EditOp(SKIPPED, i - 1, None, reference[i - 1], None))
            i -= 1
            continue
        ops.append(EditOp(EXTRA, i - 1 if i > 0 else None, j - 1, None, candidate[j - 1]))
        j -= 1
    ops.reverse()
    return ops


def first_divergence(candidate: tuple, reference: tuple) -> EditOp | None:
    for op in align(candidate, reference):
        if op.is_divergence:
            return op
    return None


def divergences(candidate: tuple, reference: tuple) -> list[EditOp]:
    return [op for op in align(candidate, reference) if op.is_divergence]
