# goethe-faust/scripts/

Corpus acquisition and analysis scripts for the Goethe-Faust reference corpus (115,432 DDB records).
These scripts drive the corpus analysis that informs the GeMeA class dispatch and property alignment design.

Run scripts in order. All scripts use project-relative paths and can be run from any working directory.

---

## Phase 1 — Data acquisition

| Script | Output | Notes |
|---|---|---|
| `fetch-search-all.py` | `data/ddb-search-goethe-all.json` | Requires DDB API key |
| `fetch-items.sh` | `data/items-all-goethe-faust.json` | Fetches per-item JSON; resumes from last ID |
| `fetch-progress.sh` | stdout | Progress monitoring for fetch-items.sh |
| `find_missing_items.py` | `data/ids-missing.txt` | Finds gaps; re-run fetch-items.sh with output |

```bash
python scripts/fetch-search-all.py
cd data && bash ../scripts/fetch-items.sh ids-all-goethe-faust.txt
python scripts/find_missing_items.py   # if needed
```

## Phase 2 — DataFrame build

| Script | Output |
|---|---|
| `build_dataframe.py` | `output/items-dataframe.parquet`, `output/items-dataframe-sample.csv` |

```bash
python scripts/build_dataframe.py
```

## Phase 3 — Core corpus analysis

| Script | Output | Notes |
|---|---|---|
| `analyse_items.py` | `output/items-analysis.json` | 6-dimension aggregation |
| `analyse_years.py` | `output/fig_years.png`, `output/years-analysis.json` | Temporal distribution |
| `analyse_bucket.py` | stdout | Drill into a specific year bucket |
| `audit_timespan_coverage.py` | stdout | TimeSpan field coverage |
| `extract_view_fields.py` | `output/view-<item-id>.json` | View field structure |
| `extract_view_id_name.py` | `output/view_id_name.json` | Unique (id, name) pairs |
| `profile_edm_fields.py` | `output/edm_field_profile.json`, `output/edm_field_profile.csv` | EDM field coverage |
| `profile_json_keys.py` | `output/edm_json_key_profile.csv` | Non-EDM JSON key paths |

## Phase 4 — dc:type / htype analysis

Quantifies signal coverage per sector × mediatype stratum — the empirical basis for dispatch priority in [`../../docs/adr/transform-adr.md`](../../docs/adr/transform-adr.md).

| Script | Output |
|---|---|
| `count_dctype_by_mediatype.py` | `output/dctype_sparte*.csv` (one per sector) |
| `count_dctype_gnd_coverage.py` | `output/dctype_gnd_coverage.csv` |
| `count_htype_by_sector.py` | `output/htype_by_sector.csv` |
| `top10_dctype_by_sector.py` | `output/top10_dctype_by_sector.csv` |
| `top10_dctype_per_htype.py` | `output/top10_dctype_per_htype.csv`, `...-annotated.csv` |
| `dispatch_signal_ratio.py` | `output/dispatch_signal_ratio.csv`, `output/dispatch_fallback.csv` |
| `summarise_vocab_coverage.py` | `output/vocab_coverage_summary.csv` |

## Phase 5 — Dispatch validation

| Script | Output | Notes |
|---|---|---|
| `validate_dispatch.py` | `output/dispatch_validation.csv`, `output/validation_sample.csv` | Full-corpus validation |
| `validate_sample.py` | stdout | Cross-checks validation_sample.csv against lookup CSVs |
| `inspect_fallback.py` | stdout | Groups D9 fallback records by (sector, mediatype, htype, dc:type) |
| `sample_type_dispatch.py` | stdout | Spot-checks dispatch table against sample records |
| `analyse_ispartof.py` | stdout | isPartOf field analysis (basis for transform-props-mapping-adr.md D12) |

## Config generation

Generate the lookup tables consumed by `scripts/transform/`. Outputs are checked in under [`../../scripts/config/`](../../scripts/config/).

| Script | Output |
|---|---|
| `gen_dctype_class_mapping.py` | `../../scripts/config/lookup_dctype_to_class.csv` |
| `gen_htype_doco_mapping.py` | `../../scripts/config/lookup_htype_doco_rico.csv/json` |
| `gen_image_type2class.py` | `../../scripts/config/image_type2class.json` |
| `gen_video_type2class.py` | `../../scripts/config/video_type2class.json` |
| `gen_agent_alignment_rows.py` | `../../scripts/config/lookup_class_prop_alignment.csv` |

Run config generation after Phase 4 (dc:type frequencies are required as input).
