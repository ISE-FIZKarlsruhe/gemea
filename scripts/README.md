# scripts/

Two pipelines build GEMEA from the DDB metadata:

- **Transform** (`transform/`): DDB-EDM records → mocho-aligned N-Quads in the named graphs `graph/ddbedm`, `graph/mocho` and `graph/prov`.
- **GND Werk linking** (`py/`, `werk-linking/`): titles of library records → GND Works → N-Quads in `graph/work`, with the provenance of each linking run in `graph/prov`.

| Requirements | For |
|---|---|
| [`../requirements-transform.txt`](../requirements-transform.txt) | the transform runtime |
| [`../requirements-linking.txt`](../requirements-linking.txt) | GND Werk linking (Flair and torch for step 1) |
| [`../requirements.txt`](../requirements.txt) | the config generators and the Goethe-Faust scripts |

Run every script from the repository root. Tests:

```bash
python -m pytest scripts/transform/tests scripts/tests scripts/werk-linking/tests -q
```

---

## Transform

A Python package (`python -m transform`) that converts DDB-EDM JSONL or SQLite records to N-Quads and a DuckDB `werk_staging` table. Usage, inputs, outputs and the full-corpus drivers: [`transform/README.md`](transform/README.md).

| Folder | Contents |
|---|---|
| `transform/` | the package: dispatch, emitters, loaders, prescan, merge |
| `transform/run/` | drivers for the full corpus and one sector, progress monitor, server venv |
| `transform/tools/` | N-Quads helpers: split by graph, fix IRIs, merge statistics |
| `transform/config_gen/` | generators of the lookup tables below |
| `transform/tests/` | pytest suite and fixtures |

The Goethe-Faust test corpus is in [`../goethe-faust/`](../goethe-faust/). The transform and the config generators find it through `GOETHE_FAUST_DIR`, which defaults to a sibling checkout; in this repository run `export GOETHE_FAUST_DIR=$PWD/goethe-faust` first.

### Lookup tables (`../config/transform/`)

| File | Purpose |
|---|---|
| `lookup_dctype_to_class.csv` | dc:type string → mocho class dispatch |
| `lookup_htype_doco_rico.csv`, `.json` | hierarchy type → mocho, DoCO or RiC-O class |
| `lookup_mediatype_class.csv` | (sector, media type) → whether the hierarchy type decides, and the W and M classes |
| `lookup_class_prop_alignment.csv` | (target class, EDM property) → target property |
| `image_type2class.json` | image dc:type → class |
| `audio_type2class.json` | audio dc:type → class |
| `video_type2class.json` | video dc:type → class |
| `lido_event_types.csv` | LIDO event type → agent and place predicates (RDA, VRA, RiC-O, DC fallback) |
| `lookup_vocnet.csv` | DDB vocnet IRIs (sectors, media types, hierarchy types) with English and German labels |

### Config generators (`transform/config_gen/`)

| Script | Writes |
|---|---|
| `profile_edm_fields.py` | which EDM and DC properties the Goethe-Faust records use (input of the alignment) |
| `align_ddbedm_to_mocho.py` | the DDB-EDM → mocho property alignment |
| `gen_agent_alignment_rows.py` | the agent rows of `lookup_class_prop_alignment.csv` |
| `gen_dctype_class_mapping.py` | `lookup_dctype_to_class.csv` |
| `gen_htype_doco_mapping.py` | `lookup_htype_doco_rico.csv` and `.json` |
| `gen_image_type2class.py` | `image_type2class.json` |
| `gen_video_type2class.py` | `video_type2class.json` |

---

## GND Werk linking

Links DDB library records to the GND Work they manifest, from their titles and authors.

```
s2_meta.parquet                       library sector, from python -m transform.prescan
  │  py/filter_de_content.py          step 0: content records in German, Middle High German, Low German or Latin
  ▼
s2_meta_de_content.parquet
  │  py/extract_werk_spans.py         step 1: title spans (ISBD rule, volume rule, Flair WORK_OF_ART); GPU
  ▼
werk_spans.jsonl
  │  py/link_gnd_local.py             step 2: candidate Works from the local GND tables, title and author rules
  │  py/merge_link_parts.py           when step 2 ran in parts
  ▼
werk_links.csv
  │  py/collect_run_provenance.py     the run's provenance record
  │  werk-linking/write_werk_rdf.py   links as N-Quads and the run's PROV-CRED provenance
  ▼
gnd-work-links-<run-id>.nq, prov-<run-id>.nq
  │  werk-linking/load_nq_qlever.py   into a running QLever server, without a rebuild
  ▼
graph/work, graph/prov
```

Each script's header gives its usage, inputs, outputs and assumptions. Every script that writes has `--dry-run`, except `filter_de_content.py`.

### Steps (`py/`)

| Script | Purpose |
|---|---|
| `filter_de_content.py` | Step 0: keep the content records (non-content hierarchy types dropped) whose language is `ger`, `gmh`, `nds` or `lat`; writes `s2_meta_de_content.parquet` and a summary CSV |
| `extract_werk_spans.py` | Step 1: title spans from the ISBD rule, a volume rule and Flair `WORK_OF_ART`, plus names and DDB authors, one JSON line per record of a linked type group; shards over GPUs, resumes by chunk, `--merge` joins the shards |
| `t10_rule_baseline.py` | The ISBD title rule step 1 loads; checked against its SHA-256, so it is kept byte for byte |
| `werk_link_rules.py` | Shared rules: title and name normalisation, search terms, author sources, decision rules, type groups, resume helpers, run ID. Not run itself |
| `gnd_jsonld_to_parquet.py` | Converts the GND Work, person and corporate body JSON-LD dumps to the Parquet tables step 2 reads, with a manifest of checksums |
| `link_gnd_local.py` | Step 2: links each record's title spans to GND Works from the local tables; baseline decision: exact title, agreeing author, one Work; resumes by chunk; records commit, checksums and settings in `run.json` |
| `merge_link_parts.py` | Joins the outputs of step 2 run on parts of one step 1 file; stops if the parts differ in commit, tables or settings |
| `link_gnd_bikidata.py` | Step 2 through a bikidata full-text index instead of local tables (alternative linker; `test_link_gnd_local.py` imports its test backend) |

### Evaluation (`py/`)

| Script | Purpose |
|---|---|
| `sample_werk_links.py` | Seeded random samples per link status into one labelling workbook (DDB and GND links, label drop-downs, run sheet) |
| `werk_links_precision.py` | Strict and lenient precision with Wilson 95 % intervals from a labelled workbook, overall and per type group; cause counts for error sheets |
| `werk_links_coverage.py` | Status counts of a links CSV by type group, author source, matched span source, truncation and candidate count |
| `count_no_candidate.py` | Breakdown of the records without a candidate Work |
| `count_function_word_spans.py` | Title spans whose search term holds only function words, by span source |
| `count_werk_linking_types.py`, `count_werk_type_pairs.py` | Hierarchy types and dc:type values of the input against the GND Work classes; the basis of the type groups |
| `isbd_probe_goethe_faust.py`, `flair_work_of_art_probe.py` | Probes of the ISBD rule and of Flair on sample titles; no GND queries |

### RDF and provenance

| Script | Purpose |
|---|---|
| `py/collect_run_provenance.py` | The provenance record of one run (`host-facts` on the GPU host; `record` and `counts` where the run's files are) |
| `werk-linking/write_werk_rdf.py` | One finished run → `gnd-work-links-<run-id>.nq` (`graph/work` plus one path graph per span method), `prov-<run-id>.nq` (`graph/prov`) and a JSON of checksums and counts |
| `werk-linking/run_files.py`, `ner.py`, `linking.py`, `handcheck.py`, `nquads.py` | Modules of the writer: run files, span side, linking side, hand check, N-Quads terms. Not run themselves |
| `werk-linking/precision_by_path.py` | Precision per path graph from a labelled sample, for the hand check triples |
| `werk-linking/count_nq.py` | Counts of N-Quads files: triples, predicates, classes per graph, Works per combination of path graphs |
| `werk-linking/check_work_links.py` | All records one run linked to a given GND Work, with DDB and GND metadata, for checking one Work by hand |
| `werk-linking/label_work_4766057_0.py` | Labels for `check_work_links.py --labels` for GND Work 4766057-0; an example, valid for that Work only |
| `werk-linking/load_nq_qlever.py` | Loads N-Quads files into a running QLever server in `INSERT DATA` batches, counting each graph before and after |

`werk-linking/` is a folder of modules run by path (its name has a hyphen). `write_werk_rdf.py` reads `werk_link_rules.py` at the commit a run was made with, through git; that commit must be in the repository it runs in.

### GND as SPARQL (`sh/`)

| Script | Purpose |
|---|---|
| `setup_gnd_qlever.sh` | Converts the GND JSON-LD authority files to N-Triples, builds a QLever index and starts a local SPARQL server on port 7001 |
