# ADR: EDM JSONL → mocho RDF Transform

**Date**: 2026-04-14
**Status**: Accepted
**Related**: `mocho/notes/alignment-ddbedm-mocho-adr.md`, `goethe-faust/notes/alignment-plan.md`

---

## Context

`transform_edm_to_mocho.py` is the reference implementation of the mocho
ingest transformation. It converts DDB-EDM JSONL corpora to mocho-aligned
RDF triples, validated against the goethe-faust corpus (115,432 records).
The decisions here are not corpus-specific: they define the canonical approach
for the mocho ingest pipeline and will be inherited by any subsequent
corpus transformation built on this foundation.

The alignment table `alignment_ddbedm_mocho.csv` is produced by the mocho
pipeline (`align_ddbedm_to_mocho.py`); this document records decisions that
govern how the transform script consumes — and in some cases corrects — that
table.

Decisions D1–D5 in `mocho/notes/alignment-ddbedm-mocho-adr.md` cover the
upstream alignment pipeline (field profiling, DC→RDA routing, IRI resolution).
This file records D1 — the design decision governing dispatch architecture.
Implementation decisions (D1–D14) are in `transform-script-adr.md`. The two
downstream ADRs are complementary: this file sets the strategy; the script ADR
records how it is realised in code.

### Methodology: corpus-driven transformation design

Before implementing dispatch logic, corpus statistics are used to determine
what signals are actually present in each sector × mediatype stratum. This
evidence-base drives all structural decisions in the transform — dispatch order,
fallback chains, and which signals to ignore — so that the implementation matches
the data rather than an assumed schema. The steps are:

1. **Measure signal coverage** (D1, this file) — quantify htype-only / dc:type-only / both / neither per stratum
2. **Design dispatch table** — set priority order and fallback per stratum from the coverage findings
3. **Implement** (D1–D14, `transform-script-adr.md`) — encode the table in `transform_edm_to_mocho.py`
4. **Validate** — spot-check output against corpus counts; update table if coverage assumptions break

---

## Decision 1: Signal coverage measurement — step 1 of corpus-driven transformation design

**Decision**: The choice of which classification signal (htype, dc:type, or both) takes
priority in ProvidedCHO class dispatch is determined empirically from the corpus, not
assumed uniform across all sectors. Two complementary measures are computed per stratum:

1. **Coverage** — share of records carrying htype only / dc:type only / both / neither.
   Determines which signal is present and thus executable as a dispatch rule.
2. **Discriminating power** — unique value count per signal divided by total records
   in the stratum (density). The signal with higher density carries finer semantic
   granularity and warrants more detailed mapping work. The signal with the higher
   ratio is recorded as the `semantic_leader` in `dispatch_signal_ratio.csv`.

Per-sector, per-mediatype signal coverage was computed from
`output/dispatch_signal_ratio.csv` (script: `scripts/dispatch_signal_ratio.py`).
The resulting priority order — documented in `transform-revised-plan.md` §1.1 — is
the normative reference for all dispatch logic in `transform_edm_to_mocho.py`.

mt007 (NOT DIGITIZED) records are **skipped** — no mocho triples are emitted.
These records carry no `binaries.binary.@href` and were incorrectly included by
the SOLR query (the digitalisat=TRUE flag does not exclude mt007). They are
excluded from the signal analysis for the same reason.

### 1.1 **Signal coverage table** (goethe-faust corpus, 115,432 records; mt007 rows omitted):

| Sector | Mediatype | n | unique htype | unique dctype | both % | htype density % | dctype density % | Semantic leader |
|---|---|---|---|---|---|---|---|---|
| sparte001 Archive | AUDIO | 8 | 0 | 1 | 0.0 | 0.0 | 12.5 | dc:type |
| sparte001 Archive | PHOTO | 5,361 | 2 | 151 | 27.2 | 0.0 | 2.8 | dc:type |
| sparte001 Archive | TEXT | 23,109 | 4 | 16 | 99.6 | 0.0 | 0.1 | dc:type |
| sparte001 Archive | VIDEO | 7 | 0 | 1 | 0.0 | 0.0 | 14.3 | dc:type |
| sparte002 Library | AUDIO | 12 | 0 | 1 | 0.0 | 0.0 | 8.3 | dc:type |
| sparte002 Library | PHOTO | 984 | 3 | 22 | 58.7 | 0.3 | 2.2 | dc:type |
| sparte002 Library | TEXT | 28,850 | 20 | 152 | 62.7 | 0.1 | 0.5 | dc:type |
| sparte002 Library | VIDEO | 8 | 1 | 1 | 100.0 | 12.5 | 12.5 | tied |
| sparte003 Monument | PHOTO | 97 | 0 | 32 | 0.0 | 0.0 | 33.0 | dc:type |
| sparte004 Research | AUDIO | 22 | 0 | 2 | 0.0 | 0.0 | 9.1 | dc:type |
| sparte004 Research | PHOTO | 948 | 2 | 117 | 1.1 | 0.2 | 12.3 | dc:type |
| sparte004 Research | TEXT | 260 | 4 | 4 | 100.0 | 1.5 | 1.5 | tied |
| sparte005 Media Library | AUDIO | 424 | 0 | 3 | 0.0 | 0.0 | 0.7 | dc:type |
| sparte005 Media Library | PHOTO | 3,792 | 0 | 101 | 0.0 | 0.0 | 2.7 | dc:type |
| sparte005 Media Library | VIDEO | 63 | 0 | 6 | 0.0 | 0.0 | 9.5 | dc:type |
| sparte006 Museum | AUDIO | 10 | 0 | 2 | 0.0 | 0.0 | 20.0 | dc:type |
| sparte006 Museum | PHOTO | 8,972 | 0 | 406 | 0.0 | 0.0 | 4.5 | dc:type |
| sparte006 Museum | VIDEO | 18 | 0 | 2 | 0.0 | 0.0 | 11.1 | dc:type |
| sparte007 Others | PHOTO | 74 | 0 | 21 | 0.0 | 0.0 | 28.4 | dc:type |
| sparte007 Others | TEXT | 11 | 3 | 3 | 100.0 | 27.3 | 27.3 | tied |

dc:type is the semantic leader in all strata where htype is absent; where both signals are
present, dc:type still carries more unique values in every stratum except three small tied
cases. This confirms that dc:type has finer semantic granularity, but its open vocabulary
across 27M DDB objects means fixed class defaults are used in place of per-stratum dc:type
annotation for most strata (§1.2).

**Caveat — semantic drift across sectors**: dc:type density is measured *within* each
sector stratum, not globally. High density does not mean dc:type is semantically stable
across sectors: the same string can denote different WEMI levels depending on provenance
(e.g. `Druck` → `vra:Work` in sparte006 Museum, `mocho:ImageWork` in sparte005 Media
Library; `Fotografie` in sparte001 Archive suppresses the image WEMI class entirely).
A flat dc:type-only lookup would therefore be wrong even where dc:type coverage is high.
The sector dimension must be preserved in all dispatch tables derived from this analysis.
This constraint is realised in `output/config/image_type2class.json` (D11,
`transform-script-adr.md`) and in the sector-keyed rows of `output/config/lookup_htype_doco_rico.csv`.

### 1.2 **Dispatch order derived from coverage**:

| Sector | Dispatch order | dc:type scope | Notes |
|---|---|---|---|
| sparte001 Archive × AUDIO | htype first | `output/config/lookup_htype_doco_rico.csv` → `aco:AudioManifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte001 Archive × IMAGE | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:ImageManifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte001 Archive × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte001 Archive × VIDEO | htype first | `output/config/lookup_htype_doco_rico.csv` → `ec:EditorialWork` (W) + `ec:MediaResource` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte002 Library × AUDIO | htype first | `output/config/lookup_htype_doco_rico.csv` → `aco:AudioManifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte002 Library × IMAGE | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:ImageManifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte002 Library × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte002 Library × VIDEO | htype first | `output/config/lookup_htype_doco_rico.csv` → `ec:EditorialWork` (W) + `ec:MediaResource` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte003 Monument × IMAGE | dc:type only | fixed: `mocho:ImmovableWork` (W) + `mocho:ImageManifestation` (M) | htype absent; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte004 Research × AUDIO | dc:type only | `output/config/audio_type2class.json` | htype absent; dispatch based on audio config |
| sparte004 Research × IMAGE | dc:type only | fixed: `mocho:ImageManifestation` (M) | htype absent or trace (<2%); too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte004 Research × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte005 Media Library × AUDIO | dc:type only | `output/config/audio_type2class.json` → `aco:AudioManifestation` (M) | htype absent in all records |
| sparte005 Media Library × IMAGE | dc:type only | fixed: `mocho:ImageWork` (W) + `mocho:ImageManifestation` (M) | htype absent in all records; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte005 Media Library × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |
| sparte005 Media Library × VIDEO | dc:type only | fixed: `ec:EditorialWork` (W) + `ec:MediaResource` (M) | htype absent in all records |
| sparte006 Museum × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype present in some records; absent → `mocho:Manifestation` |
| sparte006 Museum × AUDIO | dc:type only | fixed: `aco:AudioManifestation` (M) | htype absent in all records |
| sparte006 Museum × IMAGE | dc:type only | fixed: `vra:Work` (W) + `vra:Image` (M) | htype absent in all records; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte006 Museum × VIDEO | dc:type only | fixed: `ec:EditorialWork` (W) + `ec:MediaResource` (M) | htype absent in all records |
| sparte007 Others × IMAGE | dc:type only | fixed: `mocho:ImageManifestation` (M) | htype absent in all records; too many unique dc:types — default to `mocho:ImageManifestation` |
| sparte007 Others × TEXT | htype first | `output/config/lookup_htype_doco_rico.csv` → `mocho:Manifestation` (M) | htype vocabulary is bounded; dc:type open and only partially observed in 115k-record snapshot of 27M DDB objects |

**Alternatives considered**:
- *Uniform htype-first*: Apply htype before dc:type for all sectors. Rejected because
  htype is absent in IMAGE and AUDIO strata across sparte003–007; htype-first would
  silently fall through for the majority of records without adding coverage.
- *Uniform dc:type-first*: Apply dc:type before htype for all sectors. Rejected because
  sparte002 TEXT has 31% htype-only records; dc:type-first leaves these unclassified.
  dc:type vocabulary across the full 27M DDB corpus is also open and only partially
  observed in this snapshot — a comprehensive dc:type dispatch table cannot be built
  from the goethe-faust corpus alone.

**Rationale**: Coverage data shows signal priority is not uniform. htype-first is applied
where htype is present and its vocabulary is bounded (sparte001, sparte002 all mediatypes;
sparte004, sparte005, sparte007 TEXT). For strata where htype is absent, dc:type mapping
is unreliable — its vocabulary is open, corpus-specific, and inconsistent across sectors.
The exception is AUDIO, where prior curation work produced a stable dc:type → class mapping
(`audio_type2class.json`). All other mediatypes (IMAGE, VIDEO, TEXT fallback) receive fixed
WEMI class defaults rather than per-value dc:type dispatch.

Generalizing dc:type dispatch beyond this corpus is not feasible without ingesting a
representative sample of the full 27M DDB objects. The goethe-faust corpus is a biased
snapshot: sector and provider distribution may not reflect the full collection, so any
dc:type mapping built here would underfit the full vocabulary and overfit to this corpus's
dominant providers. Safe generalization is only possible where the vocabulary is closed by
design — htype (expert-curated, finite) and audio dc:types (prior curation). All other
strata default to fixed structural classes until a larger, representative ingest is available.

**Source**: `output/dispatch_signal_ratio.csv`, generated by `scripts/dispatch_signal_ratio.py`.

### 1.3 **Dispatch coverage validation** (goethe-faust corpus, 115,432 records):

Run: `scripts/validate_dispatch.py` → `output/dispatch_validation.csv` (2026-05-02)

| Dispatch rule | Records |
|---|---|
| sparte002×mt003 htype-first | 28,850 |
| sparte001×mt003 htype-first | 23,109 |
| sparte006×mt002 fixed | 8,970 |
| sparte001×mt002 htype-first | 5,361 |
| sparte005×mt002 fixed | 3,792 |
| sparte002×mt002 htype-first | 984 |
| sparte004×mt002 fixed | 948 |
| sparte005×mt001 audio_type2class | 424 |
| sparte004×mt003 htype-first | 260 |
| sparte003×mt002 fixed | 97 |
| sparte007×mt002 fixed | 74 |
| sparte005×mt005 fixed | 63 |
| sparte004×mt001 audio_type2class | 22 |
| sparte006×mt005 fixed | 18 |
| sparte002×mt001 htype-first | 12 |
| sparte007×mt003 htype-first | 11 |
| sparte006×mt001 fixed | 10 |
| sparte001×mt001 htype-first | 8 |
| sparte002×mt005 htype-first | 8 |
| sparte001×mt005 htype-first | 7 |
| sparte005×mt003 htype-first | 2 |
| **fallback D9** | **42** |
| mt007 skipped | 42,360 |

**mt007 skipped by sector**:

| Sector | mt007 records |
|---|---|
| sparte001 Archive | 21,745 |
| sparte002 Library | 20,335 |
| sparte006 Museum | 203 |
| sparte004 Research | 53 |
| sparte003 Monument | 15 |
| sparte005 Media Library | 9 |
| **Total** | **42,360** |

**Total dispatched**: 73,072 records (63.3% of corpus). **Fallback D9**: 42 records — likely sparte006 Museum TEXT and other small unaccounted strata not yet in §1.2. To investigate: run `grep dispatch_rule=fallback output/dispatch_validation.csv` to identify the sector × mediatype combinations.

---


## Decision 11: PROV-O provenance triples — provenance chain for DDB items

**Decision**: The transform emits PROV-O triples to record the provenance chain
from source dataset record to published DDB item. The pattern is drawn directly
from the `friedrich-schiller-test-primary.nt` reference file (lines cited below).

**Turtle pattern** (prefixes: `prov:` = `http://www.w3.org/ns/prov#`):

```turtle
@prefix prov:    <http://www.w3.org/ns/prov#> .
@prefix ddb:     <http://www.deutsche-digitale-bibliothek.de/> .
@prefix ddbkg:   <http://ddbkg.fiz-karlsruhe.de/> .
@prefix ddb-api: <https://api.deutsche-digitale-bibliothek.de/2/> .

# ── Agents ───────────────────────────────────────────────────────────────────

ddb:organization/2Q37XY5KXJNJE5MV6SWP3UKKZ6RSBLK5   # line 43
    a prov:Agent .                                    # (Deutsche Nationalbibliothek)

ddb:                                                  # line 98
    a prov:Agent .                                    # (Deutsche Digitale Bibliothek)

ddbkg:AACZRV7UQ859A7M7DD033EYKACNF380D               # line 87
    a prov:SoftwareAgent ;                            # (XSLT pipeline)
    prov:actedOnBehalfOf ddb: .                       # line 95

# ── Source entity (raw dataset record) ───────────────────────────────────────

ddbkg:DDKEKROKIWI1KBAOG0CN258P8AXKXCC7               # line 61
    a prov:Entity ;
    prov:wasAttributedTo                              # line 37
        ddb:organization/2Q37XY5KXJNJE5MV6SWP3UKKZ6RSBLK5 .

# ── Derived entity (DDB item) ─────────────────────────────────────────────────

ddb:item/IBDPENVFMNA4TNCNMJ772MB72RITSYMK            # line 76
    a prov:Entity ;
    prov:wasDerivedFrom                               # JSON: source.description.href
        ddb-api:items/222NZKK63TNRLC2VETRV722VKBDSUVGL/source/record ;
    prov:wasAttributedTo                              # line 55
        ddbkg:AACZRV7UQ859A7M7DD033EYKACNF380D ;
    prov:generatedAtTime                              # line 35
        "2021-08-04T12:05:02+0200" .

# ── Source record description (binaries.binary[i], one block per entry) ──────

ddb-api:items/222NZKK63TNRLC2VETRV722VKBDSUVGL/source/record
    a prov:Entity ;
    dc:identifier "0ac6ad6e-a985-4251-91ca-f4b918326ead" ;  # binaries.binary[i].ref
    dc:title      "Abb. Vorsatz. Titelblatt auf fliegendem Blatt..."@de ;  # binaries.binary[i].name
    dc:description "Urheber*in: DDZ (Fotografische Aufnahme) | Abb. Vorsatz. Titelblatt..."@de ;
        # concat(ifnull(binaries.binary[i].name2, ""), " | ", ifnull(binaries.binary[i].name, ""))
    dcterms:rights <http://rightsstatements.org/vocab/InC/1.0/> ;  # binaries.binary[i].kind (URI)
    dcterms:source <http://fotothek.slub-dresden.de/fotos/df_pos-2018-a_0000067_000_f.jpg> .
        # binaries.binary[i].local_pathname (URI)
```

**`prov:wasDerivedFrom` mapping**: the object is constructed from
`source.description.href` (e.g. `/items/222NZKK…/source/record`) by stripping
the leading `/` and prepending `ddb-api:`.

**Source record description mapping** (`binaries.binary[i]`, one set of triples per binary entry):

| Triple predicate | JSON path | Value type | Notes |
|---|---|---|---|
| `dc:identifier` | `binaries.binary[i].ref` | string literal | UUID of the binary |
| `dc:title` | `binaries.binary[i].name` | `@de` literal | human-readable label |
| `dc:description` | concat(`name2`, `" \| "`, `name`) | `@de` literal | nulls replaced with `""` |
| `dcterms:rights` | `binaries.binary[i].kind` | URI | rights statement URI |
| `dcterms:source` | `binaries.binary[i].local_pathname` | URI | file or IIIF URL |

**Provenance chain**: the DDB item (`prov:Entity`) is derived from its upstream
source record via the DDB API (`prov:wasDerivedFrom`); attributed to the XSLT
pipeline (`prov:SoftwareAgent`) acting on behalf of DDB (`prov:actedOnBehalfOf`);
timestamped at ingest (`prov:generatedAtTime`). The source record itself is
described with DC/DCTerms properties drawn from `binaries.binary[]`.

**PROV-O terms used**:

| Term | Type | Role |
|---|---|---|
| `prov:Agent` | class | institution (DNB, DDB) |
| `prov:SoftwareAgent` | class | XSLT transform pipeline |
| `prov:Entity` | class | source record and derived DDB item |
| `prov:wasAttributedTo` | property | links entity to responsible agent |
| `prov:wasDerivedFrom` | property | links DDB item to source record |
| `prov:actedOnBehalfOf` | property | links SoftwareAgent to owning institution |
| `prov:generatedAtTime` | property | ingest timestamp on DDB item |

**Source**: `data/friedrich-schiller-test-primary.nt`, lines 35, 37, 43, 49, 55, 61, 76, 87, 95, 98.

---

## Decision 12: PROV-O full JSON field mapping — nodes, URIs, and triples

**Decision**: The transform emits a complete PROV-O graph for each item using the
Without-Activity pattern (slide 13 of `references/rm-018-prov-o.pdf`). Node URIs
follow a `urn:ddbedm:` convention that traces each identifier back to its JSON key
chain, making the source unambiguous without requiring a dereferenceable endpoint.

### URI scheme

| Node | URI pattern | JSON source |
|---|---|---|
| CHO | `ddb:item/<id>` | `properties.item-id` |
| Dataset | `urn:ddbedm:properties:dataset-id:<id>` | `properties.dataset-id` |
| XSLT | `urn:ddbedm:properties:mapping-version:<ver>` | `properties.mapping-version` |
| Provider | `urn:ddbedm:provider-info:provider-ddb-id:<id>` | `provider-info.provider-ddb-id` |
| DDB | `<http://www.deutsche-digitale-bibliothek.de>` | fixed |

### Turtle pattern

```turtle
@prefix prov:     <http://www.w3.org/ns/prov#> .
@prefix ddb:      <http://www.deutsche-digitale-bibliothek.de/> .
@prefix dcat:     <http://www.w3.org/ns/dcat#> .
@prefix dcterms:  <http://purl.org/dc/terms/> .
@prefix foaf:     <http://xmlns.com/foaf/0.1/> .
@prefix rdfs:     <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema:   <https://schema.org/> .
@prefix lov:      <http://www.w3.org/ns/iana/media-types/> .

# ── CHO ──────────────────────────────────────────────────────────────────────

ddb:item/222NZKK63TNRLC2VETRV722VKBDSUVGL          # properties.item-id
    a prov:Entity ;
    prov:wasDerivedFrom
        <urn:ddbedm:properties:dataset-id:76409877634279609sQOu> ;  # properties.dataset-id
    prov:wasAttributedTo
        <urn:ddbedm:properties:mapping-version:6.18> ;              # properties.mapping-version
    prov:generatedAtTime "2026-01-07T15:40:43+0100" ;               # properties.ingest-date
    dcterms:hasVersion   "43" ;                                      # properties.revision-id
    dcterms:references   "ddb:222NZKK63TNRLC2VETRV722VKBDSUVGL" .  # source.description.record.ref

# ── Dataset ───────────────────────────────────────────────────────────────────

<urn:ddbedm:properties:dataset-id:76409877634279609sQOu>
    a dcat:Dataset, prov:Entity ;
    dcterms:identifier "76409877634279609sQOu" ;                     # properties.dataset-id
    rdfs:label         "Gesamtlieferung: Deutsche Fotothek - LIDO"@de ;  # properties.dataset-label
    dcterms:type       <http://www.lido-schema.org/> ;               # source.description.record.type
    prov:wasAttributedTo
        <urn:ddbedm:provider-info:provider-ddb-id:CJY7MSLPOPB7FTPC7JM5K2GGM5PBGLYI> .

# ── XSLT SoftwareAgent ────────────────────────────────────────────────────────

<urn:ddbedm:properties:mapping-version:6.18>
    a prov:SoftwareAgent ;
    dcterms:hasVersion "6.18" ;                                      # properties.mapping-version
    prov:actedOnBehalfOf <http://www.deutsche-digitale-bibliothek.de> .

# ── DDB Agent ─────────────────────────────────────────────────────────────────

<http://www.deutsche-digitale-bibliothek.de>
    a prov:Agent, foaf:Organization ;
    foaf:name "Deutsche Digitale Bibliothek" .

# ── Provider Agent ────────────────────────────────────────────────────────────

<urn:ddbedm:provider-info:provider-ddb-id:CJY7MSLPOPB7FTPC7JM5K2GGM5PBGLYI>
    a prov:Agent, foaf:Organization ;
    foaf:name        "Deutsche Fotothek" ;                           # provider-info.provider-name
    schema:url       <http://www.deutschefotothek.de> ;              # provider-info.provider-uri
    dcterms:identifier "99900890" ;                                  # provider-info.provider-id
    lov:isil         <http://ld.zdb-services.de/resource/organisations/DE-2396> .  # provider-info.provider-isil
```

### Field mapping table

**CHO** (`ddb:item/<properties.item-id>`):

| Triple | JSON path | Value type |
|---|---|---|
| `prov:wasDerivedFrom` | `properties.dataset-id` → Dataset URN | URN |
| `prov:wasAttributedTo` | `properties.mapping-version` → XSLT URN | URN |
| `prov:generatedAtTime` | `properties.ingest-date` | xsd:dateTime literal |
| `dcterms:hasVersion` | `properties.revision-id` | string literal |
| `dcterms:references` | `source.description.record.ref` | `"ddb:<ref>"` literal |

**Dataset** (`urn:ddbedm:properties:dataset-id:<value>`):

| Triple | JSON path | Value type |
|---|---|---|
| `dcterms:identifier` | `properties.dataset-id` | string literal |
| `rdfs:label` | `properties.dataset-label` | `@de` literal |
| `dcterms:type` | `source.description.record.type` | URI |
| `prov:wasAttributedTo` | `provider-info.provider-ddb-id` → Provider URN | URN |

**XSLT** (`urn:ddbedm:properties:mapping-version:<value>`):

| Triple | JSON path | Value type |
|---|---|---|
| `dcterms:hasVersion` | `properties.mapping-version` | string literal |
| `prov:actedOnBehalfOf` | fixed: `<http://www.deutsche-digitale-bibliothek.de>` | URI |

**Provider** (`urn:ddbedm:provider-info:provider-ddb-id:<value>`):

| Triple | JSON path | Value type |
|---|---|---|
| `foaf:name` | `provider-info.provider-name` | string literal |
| `schema:url` | `provider-info.provider-uri` | URI |
| `dcterms:identifier` | `provider-info.provider-id` | string literal |
| `lov:isil` | `provider-info.provider-isil` | URI |

**Skipped**: `properties.cortex-type`, `properties.automatically-translated`, `provider-info.provider-parent-id` — not modelled in the PROV-O graph.

**Pattern choice**: Without-Activity pattern (slide 13) selected over the full Activity pattern (slide 12). The Activity node adds `prov:wasGeneratedBy` + `prov:used` but requires a blank node or per-item IRI for the Ingest activity; the added expressivity is not needed for the current use case.

---

## D13 — Non-EDM fields: mocho:mimeType from binaries.binary[].mimetype

### Field location

`binaries.binary[].mimetype` is a DDB JSON API field, **not** present in the EDM RDF
graph. It appears on binary/digital-file entries attached to each item record:

```json
"binaries": {
  "binary": [
    {
      "ref": "0ac6ad6e-...",
      "mimetype": "image/jpeg",
      "primary": true,
      "local_pathname": "http://fotothek.slub-dresden.de/fotos/...",
      ...
    }
  ]
}
```

`binaries.binary` may be `null` for items with no digital files.

### Decision

Emit `mocho:mimeType` as a DataProperty triple on the WebResource subject:

```turtle
<webresource-uri>  mocho:mimeType  "image/jpeg" .
```

The WebResource URI is derived from `binaries.binary[].local_pathname` (or
`binaries.binary[].ref` if pathname is absent). Only the primary binary
(`primary: true`) is emitted per record. If `binaries.binary` is null or empty,
no triple is emitted.

`mocho:mimeType` is distinct from `mocho:mediaType`:
- `mocho:mediaType` → vocnet-mtype concept IRI (`vocnet-mtype:mt002`) — semantic category
- `mocho:mimeType` → string literal (`"image/jpeg"`) — technical format identifier

### Non-EDM fields reference table

Fields in the DDB JSON API that are **not** present in the EDM RDF graph and require
transform-side bridging:

| JSON path | mocho property | Type | Notes |
|---|---|---|---|
| `binaries.binary[].mimetype` | `mocho:mimeType` | DataProperty / xsd:string | On WebResource; primary binary only; null if no digital file |
| `edm.RDF.Agent[].type.resource` (sector) | `mocho:sector` | ObjectProperty / skos:Concept | Resolved via `provider-info.domains` lookup; see D9 |
| `edm.RDF.Concept[].about` (mediatype) | `mocho:mediaType` | ObjectProperty / skos:Concept | From flat Concept list in `edm.RDF.Concept[]`; see D9 |

---

## D15 — Output format: N-Quads; mocho-graph CHO URI minting

**Decision**: Output is N-Quads (`.nq`), not N-Triples. Each line includes the named graph IRI as the fourth element. In the `mocho` named graph, each ProvidedCHO is assigned a minted GeMeA URI instead of the original DDB URI:

```
https://gemea.ise.fiz-karlsruhe.de/mocho/<object_id>
```

A `owl:sameAs` triple links the minted URI to the original DDB URI and is emitted in the `mocho` graph:

```
<https://gemea.ise.fiz-karlsruhe.de/mocho/<object_id>>  owl:sameAs  <original-ddb-uri> .
```

URI minting applies to **ProvidedCHO only**. Agent, Place, and WebResource nodes retain their original URIs (GND URIs, DDB place URIs, provider URLs).

**Rationale**:
- N-Quads are required to carry named graph provenance in a single file stream — QLever and most SPARQL 1.1 stores ingest named graphs from N-Quads natively.
- Minting a GeMeA URI for the CHO separates the enriched mocho view from the raw DDB-EDM view. The `ddbedm` graph retains the original DDB URI as subject; the `mocho` graph uses the minted URI. `owl:sameAs` bridges the two.
- Minting Agent/Place/WebResource URIs was considered and rejected: GND URIs are authoritative cross-record join keys used by Phase 1b enrichment; re-minting them would require additional `owl:sameAs` triples per entity per record (~3–4× more at 27M-record scale) with no retrieval benefit.

**Implementation**: `get_object_id()` already extracts the 32-char object ID. The minted URI is constructed as `GEMEA_BASE + object_id`. All mocho-graph triples for the CHO use the minted URI as subject.

---

## D14 — PhysicalThing.hierarchyType: deferred

**Decision**: `retype_cho()` handles `ProvidedCHO` only. `PhysicalThing` entities (55,771 records, 48.3% coverage) also carry `hierarchyType` but are not retyped in this pass.

**Rationale**: PhysicalThing is an EDM modelling artefact for physical carrier description; its rdf:type mapping requires a separate alignment decision (likely CIDOC-CRM or LRMoo). Skipping it does not affect the correctness of the ProvidedCHO output.

**Consequence**: `PhysicalThing.hierarchyType` will appear in the unmatched keys stats. This is expected and documented.

---

## D17 — Work URI minting and WEMI link pattern

**Decision**: When class dispatch assigns a W-slot class (`rdac:C10001`, `mo:MusicalWork`), the Work entity in the KG is represented by a minted GeMeA Work URI — not by the GND authority URI directly. The WEMI link pattern is:

```turtle
<gemea-work-uri>  a rdac:C10001 .
<gemea-work-uri>  mocho:hasManifestation  <gemea-cho-uri> .
<gemea-work-uri>  skos:exactMatch         <gnd-uri> .       # only when GND lookup succeeds
<gemea-cho-uri>   mocho:isManifestationOf <gemea-work-uri> .
```

Work URI scheme (parallel to CHO minting in D15): `https://gemea.ise.fiz-karlsruhe.de/work/<id>`, where `<id>` is a deterministic hash of the lookup key `(dc:title, dc:creator)` used by `link_gnd_works.py`. When GND lookup fails, `skos:exactMatch` is omitted; the Work URI is still minted and linked to the Manifestation, leaving a stub node for future enrichment.

**Pipeline responsibility**: `transform_edm_to_mocho.py` cannot emit the WEMI link in Phase 0 — the Work URI does not exist until `link_gnd_works.py` resolves it. The transform writes the staging row to DuckDB (D26, `transform-script-adr.md`); `link_gnd_works.py` mints `<gemea-work-uri>`, runs the GND Werk lookup, and writes all Work-related triples into `graph/work`.

**Alternatives considered**:

*Use the GND URI directly as the Work node*: Emit `<gemea-cho-uri> mocho:isManifestationOf <gnd-uri>` and assert `<gnd-uri> a rdac:Work`. Rejected for three reasons:
1. **Type system conflation**: GND has its own entity model (`gndo:Work`, `gndo:MusicalWork`). Asserting `rdac:Work` on the same URI mixes two type systems on an external resource GeMeA does not own. Future enrichment from lobid-gnd adds triples to the same node under GND's schema, creating a semantically ambiguous entity.
2. **GND lookup failure leaves no Work node**: If `link_gnd_works.py` finds no GND match, the WEMI link cannot be emitted at all — no stub node is created, and the W-slot class dispatch result is lost from the KG.
3. **Deduplication fragility**: Multiple Manifestations of the same Werk share the same GND URI, but the deduplication key (`dc:title` + `dc:creator`) may produce slightly different surface forms across records. A minted URI derived from the normalized key is stable regardless of which surface form wins.

*Use `owl:sameAs` instead of `skos:exactMatch`*: `owl:sameAs` asserts full identity — all properties of `<gnd-uri>` are inherited by `<gemea-work-uri>` and vice versa under OWL semantics. This is too strong: GND's `gndo:MusicalWork` type and its bibliographic properties would be inferred on the GeMeA Work entity. `skos:exactMatch` expresses "the same concept in a different vocabulary" without triggering OWL identity closure.

**Named graph**: All Work entity triples and `mocho:isManifestationOf` links are written into `graph/work` by `link_gnd_works.py`. The `graph/mocho` stream (written by `transform_edm_to_mocho.py`) contains only Manifestation-level triples; no `mocho:isManifestationOf` triple is emitted there. This separation allows `graph/work` to be regenerated independently if the GND lookup strategy changes.
