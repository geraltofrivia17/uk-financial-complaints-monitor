"""Firm identity, and the mapping between FCA firm names and Ombudsman business names.

A firm is identified by its name with legal suffixes removed ("match key"), so
"Cynergy Bank Limited" and "Cynergy Bank Plc" in different releases are one firm.
Where two different names share a match key in the same release they are kept
apart under their strict keys. FCA reference numbers (FRN) are attached where a
release's trading-names sheet gives them.

The FCA and the Ombudsman name firms independently. Names that agree after
normalisation are linked automatically. Close but different names are written to
a review file, and only pairs marked "accept" there are used.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from .names import match_key, name_key

REVIEW_COLUMNS = [
    "fos_business_name",
    "fca_firm_name",
    "score",
    "decision",
    "review_note",
]


def assign_firm_ids(names: pd.DataFrame, reference: pd.DataFrame | None = None) -> pd.DataFrame:
    """names has period_label and firm_name. Returns firm_name -> firm_id (one row per name).

    Names are grouped by match key. Where the FCA's trading-names sheet gives one
    reference number (FRN) for two different names, the firm was renamed, and both
    names take the identifier of the name used most recently.
    """
    from .periods import label_sort_key

    frame = names[["period_label", "firm_name"]].drop_duplicates().copy()
    frame["strict"] = frame["firm_name"].map(name_key)
    frame["loose"] = frame["firm_name"].map(match_key)
    clashes = frame.groupby(["period_label", "loose"])["strict"].nunique()
    clashing = set(clashes[clashes > 1].index.get_level_values("loose"))
    frame["firm_id"] = frame["loose"].where(~frame["loose"].isin(clashing), frame["strict"])

    if reference is not None and not reference.empty:
        ref = reference.assign(
            key=reference["firm_name"].map(match_key), order=reference["period_label"].map(label_sort_key)
        )
        ref = ref[ref["key"].isin(set(frame["firm_id"]))]
        renames = {}
        for _, group in ref.groupby("frn"):
            keys = group.sort_values("order")["key"].unique()
            if len(keys) > 1:
                latest = group.sort_values("order")["key"].iloc[-1]
                renames.update({key: latest for key in keys if key != latest})
        frame["firm_id"] = frame["firm_id"].replace(renames)
    return frame.drop_duplicates("firm_name")[["firm_name", "firm_id"]]


def renamed_firms(ids: pd.DataFrame) -> pd.DataFrame:
    """Firms published under more than one name, for the documentation."""
    grouped = ids.assign(key=ids["firm_name"].map(match_key)).groupby("firm_id")["key"].nunique()
    return ids[ids["firm_id"].isin(grouped[grouped > 1].index)].sort_values(["firm_id", "firm_name"])


def _prefer_mixed_case(latest_names: pd.Series, rows: pd.DataFrame) -> pd.Series:
    """Latest name per firm, swapped for a mixed-case spelling of the same name where one exists."""
    spellings = rows[["firm_id", "firm_name", "_order"]].assign(_key=rows["firm_name"].map(name_key))
    mixed = spellings[spellings["firm_name"].str.upper() != spellings["firm_name"]]
    best = mixed.sort_values("_order").groupby(["firm_id", "_key"])["firm_name"].last()
    out = latest_names.copy()
    for firm_id, name in latest_names.items():
        if name.upper() == name:
            out[firm_id] = best.get((firm_id, name_key(name)), name)
    return out


def firm_dimension(firm_rows: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """One row per firm: latest published name and group, first and last period, FRN.

    Some releases publish names in capitals ("NATIONAL WESTMINSTER BANK PUBLIC
    LIMITED COMPANY"). When the latest name is all capitals, the most recent
    spelling of the same name (same name_key) published in mixed case is used
    instead ("National Westminster Bank Plc"), so the name is always one the FCA
    published. A firm's renames are not undone: only spellings of its latest
    name are considered.
    """
    from .periods import label_sort_key

    rows = firm_rows.assign(_order=firm_rows["period_label"].map(label_sort_key)).sort_values("_order")
    latest = rows.groupby("firm_id").last()
    latest["firm_name"] = _prefer_mixed_case(latest["firm_name"], rows)
    summary = rows.groupby("firm_id").agg(
        first_period=("period_label", "first"),
        last_period=("period_label", "last"),
        periods_reported=("period_label", "nunique"),
        names_used=("firm_name", "nunique"),
    )
    dim = latest[["firm_name", "firm_group"]].join(summary).reset_index()
    if not reference.empty:
        ref = reference.assign(
            _key=reference["firm_name"].map(match_key), _order=reference["period_label"].map(label_sort_key)
        )
        ref = ref.merge(
            firm_rows[["firm_name", "firm_id"]]
            .drop_duplicates()
            .assign(_key=lambda d: d["firm_name"].map(match_key))[["_key", "firm_id"]]
            .drop_duplicates("_key"),
            on="_key",
            how="inner",
        )
        ref = ref.sort_values("_order").groupby("firm_id")["frn"].last()
        dim = dim.merge(ref.rename("frn"), on="firm_id", how="left")
    else:
        dim["frn"] = pd.NA
    dim["frn"] = dim["frn"].astype("Int64")
    return dim.rename(columns={"firm_name": "firm_name_latest", "firm_group": "firm_group_latest"})


def candidate_pairs(
    fos_names: pd.Series, fca_names: pd.DataFrame, threshold: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Exact links, and close pairs that need review.

    fca_names has every published FCA firm name with its firm_id, so an Ombudsman
    name matches a firm under any name the firm has used. Returns (exact,
    candidates): exact has fos_business_name and firm_id; candidates holds the
    best FCA name for each Ombudsman name without an exact link that scores at or
    above the threshold.
    """
    fca = fca_names[["firm_name", "firm_id"]].drop_duplicates().copy()
    fca["key"] = fca["firm_name"].map(match_key)
    fca = fca.drop_duplicates("key")
    by_key = dict(zip(fca["key"], fca["firm_id"], strict=True))
    name_by_key = dict(zip(fca["key"], fca["firm_name"], strict=True))
    choices = list(by_key)
    exact_rows, candidates = [], []
    for name in sorted(set(fos_names.dropna())):
        key = match_key(name)
        if key in by_key:
            exact_rows.append({"fos_business_name": name, "firm_id": by_key[key]})
            continue
        best = process.extractOne(key, choices, scorer=fuzz.token_sort_ratio)
        if best and best[1] >= threshold:
            candidates.append(
                {
                    "fos_business_name": name,
                    "fca_firm_name": name_by_key[best[0]],
                    "firm_id": by_key[best[0]],
                    "score": round(best[1], 1),
                }
            )
    return pd.DataFrame(exact_rows), pd.DataFrame(candidates)


def merge_reviews(candidates: pd.DataFrame, review_path: Path) -> pd.DataFrame:
    """Carry earlier decisions into the current candidate list and rewrite the review file."""
    if review_path.exists():
        previous = pd.read_csv(review_path, dtype=str).fillna("")
    else:
        previous = pd.DataFrame(columns=REVIEW_COLUMNS)
    merged = candidates.merge(
        previous[["fos_business_name", "fca_firm_name", "decision", "review_note"]],
        on=["fos_business_name", "fca_firm_name"],
        how="left",
    ).fillna({"decision": "", "review_note": ""})
    merged.sort_values(["decision", "score"], ascending=[True, False])[REVIEW_COLUMNS].to_csv(
        review_path, index=False, lineterminator="\n"
    )
    return merged


def firm_map(exact: pd.DataFrame, reviewed: pd.DataFrame) -> pd.DataFrame:
    accepted = reviewed[reviewed["decision"].str.lower() == "accept"][["fos_business_name", "firm_id"]]
    out = pd.concat(
        [exact.assign(link_method="exact name"), accepted.assign(link_method="reviewed match")],
        ignore_index=True,
    )
    return out.drop_duplicates("fos_business_name")
