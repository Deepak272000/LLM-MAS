"""
ConversionAgent
===============
Deterministic sub-agent that converts currency amounts.

Uses a static exchange-rate table (relative to USD).
No LLM, no gRPC — invoked as a tool by CurrencyOrchestrator.
"""
import logging

log = logging.getLogger(__name__)

# Rates relative to 1 USD (as of training-data cutoff — for research use)
USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 149.50,
    "CAD": 1.36,
    "AUD": 1.53,
    "CHF": 0.90,
    "CNY": 7.24,
    "INR": 83.10,
    "BRL": 4.97,
    "MXN": 17.15,
    "SGD": 1.34,
    "HKD": 7.82,
    "NOK": 10.56,
    "SEK": 10.42,
    "DKK": 6.88,
    "NZD": 1.63,
    "ZAR": 18.63,
    "KRW": 1325.0,
    "TRY": 30.50,
    "PLN": 3.98,
    "THB": 35.10,
    "IDR": 15700.0,
    "VND": 24100.0,
    "NGN": 1580.0,
    "EGP": 30.90,
    "BGN": 1.80,
    "CZK": 22.60,
    "HUF": 356.0,
    "RON": 4.57,
    "ILS": 3.70,
    "AED": 3.67,
    "SAR": 3.75,
    "QAR": 3.64,
    "CLP": 928.0,
    "COP": 3980.0,
    "ARS": 870.0,
    "PEN": 3.71,
    "MAD": 10.10,
    "KZT": 452.0,
    "UAH": 37.50,
    "PKR": 279.0,
    "BDT": 110.0,
    "LKR": 325.0,
}

SUPPORTED = sorted(USD_RATES.keys())


class ConversionAgent:
    """Converts a money amount from one currency to another."""

    def convert(
        self,
        from_currency: str,
        units: int,
        nanos: int,
        to_currency: str,
    ) -> dict:
        """
        Convert money.

        Args:
            from_currency: source ISO 4217 code
            units:         whole units
            nanos:         fractional nanos (0–999_999_999)
            to_currency:   target ISO 4217 code

        Returns:
            dict with keys:
              - currency_code: str
              - units: int
              - nanos: int
              - rate_used: float
              - error: str or None
        """
        src = from_currency.upper().strip()
        tgt = to_currency.upper().strip()

        if src not in USD_RATES:
            log.warning("ConversionAgent: unsupported source currency %s", src)
            return {"error": f"Unsupported currency: {src}",
                    "currency_code": tgt, "units": 0, "nanos": 0, "rate_used": 0.0}
        if tgt not in USD_RATES:
            log.warning("ConversionAgent: unsupported target currency %s", tgt)
            return {"error": f"Unsupported currency: {tgt}",
                    "currency_code": tgt, "units": 0, "nanos": 0, "rate_used": 0.0}

        amount_src = units + nanos / 1_000_000_000
        # Convert via USD as pivot
        amount_usd = amount_src / USD_RATES[src]
        amount_tgt = amount_usd * USD_RATES[tgt]

        out_units = int(amount_tgt)
        out_nanos = round((amount_tgt - out_units) * 1_000_000_000)
        rate_used = round(USD_RATES[tgt] / USD_RATES[src], 6)

        log.info(
            "ConversionAgent: %.9f %s → %.9f %s (rate=%.6f)",
            amount_src, src, amount_tgt, tgt, rate_used
        )

        return {
            "currency_code": tgt,
            "units":         out_units,
            "nanos":         out_nanos,
            "rate_used":     rate_used,
            "error":         None,
        }


class SupportedCurrenciesAgent:
    """Returns the list of supported currency codes."""

    def list_currencies(self) -> dict:
        log.info("SupportedCurrenciesAgent: returning %d currencies", len(SUPPORTED))
        return {"currency_codes": SUPPORTED}
