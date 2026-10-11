"""The confidence a card SHOWS, asked in one place.

An idea carries two confidences once Change 1 is on. ``blended_confidence_raw``
is the analyzer's own blend, the scale every floor in this repo is defined on
(``MIN_CONFIDENCE``, ``SCALP_MIN``, ``SIGNAL_DISPLAY_MIN_CONFIDENCE``,
``min_alert_conf``). ``confidence`` is what the calibration curve left on the
field, an estimated win rate on a different scale. They are two quantities, and
five surfaces printed them under one word with five hand-written fallbacks:

    proactive_monitor.py  float((raw if raw is not None else confidence) or 0.0)
    trading_commands.py   float(v) if v is not None else 0.0
    telegram_handler.py   raw if raw is not None else new_idea.confidence
    signal_card.py        idea.confidence            <- the calibrated one
    risk_engine.py        prints idea.confidence beside a RAW floor

THE SHARPEST INSTANCE IS ONE MESSAGE, NOT TWO CARDS. `_send_idea_with_door`
builds the PNG and its own caption in the same function, so a SUI signal whose
blend was 0.70 and whose calibrated confidence was 0.31 went out as a caption
reading ``Conf 70%`` over an image whose CONFIDENCE cell read ``31%``. Driven,
both numbers come off one `TradeIdea`, in one `send_photo`.

Two of those five fallbacks read a BOOL as a confidence -- ``float(True)`` is
1.0, which clears every floor and prints as 100% -- which is the defect
`pre_calibration_confidence` was written for, at the display end.

`displayed_confidence` is the one reading. It answers the blend when there is
one, because that is the figure the alert gate, the display threshold and the
entry floor all compare against; a card showing anything else is a number no
gate on the page used.

Four words, because four things are true of an idea's confidence and only two
of them are a measurement:

``blend``   the analyzer's own pre-calibration blend. Measured.
``own``     no blend, and the idea's own confidence reads as a number from a
            producer that measured one -- a scan row's score, a drift
            re-offer's inherited figure. Measured, on the raw scale.
``stamp``   no blend, and `quality_reading` says the figure is not a
            measurement: `build_manual_idea` writes ``confidence=1.0`` on
            every hand-typed ticket. A card that prints ``100%`` there tells
            the operator the bot is certain about an idea the operator typed.
``unread``  nothing on the idea is readable as a confidence.

Never raises: a malformed idea reads ``unread``.

A SCORE, NEVER A PERCENT. Every surface printed the reading as ``72%``, which
reads as a probability, and the figure is not one: the blend is snapshotted
BEFORE the calibration curve (``_blended_confidence_raw`` in the analyzer) and
an ``own`` figure never went through one, so no figure this reading answers has
been mapped to outcomes, whatever CONFIDENCE_CALIBRATION_ENABLED says. A
percent sign is kept for a figure a fitted curve produced, and this reading
never answers one, so `ConfidenceReading.text` prints ``score 72
(uncalibrated)`` and `SCORE_MEANING` is the sentence a model or a page carries
beside it. The calibration file's per-bin recorded wins are not printed beside
the score: no reading here answers "the recorded win rate of this score's bin",
and a second copy of the website chart's arithmetic would be a second answer.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

#: What a card prints where there is no figure to print.
UNREAD_DASH = "—"

#: What a card prints for a hand-typed ticket's stamp.
STAMP_TEXT = "not measured"

#: The two words a measured figure is printed with. The website's twin
#: (`app/public/js/confidence-score.js`) is pinned to these by a test.
SCORE_WORD = "score"
UNCALIBRATED_WORD = "uncalibrated"

#: What a confidence score is, for a reader that is handed one -- the chat
#: model above all, which was handed "confidence 72%" with nothing saying it
#: was not a probability.
SCORE_MEANING = (
    "A confidence score is the analyzer's own 0-100 rating of a setup. It is "
    "not a probability or a win rate: no fitted calibration curve has mapped "
    "it to outcomes, so never describe it as a chance or likelihood of "
    "winning.")


def score_number(value: float) -> str:
    """A raw-scale 0-1 figure as the 0-100 number a card prints, no sign."""
    return f"{value * 100:.0f}"


def score_text(value: Optional[float], *, dash: str = UNREAD_DASH) -> str:
    """``score 72 (uncalibrated)`` for a raw-scale figure, ``dash`` for None."""
    if value is None:
        return dash
    return f"{SCORE_WORD} {score_number(value)} ({UNCALIBRATED_WORD})"


class ConfidenceReading(NamedTuple):
    """What a card may say about this idea's confidence."""

    #: 0.0-1.0 on the RAW scale, or None for ``stamp`` and ``unread``.
    value: Optional[float]
    #: ``blend`` | ``own`` | ``stamp`` | ``unread``.
    basis: str

    @property
    def measured(self) -> bool:
        """True only where a figure was measured about THIS idea."""
        return self.basis in ("blend", "own")

    def clears(self, bar: float) -> bool:
        """True only when a MEASURED figure reaches ``bar`` (>=).

        Every gate that reads a confidence asks exactly this, and each of them
        wrote it as ``read.measured and read.value >= bar`` -- two reads of one
        reading, and a shape mypy cannot narrow, because ``measured`` is a
        property and the analyser has no way to connect it to ``value``. A cast
        would silence that; one method removes the second read instead.

        Unmeasured does not clear a bar. A bar is a FLOOR, and a floor cleared
        by a figure nobody measured is not a floor -- which is the ruling
        `_high_conviction_margin` already takes for a hand-typed ticket's stamp.
        """
        return self.value is not None and self.measured and self.value >= bar

    def above(self, bar: float) -> bool:
        """True only when a MEASURED figure exceeds ``bar`` (strict).

        The other direction, for a check whose finding is a CONCERN rather than
        a permission: the critique's overconfidence rule fires on what the model
        said, so a stamp nobody measured is not overconfidence and abstains.
        """
        return self.value is not None and self.measured and self.value > bar

    def text(self, *, dash: str = UNREAD_DASH, stamp: str = STAMP_TEXT) -> str:
        """The card's own text: a score, or why there is none.

        It was ``pct()`` and printed ``72%``, a probability's spelling on a
        figure no curve has mapped to outcomes (see the module docstring).

        A stamp and an unreadable field get DIFFERENT words, because they are
        different facts: one is a figure nobody measured and the other is a
        field nobody could read.
        """
        if self.basis == "stamp":
            return stamp
        return score_text(self.value, dash=dash)


def displayed_confidence(idea: Any) -> ConfidenceReading:
    """The confidence this idea's cards may show, and which quantity it is."""
    try:
        from bot.learning.confidence_calibration import pre_calibration_confidence

        blend = pre_calibration_confidence(idea)
    except Exception:
        blend = None
    if blend is not None:
        return ConfidenceReading(float(blend), "blend")

    try:
        from bot.risk.quality_ladder import confidence_on_record, quality_reading

        conf = confidence_on_record(getattr(idea, "confidence", None))
        if conf is None:
            return ConfidenceReading(None, "unread")
        if not quality_reading(idea).measured:
            return ConfidenceReading(None, "stamp")
        return ConfidenceReading(float(conf), "own")
    except Exception:
        return ConfidenceReading(None, "unread")
