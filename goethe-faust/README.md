# DDB Goethe–*Faust* Corpus

Metadata for 115,432 Deutsche Digitale Bibliothek (DDB) objects retrieved via the keywords *Goethe* and *Faust*.
This corpus serves as the reference implementation corpus for the [GeMeA](https://github.com/ISE-FIZKarlsruhe/gemea) knowledge graph — the alignment and class dispatch logic were developed and validated here before scaling to the full 26.8M-object DDB collection.

## Corpus overview

| Metric | Value |
|---|---|
| Total records | 115,432 |
| Queries | "goethe" (96,773) + "faust" (25,275), deduplicated |
| Unique providers | 454 |
| Records with date | 102,467 (88.8%) |
| Year range | 1010–2025 |
| Digitized | 73,045 (63.3%) |

## Directory layout

```
data/
  ids-all-goethe-faust.txt      — 115,432 DDB object IDs (one per line)
  items-excerpt-1000.json       — 1,000-record sample for inspection
  items-all-goethe-faust.json   — full JSONL (gitignored; download below)

output/
  alignment_ddbedm_mocho.csv/json   — EDM → mocho field alignment table
  dctype_sparte*.csv            — dc:type frequency per sector
  dispatch_signal_ratio.csv     — htype vs dc:type coverage per sector × mediatype
  dispatch_validation.csv       — full-corpus dispatch validation results
  edm_field_profile.*           — EDM field coverage profile
  fig_years.png                 — temporal distribution (25-yr buckets, 1600–present)
  fig1_metadata_format.png      — metadata format breakdown
  fig2_sector.png               — sector × digitization
  fig4_dc_type_top20.png        — top 20 dc:type values
  items-analysis.json           — 6-dimension aggregation
  years-analysis.json           — year extraction results
  [... full listing in output/]

scripts/                        — corpus analysis pipeline (see scripts/README.md)
requirements.txt                — Python dependencies
```

## Downloads

The full JSONL and N-Quads dump are hosted externally:

| File | URL |
|---|---|
| `items-all-goethe-faust.json` (2.4 GB) | <https://gemea.ise.fiz-karlsruhe.de/downloads/goethe-faust/> |
| `goethe-faust.nq` (N-Quads) | <https://gemea.ise.fiz-karlsruhe.de/downloads/goethe-faust/> |

## Reproducing the dataset

Run scripts in order. All scripts use project-relative paths and can be run from any working directory.

1. **Fetch search results** — `python scripts/fetch-search-all.py`
   Requires a DDB API key. Outputs `data/ddb-search-goethe-all.json`.

2. **Fetch item records** — `cd data && bash ../scripts/fetch-items.sh ids-all-goethe-faust.txt`
   Saves individual JSON files and appends to `data/items-all-goethe-faust.json`.
   To fill any gaps: `python scripts/find_missing_items.py`, then re-run `fetch-items.sh ids-missing.txt`.

3. **Build parquet** — `python scripts/build_dataframe.py`
   Outputs `output/items-dataframe.parquet` (115,432 × 10) and a 500-row CSV sample.

4. **Analyse** — `python scripts/analyse_items.py` · `python scripts/analyse_years.py` · `python scripts/analyse_bucket.py`

5. **dc:type / htype analysis** — `python scripts/count_dctype_by_mediatype.py` · `python scripts/dispatch_signal_ratio.py`

6. **Dispatch validation** — `python scripts/validate_dispatch.py`

7. **Generate config lookup tables** — `python scripts/gen_dctype_class_mapping.py` · `python scripts/gen_htype_doco_mapping.py` · etc.

See [`scripts/README.md`](scripts/README.md) for full per-script documentation.

## Self-hosting

**1. Download**
```bash
wget https://gemea.ise.fiz-karlsruhe.de/downloads/goethe-faust/goethe-faust.nq
```

**2. Configure**
```bash
cp config.env.example config.env   # set NQ_INPUT_DIR, INDEX_DIR, and ports
```

**Before running**
- [ ] `NQ_INPUT_DIR` exists and contains `goethe-faust.nq`
- [ ] `INDEX_DIR` exists and the Docker user (UID 1000) has write access — if not:
  ```bash
  sudo chmod 777 $INDEX_DIR
  ```
- [ ] Ports `QLEVER_PORT` and `SHMARQL_PORT` are free on the host

**3. Start QLever + SHMARQL**
```bash
docker compose --env-file config.env -f docker-compose.qlever.yml up -d
```
SPARQL endpoint: `http://localhost:7030` · SHMARQL browser: `http://localhost:7032` (defaults; adjust in `config.env`).

**4. MCP agent access (optional)**
```json
{
  "mcpServers": {
    "goethe-faust-qlever": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "ghcr.io/xorwell/mcp-server-qlever:latest",
               "-e", "http://<qlever-host>:<QLEVER_PORT>"]
    }
  }
}
```

## Troubleshooting

**T1. Check container logs**
```bash
docker compose --env-file config.env -f docker-compose.qlever.yml logs qlever-goethe-faust
```

**T2. `invalid spec: :/input:ro: empty section between colons`**
`NQ_INPUT_DIR` is not set. Check that `config.env` contains a valid `NQ_INPUT_DIR` path.

**T3. `Permission denied` writing to `/data`**
The Docker user cannot write to `INDEX_DIR`. Check that `INDEX_DIR` exists:
```bash
mkdir -p $INDEX_DIR && chmod 777 $INDEX_DIR
```

**T4. `ERROR: no files matched /input/*.nq`**
No `.nq` file found in `NQ_INPUT_DIR`. Confirm `goethe-faust.nq` is in that directory.

**T5. Port already in use**
Change `QLEVER_PORT` or `SHMARQL_PORT` in `config.env` to a free port.

**T6. `dependency qlever-goethe-faust failed to start`**
Root cause is always in the QLever logs — run **T1** first.

## Caveats

- The 2000–2024 temporal bucket is dominated by Goethe-Universität Frankfurt institutional records (theses, working papers) rather than cultural-heritage items about Goethe or *Faust*.
- 2,552 pre-1600 records are excluded from `fig_years.png` but retained in `years-analysis.json`.
- 1 ID was permanently unavailable (HTTP 404) and is absent from the JSONL.

## Citation

If you use this corpus, please cite:

```bibtex
@inproceedings{tan2026gemea,
  author    = {Tan, Mary Ann and Gesese, Genet Asefa and Sack, Harald},
  title     = {{GeMeA: A Knowledge Graph for the German Digital Library}},
  booktitle = {{Proceedings of the 23rd International Semantic Web Conference (ISWC 2026)}},
  year      = {2026},
  note      = {Resource Track. Forthcoming.},
}
```
