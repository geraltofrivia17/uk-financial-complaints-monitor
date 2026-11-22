"""Firm names: a strict key for identifying a firm, and a looser key for matching.

The FCA and the Ombudsman publish names only (an FCA reference number appears in
some recent FCA files). Spelling, case and legal suffixes vary between releases
and between the two bodies, so names are normalised before they are compared.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN_MAP = {"LTD": "LIMITED", "CO": "COMPANY"}
_LEGAL_SUFFIXES = {"LIMITED", "PLC", "LLP", "LLC", "SA", "SE", "AG", "NV", "INC", "DAC", "PUBL", "AB"}
_LONG_FORMS = {
    "PUBLIC LIMITED COMPANY": "PLC",
    "DESIGNATED ACTIVITY COMPANY": "DAC",
    "LIMITED LIABILITY PARTNERSHIP": "LLP",
}
_TOTAL_ROWS = {"GRAND TOTAL", "TOTAL", "TOTALS", "BLANK", "TOTALS OF THE ABOVE"}


def clean_name(name: object) -> str:
    """The published name with whitespace tidied (used for display)."""
    return re.sub(r"\s+", " ", str(name).replace(" ", " ")).strip().lstrip("*").strip()


def _join_initials(tokens: list[str]) -> list[str]:
    """["U", "K", "INSURANCE"] -> ["UK", "INSURANCE"]: runs of single letters become one token."""
    out: list[str] = []
    run = ""
    for token in tokens:
        if len(token) == 1 and token.isalpha():
            run += token
            continue
        if run:
            out.append(run)
            run = ""
        out.append(token)
    if run:
        out.append(run)
    return out


def name_key(name: object) -> str:
    """Strict key: upper case, no punctuation, LTD spelt out. Identifies one firm."""
    text = unicodedata.normalize("NFKD", clean_name(name)).encode("ascii", "ignore").decode()
    text = text.upper().replace("&", " AND ").replace(".", "").replace("'", "")
    text = re.sub(r"\(THE\)", " ", text)
    for long_form, short in _LONG_FORMS.items():
        text = re.sub(rf"\b{long_form}\b", short, text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    tokens = [_TOKEN_MAP.get(token, token) for token in text.split()]
    tokens = _join_initials(tokens)
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    return " ".join(tokens)


def match_key(name: object) -> str:
    """Looser key for comparing names across sources: legal suffixes removed."""
    tokens = [token for token in name_key(name).split() if token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def is_total_row(name: object) -> bool:
    key = name_key(name)
    return key in _TOTAL_ROWS or key.startswith("OVERALL TOTAL") or key.startswith("TOTALS OF")
