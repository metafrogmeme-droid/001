"""Post-Pectra exit churn. Confirmed from beacon ``/eth/v1/config/spec``.

Read 2026-10-23 from ethereum-beacon-api.publicnode.com. These are not
unverified defaults. An unread total active balance does not become the
128 ETH floor: the floor applies only once the balance has been read.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from bot.t2c.numbers import as_decimal, json_num

# gwei, as published.
MIN_PER_EPOCH_CHURN_LIMIT_ELECTRA = 128_000_000_000
MAX_PER_EPOCH_ACTIVATION_EXIT_CHURN_LIMIT = 256_000_000_000
CHURN_LIMIT_QUOTIENT = 65_536
MIN_VALIDATOR_WITHDRAWABILITY_DELAY = 256
MAX_WITHDRAWALS_PER_PAYLOAD = 16
MAX_VALIDATORS_PER_WITHDRAWALS_SWEEP = 16_384

GWEI_PER_ETH = Decimal(10) ** 9
EPOCH_SECONDS = Decimal(384)  # 32 slots * 12 seconds
EPOCHS_PER_DAY = Decimal(86400) / EPOCH_SECONDS  # 225
SECONDS_PER_DAY = Decimal(86400)


def confirmed_churn() -> dict:
    return {
        "status": "confirmed",
        "confirmed_as_of": "2026-10-23",
        "confirmed_from": "beacon /eth/v1/config/spec",
        "min_per_epoch_churn_limit_electra_gwei": str(MIN_PER_EPOCH_CHURN_LIMIT_ELECTRA),
        "max_per_epoch_activation_exit_churn_limit_gwei": str(
            MAX_PER_EPOCH_ACTIVATION_EXIT_CHURN_LIMIT),
        "churn_limit_quotient": str(CHURN_LIMIT_QUOTIENT),
        "min_validator_withdrawability_delay_epochs": str(
            MIN_VALIDATOR_WITHDRAWABILITY_DELAY),
        "max_withdrawals_per_payload": str(MAX_WITHDRAWALS_PER_PAYLOAD),
        "max_validators_per_withdrawals_sweep": str(
            MAX_VALIDATORS_PER_WITHDRAWALS_SWEEP),
        "min_eth_per_epoch": "128",
        "max_eth_per_epoch": "256",
    }


def churn_eth_per_epoch(total_active_gwei: object) -> Decimal | None:
    """min(256, max(128, floor(total / 65536))) ETH, floored to 1 ETH.

    ``None`` when the total active balance was not read.
    """
    total = as_decimal(total_active_gwei)
    if total is None or total < 0:
        return None
    raw = total // Decimal(CHURN_LIMIT_QUOTIENT)
    minimum = Decimal(MIN_PER_EPOCH_CHURN_LIMIT_ELECTRA)
    maximum = Decimal(MAX_PER_EPOCH_ACTIVATION_EXIT_CHURN_LIMIT)
    churn = raw if raw > minimum else minimum
    if churn > maximum:
        churn = maximum
    increment = GWEI_PER_ETH
    churn = churn - (churn % increment)
    return churn / increment


def queue_wait_days(queue_eth: object, churn_eth: object) -> Fraction | None:
    """Days at the given churn. A fraction, so a repeating wait is exact."""
    queue = _as_fraction(queue_eth)
    churn = _as_fraction(churn_eth)
    if queue is None or churn is None:
        return None
    if queue < 0 or churn <= 0:
        return None
    return queue / (churn * Fraction(225))


def withdrawability_days() -> Fraction:
    """256 epochs of 384 seconds, as a fraction of a day."""
    return Fraction(MIN_VALIDATOR_WITHDRAWABILITY_DELAY) * 384 / 86400


def _as_fraction(value: object) -> Fraction | None:
    number = as_decimal(value)
    if number is None:
        return None
    return Fraction(number)


def _publish_fraction(value: Fraction | None) -> str | None:
    """Exact decimal when it terminates, otherwise 8 places, half up.

    A precision-28 quotient of a repeating fraction prints a residue
    (``32.70250000000000000000000001``). That residue is not the hours.
    """
    if value is None:
        return None
    negative = value < 0
    value = abs(Fraction(value))
    den = value.denominator
    twos = 0
    fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    if den == 1:
        scale = max(twos, fives)
        num = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
        if scale == 0:
            text = str(num)
        else:
            digits = str(num).zfill(scale + 1)
            text = digits[:-scale] + "." + digits[-scale:]
            text = text.rstrip("0").rstrip(".")
    else:
        scale = 8
        scaled = value.numerator * (10 ** scale)
        quotient, remainder = divmod(scaled, value.denominator)
        if remainder * 2 >= value.denominator:
            quotient += 1
        digits = str(quotient).zfill(scale + 1)
        text = digits[:-scale] + "." + digits[-scale:]
        text = text.rstrip("0").rstrip(".")
    if text == "":
        text = "0"
    return "-" + text if negative else text


def cap_queue_scenario(queue_eth: object = Decimal(12950)) -> dict:
    """Queue plus withdrawability IF exit churn is at the 256 ETH cap.

    The quoted "1 day 7 hours" is stored beside the sum. The two are not
    forced equal. The 7.1 day sweep is not added here. Hours are exact
    fractions, so a repeating quotient is not printed as a residue.
    """
    queue_days = queue_wait_days(queue_eth, Decimal(256))
    delay_hours = withdrawability_days() * 24
    queue_hours = None if queue_days is None else queue_days * 24
    sum_hours = None if queue_hours is None else queue_hours + delay_hours
    matches = sum_hours is not None and sum_hours == Fraction(31)
    return {
        "assumption": "exit churn at the 256 ETH per-epoch cap",
        "queue_eth": json_num(as_decimal(queue_eth)),
        "queue_hours": _publish_fraction(queue_hours),
        "withdrawability_hours": _publish_fraction(delay_hours),
        "sum_hours": _publish_fraction(sum_hours),
        "quoted_wait": "1 day 7 hours",
        "matches_quoted_wait": matches,
    }
