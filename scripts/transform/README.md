# scripts/transform/

Reference transform for the mocho ingest pipeline, using the goethe-faust corpus
(115,432 DDB records). Converts DDB-EDM JSONL to mocho-aligned N-Quads and a
DuckDB werk_staging table for GND Werk linking.

---

## Package structure

| Module | Contents |
|---|---|
| `constants.py` | IRIs, prefix table, dispatch tables, path defaults, type aliases |
| `utils.py` | N-Quads formatting, URI minting, value normalisation |
| `loaders.py` | CSV/JSON config loaders |
| `emitters.py` | Triple emitters: ddbedm passthrough, mocho alignment, PROV-O, werk_staging |
| `transform.py` | `transform_record` — per-record orchestration |
| `__main__.py` | CLI entry point (`python -m transform`) |
| `merge.py` | `python -m transform merge` — concat .nq shards, merge DuckDB + stats |
| `sqlite_export.py` | `export()` — sequential SQLite → JSONL export for bulk runs |

---

## Pipeline

### Development / goethe-faust corpus

```
data/items-all-goethe-faust.json      JSONL input
output/config/*.csv / *.json          dispatch + alignment tables
          │
          ▼
  python -m transform   (from scripts/)
          │
          └── output/transform/YYYYMMDD_HHMMSS/
                  <stem>.nq                   N-Quads (all named graphs)
                  <stem>-werk-staging.duckdb  DuckDB werk_staging table
                  <stem>-stats.json           run statistics
                  <stem>-errors.jsonl         per-record errors
                  <stem>.log                  run log
```

`<stem>` is the input filename without extension (e.g. `items-all-goethe-faust.json` → `items-all-goethe-faust`), or the value of `--stub` if provided. Each invocation creates a new timestamped run directory. Use `--outdir` to override.

### Full GeMeA corpus (Option C parallel)

One `sN.sqlite` per sector. Each sector runs in its own subshell — export then
transform back-to-back — with all sectors in parallel:

```
s1.sqlite ─┐                          s1/ (.nq + .duckdb)  ─┐
s2.sqlite ─┤  sqlite_export + transform  s2/ (.nq + .duckdb)  ─┤  cat → merged.nq
  ...      ─┤  (pipelined per sector,    ...                  ─┤  duckdb → merged.duckdb
sN.sqlite ─┘   all in parallel)       sN/ (.nq + .duckdb)  ─┘
```

```bash
GOETHE=goethe-faust
CFG=$GOETHE/output/config
GEMEA=gemea/data/sqlite
EXPORT=/tmp/gemea-export

for n in 1 2 3 4 5 6 7; do
  (
    PYTHONPATH=$GOETHE/scripts python -m transform.sqlite_export \
      --db  $GEMEA/s${n}.sqlite \
      --out $EXPORT/s${n}.jsonl \
    && \
    PYTHONPATH=$GOETHE/scripts python -m transform \
      --jsonl  $EXPORT/s${n}.jsonl \
      --outdir $GOETHE/output/transform/gemea/s${n} \
      --stats dispatch \
      --alignment $CFG/lookup_class_prop_alignment.csv \
      --lido      $CFG/lido_event_types.csv \
      --htype     $CFG/lookup_htype_doco_rico.csv \
      --mediatype $CFG/lookup_mediatype_class.csv \
      --audio     $CFG/audio_type2class.json
  ) &
done
wait

# merge
cat $GOETHE/output/transform/gemea/s*/*.nq > $GOETHE/output/transform/gemea/merged.nq
```

See [`docs/adr/transform-adr.md`](../../docs/adr/transform-adr.md) for dispatch design decisions.

## Named graphs

| Graph IRI | Content |
|---|---|
| `https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm` | Verbatim EDM passthrough |
| `https://gemea.ise.fiz-karlsruhe.de/graph/mocho` | mocho-aligned triples (class dispatch, property alignment, agents, places) |
| `https://gemea.ise.fiz-karlsruhe.de/graph/prov` | PROV-O Layer 1 provenance chain |

`werk_staging` rows are written to DuckDB, not to the N-Quads file.

## Inputs

| File | Description |
|---|---|
| `data/items-all-goethe-faust.json` | DDB-EDM JSONL; one JSON object per line |
| `data/ids-all-goethe-faust.txt` | 32-char DDB object IDs; passed via `--ids` to filter JSONL |
| `output/config/lookup_mediatype_class.csv` | `(sector, mediatype)` → class dispatch |
| `output/config/lookup_htype_doco_rico.csv` | `htype_code` → rdf:type IRIs (§1.1) |
| `output/config/lookup_class_prop_alignment.csv` | `(target_class, edm_prop)` → mocho property |
| `output/config/lido_event_types.csv` | LIDO event URI → agent predicates per WEMI level |
| `output/config/audio_type2class.json` | `dc:type` → audio group (A/B/C) for mt001 dispatch |

## CLI

Run from the `scripts/` directory. Full argument list: `python -m transform --help`.

| Group | Arguments |
|---|---|
| I/O | `--jsonl FILE`, `--ids FILE`, `--outdir DIR`, `--stem STR` |
| Config | `--alignment`, `--lido`, `--htype`, `--mediatype`, `--audio` |
| Stats | `--stats none\|basic\|dispatch\|full` (default: `basic`) |
| Logging | `--log-level DEBUG\|INFO\|WARNING\|ERROR`, `--debug` |
| Development | `--limit N` |

**Stats recommendation**: `--stats dispatch` for full-corpus runs; `--stats full --limit 50000`
for vocabulary coverage data.

## Merging shards

After a parallel run, merge all per-worker outputs into combined files:

```bash
python -m transform merge /data/ddb/gemea/mocho-transform/20260508_1200xxxx
```

Finds all `*.nq`, `*-werk-staging.duckdb`, and `*-stats.json` under the run directory
recursively, merges them, and deletes the shards only after all merges succeed.

| Output | Default path |
|---|---|
| N-Quads | `<out_base>/combined.nq` |
| DuckDB werk_staging | `<out_base>/werk-staging-merged.duckdb` |
| Stats | `<out_base>/combined-stats.json` |

Override with `--nq`, `--db`, `--stats`. DuckDB merge is skipped if `duckdb` is not
installed (shard files retained).

---

```bash
# full corpus, default stats
python -m transform

# full corpus, dispatch stats (recommended for production)
python -m transform --stats dispatch

# first 500 records, debug logging
python -m transform --limit 500 --debug

# vocabulary coverage sample
python -m transform --limit 50000 --stats full

# explicit output directory
python -m transform --outdir ../output/transform/dev

# custom ID filter
python -m transform --ids ../data/ids-sample.txt
```

## Tests

```bash
# from project root
.venv/bin/python -m pytest scripts/transform/tests/ -q
```

42 unit tests covering loaders, class dispatch (§1.1), N-Quad formatting, PROV node
construction, creator/contributor dispatch, and the D9 fallback. Uses actual config
CSVs — no mocking.

---

**Key references**:
- [`../../docs/adr/transform-adr.md`](../../docs/adr/transform-adr.md) — class dispatch and WEMI alignment decisions (D1–D12)
- [`../../docs/adr/transform-script-adr.md`](../../docs/adr/transform-script-adr.md) — implementation decisions (D13–D27)
- [`../../docs/adr/transform-props-mapping-adr.md`](../../docs/adr/transform-props-mapping-adr.md) — property mapping decisions
