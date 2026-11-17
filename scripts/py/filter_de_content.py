#!/usr/bin/env python3
"""
Purpose:  Filter s2_meta.parquet to German-language and Latin content titles.
          Applies two sequential filters:
            1. Remove non-content hierarchy types (BLANKET_EXCLUDE)
            2. Keep only target language codes: ger, gmh, nds, lat
          Prints a step-by-step summary with counts and percentages.
Usage:    python scripts/py/filter_de_content.py
Inputs:   data/out/s2/s2_meta.parquet
Outputs:  data/out/s2/s2_meta_de_content.parquet
          data/processed/filter_de_content_summary.csv
Dependencies: pandas, pyarrow
Assumptions: Run from the gemea/ project root.
"""

import pandas as pd
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PARQUET_IN  = Path("data/out/s2/s2_meta.parquet")
PARQUET_OUT = Path("data/out/s2/s2_meta_de_content.parquet")
CSV_OUT     = Path("data/processed/filter_de_content_summary.csv")
CSV_OUT.parent.mkdir(parents=True, exist_ok=True)

# ── Filter definitions ─────────────────────────────────────────────────────────
# Non-content htypes
BLANKET_EXCLUDE = {
    "htype_001",  # Abschnitt          — section headings
    "htype_004",  # Annotation         — manuscript annotation labels
    "htype_010",  # Eintrag            — same as Annotation
    "htype_016",  # Index              — back-matter registers/indexes
    "htype_017",  # Inhaltsverzeichnis — table-of-contents labels
    "htype_018",  # Kapitel            — chapter headings
    "htype_028",  # Vorwort            — prefaces/paratexts
    "htype_029",  # Widmung            — dedication texts
}

# Target language codes (ISO 639-2/B): German-family + Latin
# Latin is included because pre-1800 German cultural heritage is heavily bilingual;
# GND has Latin Werk records; xlm-roberta handles Latin via multilingual pretraining.
DE_LANGS = {"ger", "gmh", "nds", "lat"}

# ── Load ───────────────────────────────────────────────────────────────────────
print(f"Loading {PARQUET_IN} …")
df = pd.read_parquet(PARQUET_IN)
n_total = len(df)
print(f"  {n_total:,} rows\n")

# ── Step 1: htype filter ───────────────────────────────────────────────────────
excluded_htypes = df["hierarchy_type"].isin(BLANKET_EXCLUDE)
n_htype_excluded = excluded_htypes.sum()

# breakdown by excluded htype
htype_counts = (
    df[excluded_htypes]["hierarchy_type"]
    .value_counts()
    .rename_axis("htype")
    .reset_index(name="n_excluded")
)
htype_counts["pct_of_total"] = htype_counts["n_excluded"] / n_total * 100

df_after_htype = df[~excluded_htypes].copy()
n_after_htype = len(df_after_htype)

# ── Step 2: language filter ────────────────────────────────────────────────────
lang_col = df_after_htype["lang"].fillna("(none)").str.strip()
lang_col = lang_col.replace("", "(none)")

non_de = ~lang_col.isin(DE_LANGS)
n_lang_excluded = non_de.sum()

# breakdown by excluded lang (top 15)
lang_counts = (
    df_after_htype[non_de]["lang"]
    .fillna("(none)").str.strip().replace("", "(none)")
    .value_counts()
    .rename_axis("lang")
    .reset_index(name="n_excluded")
)
lang_counts["pct_of_after_htype"] = lang_counts["n_excluded"] / n_after_htype * 100

# also breakdown of kept langs
kept_lang_counts = (
    lang_col[~non_de]
    .value_counts()
    .rename_axis("lang")
    .reset_index(name="n_kept")
)

df_final = df_after_htype[~non_de].copy()
n_final = len(df_final)

# ── Print summary ──────────────────────────────────────────────────────────────
W = 62
print("━" * W)
print(" Filter summary")
print("━" * W)
print(f"  {'Total rows':<36} {n_total:>10,}  (100.0%)")
print()

print(f"  Step 1 — remove non-content htypes")
print(f"  {'Excluded (BLANKET_EXCLUDE)':<36} {n_htype_excluded:>10,}  "
      f"({n_htype_excluded / n_total * 100:.1f}%)")
for _, r in htype_counts.iterrows():
    print(f"    {r['htype']:<34} {r['n_excluded']:>10,}  "
          f"({r['pct_of_total']:.1f}%)")
print(f"  {'Remaining after htype filter':<36} {n_after_htype:>10,}  "
      f"({n_after_htype / n_total * 100:.1f}%)")
print()

print(f"  Step 2 — keep only German + Latin (ger, gmh, nds, lat)")
print(f"  {'Excluded (non-German lang)':<36} {n_lang_excluded:>10,}  "
      f"({n_lang_excluded / n_after_htype * 100:.1f}% of remaining)")
print(f"  Top excluded language codes:")
for _, r in lang_counts.head(15).iterrows():
    print(f"    {r['lang']:<34} {r['n_excluded']:>10,}  "
          f"({r['pct_of_after_htype']:.1f}%)")
print()

print(f"  Kept language breakdown:")
for _, r in kept_lang_counts.iterrows():
    print(f"    {r['lang']:<34} {r['n_kept']:>10,}  "
          f"({r['n_kept'] / n_final * 100:.1f}% of final)")
print()

print("━" * W)
print(f"  {'Final rows':<36} {n_final:>10,}  "
      f"({n_final / n_total * 100:.1f}% of total)")
print(f"  {'Total removed':<36} {n_total - n_final:>10,}  "
      f"({(n_total - n_final) / n_total * 100:.1f}% of total)")
print("━" * W)

# ── Save summary CSV ───────────────────────────────────────────────────────────
rows = [
    {"step": "total",          "filter": "",           "n": n_total,         "pct_of_total": 100.0},
    {"step": "after_htype",    "filter": "htype",      "n": n_after_htype,   "pct_of_total": n_after_htype / n_total * 100},
    {"step": "final",          "filter": "lang=de",    "n": n_final,         "pct_of_total": n_final / n_total * 100},
]
for _, r in htype_counts.iterrows():
    rows.append({"step": "htype_excluded", "filter": r["htype"],
                 "n": r["n_excluded"], "pct_of_total": r["pct_of_total"]})
for _, r in lang_counts.iterrows():
    rows.append({"step": "lang_excluded", "filter": r["lang"],
                 "n": r["n_excluded"], "pct_of_total": r["n_excluded"] / n_total * 100})

summary_df = pd.DataFrame(rows)
summary_df.to_csv(CSV_OUT, index=False)
print(f"\nSummary CSV saved → {CSV_OUT}")

# ── Save parquet ───────────────────────────────────────────────────────────────
print(f"Saving {PARQUET_OUT} …")
df_final.to_parquet(PARQUET_OUT, index=False)
print(f"  Saved {n_final:,} rows, {len(df_final.columns)} columns")
print(f"  Size: {PARQUET_OUT.stat().st_size / 1024**2:.1f} MB")
