"""Lossless type repair for LLM-supplied tool-call arguments.

The orchestrator LLM intermittently emits tool-call arguments with the wrong
JSON type -- ``address`` as a string, ``items`` as a list of bare strings,
``units`` as ``"27"``, ``credit_card_number`` as an int.  The dispatchers in
``co_helper_checkout_orchestrator.py`` fall back to the canonical scenario value
only when a key is *missing*, so a present-but-mistyped value flows straight
through and crashes the sub-agent helper, costing the entire run as an
INFRA_ERROR.

The contract here is deliberately narrow: **repair serialisation, never invent a
value.**

* ``"27"`` -> ``27`` and ``'{"city": "Montreal"}'`` -> ``{"city": "Montreal"}``
  preserve exactly what the agent emitted, so the oracle still judges the
  agent's real output.
* ``["PROD-001"]`` -> ``[{"product_id": "PROD-001", "quantity": ...}]`` would
  require inventing ``quantity``, which feeds the oracle-compared checkpoint
  ``shippingagent_get_quote.TASK_START.item_count``.  We refuse, and fall back
  to the canonical scenario value instead -- the same thing the dispatchers
  already do for a missing key.

Several of these fields are oracle-compared (``paymentagent.TASK_START.units``
and ``.nanos`` among them), so fabricating a plausible value would let the
harness manufacture a detection outcome.  Falling back to the canonical value
keeps the run on exactly the inputs a clean run uses.

Every repair and every fallback is counted, so that "LLM tool-call type
confusion" is reported as an observed agent failure mode rather than silently
disappearing into the infra-error bucket.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation

# Keyed by "field:fromtype->totype" (a repair) or "field:fromtype->fallback"
# (unrepairable; canonical value substituted).
TYPE_REPAIRS: Counter = Counter()

_UNREPAIRABLE = object()

_NANOS_PER_UNIT = 1_000_000_000


def repair_report() -> dict:
    """Repairs and fallbacks observed so far, keyed by ``field:from->to``."""
    return dict(TYPE_REPAIRS)


def reset_repairs() -> None:
    TYPE_REPAIRS.clear()


def _loads(value):
    """Parse a double-encoded JSON string, or return None if it isn't one."""
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _is_acceptable(value, expected, element) -> bool:
    if expected is not bool and isinstance(value, bool):
        return False                      # bools masquerade as ints
    if expected is float and isinstance(value, int):
        return True                       # an int is a perfectly good float
    if not isinstance(value, expected):
        return False
    if expected is list and element is not None:
        return all(isinstance(item, element) for item in value)
    return True


def _repair(value, expected, element):
    if expected is dict:
        parsed = _loads(value)
        return parsed if isinstance(parsed, dict) else _UNREPAIRABLE

    if expected is list:
        seq = value if isinstance(value, list) else _loads(value)
        if not isinstance(seq, list):
            return _UNREPAIRABLE
        if element is None:
            return seq
        recovered = []
        for item in seq:
            if isinstance(item, element):
                recovered.append(item)
                continue
            parsed = _loads(item)
            if not isinstance(parsed, element):
                return _UNREPAIRABLE      # recovering this would mean inventing fields
            recovered.append(parsed)
        return recovered

    if expected is int:
        if isinstance(value, float):
            return int(value) if value.is_integer() else _UNREPAIRABLE
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
            try:
                as_float = float(value.strip())
            except ValueError:
                return _UNREPAIRABLE
            return int(as_float) if as_float.is_integer() else _UNREPAIRABLE
        return _UNREPAIRABLE

    if expected is float:
        if isinstance(value, int):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return _UNREPAIRABLE
        return _UNREPAIRABLE

    if expected is str:
        # int/float/bool round-trip exactly; a dict would only stringify to a
        # Python repr, which is not the value the agent meant to send.
        return str(value) if isinstance(value, (int, float, bool)) else _UNREPAIRABLE

    return _UNREPAIRABLE


def coerce(key: str, value, expected: type, fallback, element: type | None = None):
    """Return ``value`` as ``expected``, repairing losslessly where possible.

    Falls back to ``fallback`` when the value is absent or cannot be recovered
    without inventing data.  ``element`` constrains list member types.
    """
    if value is None:
        return fallback

    if _is_acceptable(value, expected, element):
        return value

    repaired = _repair(value, expected, element)
    if repaired is not _UNREPAIRABLE:
        TYPE_REPAIRS[f"{key}:{type(value).__name__}->{expected.__name__}"] += 1
        return repaired

    TYPE_REPAIRS[f"{key}:{type(value).__name__}->fallback"] += 1
    return fallback


def _as_decimal(value):
    """Exact ``Decimal`` for a numeric or numeric-string value, else ``None``."""
    if isinstance(value, bool):
        return None                       # bools masquerade as ints
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))        # str() first: Decimal(27.49) is not 27.49
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def coerce_money(units_value, nanos_value, units_fallback, nanos_fallback):
    """Return ``(units, nanos)`` for a protobuf-style Money pair.

    A model that emits ``amount_units = 27.49`` has expressed the amount as one
    decimal instead of the ``units``/``nanos`` split the API expects.  This is a
    *semantic* mistake rather than a serialisation one, and ``coerce`` rightly
    refuses it -- ``27.49`` is not losslessly an ``int``.  Redistributing the
    fraction into ``nanos`` does preserve the value exactly, so it is a repair
    and not an invention.

    It matters because ``paymentagent.TASK_START.units`` and ``.nanos`` are
    oracle-compared.  Substituting the canonical fallback hands the oracle a
    clean amount the agent never produced, which masks precisely the tampering
    the money faults exist to detect; preserving the agent's real amount lets
    the oracle judge what the agent actually did.

    Only applied when the fraction has nowhere else to live, i.e. ``nanos`` is
    absent or zero.  A fractional ``units`` *and* a non-zero ``nanos`` is an
    ambiguous intent, so each field then falls back independently, as before.
    """
    amount = _as_decimal(units_value)
    if amount is not None and amount % 1 != 0:
        supplied_nanos = _as_decimal(nanos_value)
        if supplied_nanos is None or supplied_nanos == 0:
            whole = int(amount)                    # truncates toward zero
            frac = amount - whole                  # keeps the sign, as Money requires
            nanos = int((frac * _NANOS_PER_UNIT).to_integral_value())
            TYPE_REPAIRS[f"amount:{type(units_value).__name__}->units+nanos"] += 1
            return whole, nanos

    return (
        coerce("units", units_value, int, units_fallback),
        coerce("nanos", nanos_value, int, nanos_fallback),
    )
