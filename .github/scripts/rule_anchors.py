"""Deriving and repairing the anchors that `rules/tunables.yaml` declares.

An anchor is the substring of an alert's expression that contains the threshold and occurs in that
expression exactly once. Both halves matter: `> 0.05` appears twice in
`TankoVaultHighServerErrorRatio` and means two unrelated things, so the bare comparison cannot
identify which number an override should move. Widening it until it is unique is what makes the
substitution addressable at all.

Shared by `add-tunable.py`, which derives an anchor when a tunable is first declared, and by
`audit-observability.py`, which suggests one when a rule edit has left an existing anchor
ambiguous or orphaned. One implementation, so the suggestion an operator is given is the same
string the tool would have written.
"""

from __future__ import annotations

import re


def literal_positions(expr: str, literal: str) -> list[int]:
    """Every index at which `literal` appears in `expr` as a number standing on its own.

    The lookarounds are what stop `1` matching inside `increase1h` or `3` inside `p95_15m`. Without
    them an anchor can be derived around a slice of a metric name, and the override then rewrites
    the series being queried rather than the threshold it is compared against.
    """
    pattern = re.compile(rf"(?<![0-9A-Za-z_.]){re.escape(literal)}(?![0-9A-Za-z_.])")
    return [match.start() for match in pattern.finditer(expr)]


def numeric_literals(expr: str) -> list[tuple[str, int]]:
    """Every number in an expression, as (literal, index), in the order they appear.

    The discovery half of `add-tunable`: given an alert and no idea which number is the threshold,
    this is the menu. Numbers inside identifiers are excluded for the same reason as above.
    """
    found = []
    for match in re.finditer(r"(?<![0-9A-Za-z_.])-?[0-9]+(?:\.[0-9]+)?(?![0-9A-Za-z_.])", expr):
        found.append((match.group(0), match.start()))
    return found


COMPARISON = re.compile(r"(>=|<=|==|!=|>|<)")


def _balanced(text: str) -> bool:
    """Whether a window closes every bracket and quote it opens.

    A window cutting through `{tankovault_scope=~".*", class="auth"}` is still a unique substring
    and still substitutes correctly, but it is unreadable in a review and breaks on any edit
    inside the selector. Requiring balance keeps an anchor to whole operands.
    """
    return (
        text.count("{") == text.count("}")
        and text.count("(") == text.count(")")
        and text.count('"') % 2 == 0
    )


def derive_anchor(expr: str, literal: str, position: int) -> str | None:
    """The narrowest *readable* unique window of `expr` around the literal at `position`.

    Uniqueness alone is a low bar and a bad anchor: `0.85` occurs once in
    `PaperlessNgxVolumeFillingUp` today, so it would qualify, and it would silently become
    ambiguous the moment anyone added a second one. So a window has to earn three more things
    before it is offered:

      - it spans a comparison operator, so the anchor names a threshold rather than a number;
      - it reaches back over the operand being compared, so it says *what* is being thresholded —
        `...:volume_used:ratio{...} > 0.85` rather than `> 0.85`;
      - it balances its brackets and quotes, so it never cuts a label selector in half.

    Expansion is leftward by whitespace-delimited token, and only widens rightward if the left
    edge reaches the start of the expression without becoming unique.

    Returns None when no window works — when the same expression compares the same operand against
    the same number twice. Such a threshold is not addressable and the rule needs rewriting before
    it can be made tunable.
    """
    end = position + len(literal)
    boundaries = [0] + [m.end() for m in re.finditer(r"\s+", expr)] + [len(expr)]
    starts = sorted({b for b in boundaries if b <= position}, reverse=True)
    ends = sorted({b for b in boundaries if b >= end})

    def candidates():
        for right in ends:
            for left in starts:
                window = expr[left:right].strip()
                if not window or not _balanced(window):
                    continue
                if len(literal_positions(window, literal)) != 1:
                    continue
                if expr.count(window) != 1:
                    continue
                match = COMPARISON.search(window)
                if not match:
                    continue
                yield window, bool(window[: match.start()].strip()), "\n" not in window

    # Four tiers, best first. The operand is what makes an anchor say *what* is thresholded, and
    # staying on one line is what keeps it pasteable into `tunables.yaml` — but the rules wrap
    # long expressions across lines, and there the operand and its comparison are simply not on
    # the same line. Rather than emit an anchor carrying a newline and the following line's
    # indentation, drop the operand and keep `> 0.5`, which is what these rules' authors chose by
    # hand for exactly the same reason.
    tiers: dict[tuple[bool, bool], str] = {}
    for window, has_operand, single_line in candidates():
        tiers.setdefault((has_operand, single_line), window)
    for key in ((True, True), (False, True), (True, False), (False, False)):
        if key in tiers:
            return tiers[key]
    return None


def suggest_anchor(expr: str, literal: str) -> str | None:
    """An anchor for `literal` in `expr`, when there is exactly one place it could mean.

    Used by the audit to turn "this anchor no longer matches" into something pasteable. Declines
    to guess when the literal appears more than once: choosing between two comparisons is the
    author's call, and a confident wrong suggestion is worse than none.
    """
    positions = literal_positions(expr, literal)
    if len(positions) != 1:
        return None
    return derive_anchor(expr, literal, positions[0])
