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
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

#: What a card prints where there is no figure to print.
UNREAD_DASH = "—"

#: What a card prints for a hand-typed ticket's stamp.
STAMP_TEXT = "not measured"


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

    def pct(self, *, dash: str = UNREAD_DASH, stamp: str = STAMP_TEXT) -> str:
        """The card's own text: a percent, or why there is none.

        A stamp and an unreadable field get DIFFERENT words, because they are
        different facts: one is a figure nobody measured and the other is a
        field nobody could read.
        """
        if self.basis == "stamp":
            return stamp
        if self.value is None:
            return dash
        return f"{self.value * 100:.0f}%"


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
