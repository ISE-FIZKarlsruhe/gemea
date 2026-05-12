# ADR: EDM → mocho Property Mapping Decisions

**Date**: 2026-05-02
**Status**: In progress
**Related**: `transform-adr.md` (class dispatch), `transform-script-adr.md` (implementation), `transform-props-mapping-plan.md` (full property mapping catalogue)

---

## Context

This document records decisions about **which predicate to emit** for each EDM/DC property in `transform_edm_to_mocho.py`. Class-assignment decisions (rdf:type dispatch, htype lookup, mediatype dispatch) are in `transform-adr.md` and `transform-script-adr.md`. The decisions here govern property-level choices: which RDA/RiC-O/VRA/vocab predicate replaces or accompanies the source DC/EDM predicate, and when the source predicate is kept or skipped.

All ProvidedCHOs are typed as `mocho:Manifestation` (D9, `transform-script-adr.md`). Target predicates for Manifestation-level properties use `rdam:` where a Manifestation-specific RDA property exists. For Work-level nodes produced by W+M dispatch, `rdaw:` properties are used.

---

## Decision 1: Subject keys — IRI correction and value-type dispatch

*(Moved from `transform-script-adr.md` D6)*

**Decision**: Three JSON keys carry subject data: `dcSubject`, `dcTermsSubject`, `dcTermSubject`. These are handled by a dedicated `emit_subject_triples()` function, not the generic alignment loop.

**Background**: Corpus inspection revealed that `dcTermsSubject` was incorrectly mapped to `dc:subject` (`http://purl.org/dc/elements/1.1/subject`) in `alignment_ddbedm_mocho.csv`. The correct IRI is `dcterms:subject` (`http://purl.org/dc/terms/subject`). This was a derivation error in the alignment script's IRI resolution step; `dcTermSubject` (note: missing `s`) was correctly resolved to `dcterms:subject` via an explicit `OVERRIDES` entry in `align_ddbedm_to_mocho.py`. The fix was applied directly to the CSV (42 rows: `edm_prefix` `dc→dcterms`, `edm_iri` corrected).

**Dispatch logic**:
- Literal value (string or lang-tagged text) → `dc:subject "string"@lang` — uncontrolled annotation; no concept node minted.
- IRI value (`{"resource": ..., "$": label}`) → two triples:
  1. `<cho> dcterms:subject <concept-uri>`
  2. `<concept-uri> rdfs:label "label"@lang` — concept stub (label from `$` field; language from `@language` if present)

**Deduplication**: `emit_subject_triples()` collects values from all three keys and deduplicates `(pred_nt, obj_nt)` pairs in a per-record set before writing. This prevents duplicate triples when the same value appears under multiple keys (occurs in ~60% of records). Concept stubs (`rdfs:label`) are also deduplicated per record — one label triple per URI.

**Rationale**: The IRI + label stub pattern is strictly better for SPARQL retrieval than a literal-only approach:
- URI equality tests are index lookups; string matching is a scan with normalization risk.
- One `rdfs:label` triple per concept URI allows cross-record deduplication for faceting: `GROUP BY ?c ?label COUNT(?cho)` without string normalization.
- Future enrichment (`skos:broader`, `skos:altLabel`, `owl:sameAs` to GND/LCSH) attaches to the concept node without touching CHO triples.

`dc:subject` is kept for literal-only values as a fallback annotation, signalling "unresolved, no authority URI". This keeps literal subjects queryable without mixing literal and IRI objects under `dcterms:subject`.

---

## Decision 2: Creator → rdam:P30329 "has creator agent of manifestation"

*(Moved from `transform-script-adr.md` D7; IRI corrected from P30263 → P30329)*

**Decision**: `dc:creator` (json_key: `creator`) is mapped to `rdam:P30329` "has creator agent of manifestation" (`http://rdaregistry.info/Elements/m/P30329`). The alignment table's 464 Work-level candidates are bypassed.

**IRI correction**: The earlier decision (D7 in `transform-script-adr.md`) incorrectly cited `rdam:P30263`. The RDA properties CSV (`mocho/output/rda_properties_rda-5.4.9.csv`) confirms `P30263` is "has reduction ratio designation" — unrelated to creator. The correct Manifestation-level generic creator property is `rdam:P30329` "has creator agent of manifestation". This correction must also be applied in `transform_edm_to_mocho.py`.

**Background**: The alignment table produces 464 candidates for `creator`, all at the Work WEMI level — including highly specific properties such as "has production company", "has plaintiff corporate body". These are correct sub-properties of Work-level creator properties but wrong for a generic `dc:creator` value where the role is unknown. The WEMI level is determined by D9 (`transform-script-adr.md`): all ProvidedCHOs are `mocho:Manifestation` → creator property must be Manifestation-level.

**Typed subproperties** (Phase 1b): `rdam:P30363` (person), `rdam:P30392` (collective agent), `rdam:P30421` (corporate body), `rdam:P30450` (family) are the typed subproperties of `rdam:P30329`. These are the Phase 1b resolution path once GND agent type is resolved by `link_gnd_agents.py`.

**Alternatives considered**:
- *Emit all 464*: Semantically noisy; a Goethe letter would assert "has plaintiff corporate body" for the author. Rejected.
- *Use rdaw:P10065 has creator agent of work*: Work-level; inconsistent with D9. Rejected.
- *Mediatype dispatch*: Correct role remains unknown even with mediatype. Rejected for POC.

**Open**: D7 does not specify whether a GND URI in `resource` should cause an `edm:Agent` node to be minted and linked, or whether a plain literal is emitted. Phase 1b GND enrichment is the intended resolution path.

---

## Decision 3: Contributor — LIDO event type dispatch

*(Supersedes earlier draft: "keep dc:contributor". Prior rationale: no generic RDA contributor property exists; alignment table candidates were all role-specific or wrong WEMI level.)*

**Decision**: The specific predicate emitted for a `dc:contributor` value is determined by the LIDO event type of the `edm:Event` in which the contributor's Agent URI participates. Resolution chain:

```
ProvidedCHO.hasMet[].resource  →  edm:Event.about
edm:Event.hasType.resource     →  LIDO event type URI
edm:Event.P11_had_participant[].resource  ==  contributor[].resource
→  emit <cho> <target_prop> <contributor.resource>
```

If no matching Event is found (contributor URI absent from any Event.P11_had_participant, or contributor is label-only), fall back to `dc:contributor`.

**Corpus evidence** (`data/items-excerpt-1000.json`, 519 contributor values):
- URI match → DDB org / GND: 325 (62.6%) — reliable join key
- Label match → DDB org / GND: 174 (33.5%) — lower than creator; label is not a reliable fallback

See `notes/corpus-analysis.md §1b` and `data/analysis/contributor_agent_coverage.csv`.

**LIDO event type → target predicate dispatch** (`output/config/lido_event_types.csv`):

| LIDO event type | label | rdam_prop (M) | rdaw_prop (W) | vra_image | vra_work | rico_prop |
|---|---|---|---|---|---|---|
| lido00012, eventType/creation | creation | `rdam:P30329` | `rdaw:P10065` | `vra:creator` | `vra:creator` | `rico:hasCreator` |
| lido00228, eventType/publication | publication | `rdam:P30083` | `dc:contributor` | `dc:contributor` | `dc:contributor` | `rico:hasPublisher` |
| lido00007 | production | `rdam:P30081` | `dc:contributor` | `vra:producer` | `vra:producer` | `dc:contributor` |
| lido01127 | photography | `rdam:P30329` | `rdaw:P10056` | `vra:photographer` | `vra:photographer` | `dc:contributor` |
| lido00224 | designing | `dc:contributor` | `rdaw:P10051` | `vra:designer` | `vra:designer` | `dc:contributor` |
| lido00226 | commissioning | `dc:contributor` | `rdaw:P10287` | `dc:contributor` | `dc:contributor` | `dc:contributor` |
| lido00003 | unknown_event | `dc:contributor` | `dc:contributor` | `dc:contributor` | `dc:contributor` | `dc:contributor` |
| all others | — | `dc:contributor` | `dc:contributor` | `dc:contributor` | `dc:contributor` | `dc:contributor` |

For aco, mo, doco, ec, and mocho subclasses: `dc:contributor` in all rows (no role-specific property in those vocabularies).

**RDA property notes**:
- `rdam:P30329` "has creator agent of manifestation" — used for photography as well as creation: no Manifestation-level photographer property exists in RDA; the photographer is the creator agent of the photographic manifestation.
- `rdam:P30081` "has producer agent of unpublished manifestation" — covers fabricated/inscribed artifacts (manuscripts, prints, artworks); no W-level RDA production-agent property exists, hence `dc:contributor` fallback for `rdaw_prop`.
- `rdam:P30083` "has publisher agent" — publication is inherently Manifestation-level in RDA; no W-level equivalent, hence `dc:contributor` fallback.
- `rdaw:P10051` "has designer agent", `rdaw:P10287` "has commissioning agent" — W-level only; no M-level equivalents, hence `dc:contributor` for `rdam_prop`.
- `rdaw:P10056` "has photographer agent of work" — W-level specific property for photography.

**Source**: `output/config/lido_event_types.csv`; RDA labels verified from `mocho/output/rda_properties_rda-5.4.9.csv`; VRA properties from `mocho/output/mapping_vra_to_rda.csv`; RiC-O properties from `mocho/output/mapping_rico_to_rda.csv`.

---

## Decision 4: dc:title — dual-emit with class-specific title predicate

**Decision**: For every `dc:title` value, two triples are emitted: `dc:title` (universal cross-WEMI handle) and a class-specific title predicate determined by the target class of the node. The class-specific predicate is looked up from `output/config/lookup_class_prop_alignment.csv` (columns: `edm_class, target_class, wemi, edm_prop, target_prop`). When `target_prop == edm_prop`, only `dc:title` is emitted.

**Class-specific predicates for dc:title**:

| target_class | wemi | target_prop | Notes |
|---|---|---|---|
| `rdac:C10007`, `mocho:Manifestation` | M | `rdam:P30134` "has title of manifestation" | rdac classes only |
| `rdac:C10001`, `mocho:ImmovableWork`, `mocho:ImageWork`, `ec:EditorialWork` | W | `rdaw:P10088` "has title of work" | rdac-derived mocho classes |
| `vra:Image`, `vra:Work` | M/W | `vra:title` | VRA Core has its own title property |
| `rico:RecordSet`, `rico:Record`, `rico:RecordPart` | — | `rico:hasOrHadTitle` | RiC-O; WEMI not applicable |
| `aco:AudioManifestation`, `mocho:ImageManifestation`, `mo:Musical*`, `ec:MediaResource`, `doco:*` | M | `dc:title` | No vocab-specific title property; dc:title only |

**Rationale**: `dc:title` is declared `rdfs:subPropertyOf dct:title` in mocho's RDA→DCT map (`mapRDA2DCT.ttl`), and `rdam:P30134` / `rdaw:P10088` are declared `rdfs:subPropertyOf dct:title` in the same file. QLever has no OWL reasoner, so the entailment is not materialized from subPropertyOf chains — dual-emit at ingest time is the practical approach. `dc:title` serves as the cross-WEMI query handle; the class-specific property serves WEMI-aware consumers.

**W+M nodes**: For W+M assignments (e.g. `rdac:C10001` + `rdac:C10007` from sparte002 Library htype dispatch), `dc:title` + `rdaw:P10088` goes on the Work node; `dc:title` + `rdam:P30134` goes on the Manifestation node — both derived from the same source `dc:title` value.

**Source**: `output/config/lookup_class_prop_alignment.csv` — currently populated for `dc:title`; extended as other properties are decided.

---

## Decision 5: Five predicate remappings — DC/EDM → RDA Manifestation-level

**Decision**: The following five source predicates are replaced by their Manifestation-level RDA equivalents. Source rationale: `mocho/output/mapping_dct_to_rda.csv` provides the DC → RDA sub-property mapping; Manifestation-level (`rdam:`) properties are selected per D9.

| json_key | Source predicate | Target predicate | Label |
|---|---|---|---|
| `title` | `dc:title` | `rdam:P30134` | "has title of manifestation" |
| `description` | `dc:description` | `rdam:P30137` | "has note on manifestation" |
| `date` | `dc:date` | `rdam:P30278` | "has date of manifestation" |
| `issued` | `dc:issued` | `rdam:P30278` | "has date of manifestation" (same as `date`) |
| `isPartOf` | `dcterms:isPartOf` | `rdam:P30020` | "is part of manifestation" |

Note: `dc:title` dual-emit is governed by D4. For `description`, `date`, `issued`, `isPartOf` the source predicate is replaced, not dual-emitted — these do not have the cross-WEMI querying motivation that title has.

---

## Decision 8: dcterms:alternative — class-specific variant title dispatch

**Decision**: `dcterms:alternative` is mapped per target class:

| target_class | wemi | target_prop |
|---|---|---|
| `rdac:C10007`, `mocho:Manifestation` | M | `rdam:P30128` "has variant title of manifestation" |
| `rdac:C10001` | W | `rdaw:P10086` "has variant title of work" |
| all others | — | `dcterms:alternative` (keep as-is) |

**Rationale**: `rdam:P30128` is the correct Manifestation-level variant title property; `rdam:P30131` "has abbreviated title" is too narrow — it implies a formally abbreviated form (e.g. acronym), not a generic alternative title. `rdaw:P10086` is the Work-level parallel. No equivalent found in VRA, RiC-O, MO, or ACO; those classes keep the source predicate. RiC-O uses `rico:hasOrHadTitle` for all title types distinguished by `rico:hasTitleType`, but emitting the same predicate for both main title and alternative would conflate them without type context — keeping `dcterms:alternative` is the safer fallback.

**Closes open question from D5.**

**Source**: `output/config/lookup_class_prop_alignment.csv` (dcterms:alternative rows).

---

## Decision 9: dc:date and dc:issued — class-specific date predicate dispatch

**Decision**: `dc:date` and `dc:issued` are mapped per target class via `output/config/lookup_class_prop_alignment.csv`. The mapping is:

| target_class | wemi | dc:date | dc:issued |
|---|---|---|---|
| `rdac:C10007`, `mocho:Manifestation` | M | `rdam:P30278` "has date of manifestation" | `rdam:P30011` "has date of publication" |
| `mocho:ImageManifestation`, `mocho:ImmovableWork`, `mocho:ImageWork` | M/W | `dc:date` | `dc:issued` |
| `rdac:C10001` | W | `rdaw:P10219` "has date of work" | N/A |
| `aco:AudioManifestation`, `mo:MusicalManifestation`, `mo:MusicalWork` | M/W | `dc:date` | `dc:issued` |
| `doco:*`, `ec:MediaResource`, `ec:EditorialWork` | M/W | `dc:date` | `dc:issued` |
| `vra:Image` | M | `vra:dateCreated` | `dc:issued` |
| `vra:Work` | W | `vra:dateCreated` | N/A |
| `rico:RecordSet`, `rico:Record`, `rico:RecordPart` | — | `rico:creationDate` | `rico:publicationDate` |

**N/A** rows are not emitted — no meaningful publication date applies at Work level in RDA or VRA.

**Rationale**:
- `rdam:P30278` and `rdam:P30011` are the correct Manifestation-level RDA properties. `dc:date` is a generic date; `dc:issued` specifically denotes publication — `rdam:P30011` "has date of publication" captures this distinction.
- `rdac:C10001` Work nodes receive `rdaw:P10219` "has date of work" for `dc:date`; `dc:issued` is not applicable at Work level.
- VRA classes use `vra:dateCreated` (approximate to `rdaw:P10219`; confirmed in `mapping_vra_to_rda.csv`).
- RiC-O classes use `rico:creationDate` / `rico:publicationDate` — native archival date properties with no RDA equivalent (confirmed "no match" in `mapping_rico_to_rda.csv`).
- All other classes (aco, mo, doco, ec, mocho subclasses) keep the source predicate — no date property exists in their respective vocabularies.

**Closes open question from D5.**

**Source**: `output/config/lookup_class_prop_alignment.csv` (dc:date and dc:issued rows).

---

## Decision 7: Creator URI resolution — emit dcterms:creator when Agent is DDB org or GND

**Decision**: When a `ProvidedCHO.creator` value resolves to an `edm:Agent` whose
`about` URI is a DDB organization URI (`http://www.deutsche-digitale-bibliothek.de/organization/…`)
or a GND URI (`http://d-nb.info/gnd/…`), emit:

```turtle
<cho> dcterms:creator <agent.about> .
```

Resolution is attempted in two steps, in priority order:

1. **URI match**: `creator[].resource` == `agent[].about` — direct IRI equality.
2. **Label match**: `creator[].$` matched against any `agent[].prefLabel[].$` after
   comma-order normalization (`"Lastname, Firstname"` ↔ `"Firstname Lastname"`).
   Applied only when step 1 fails or `creator[].resource` is absent.

If neither step resolves, fall back to the `rdam:P30329` plain-literal path (D2).

**Corpus evidence** (`data/items-excerpt-1000.json`, 488 creator values):
- URI match → DDB org / GND: 301 (61.7%)
- Label match → DDB org / GND (after normalization): 269 (55.1%)

See `notes/corpus-analysis.md §1` and `data/analysis/creator_agent_coverage.csv`.

**Rationale**: An IRI-valued `dcterms:creator` provides a stable node for GND/VIAF/Wikidata `owl:sameAs` alignment and a target for Phase 1b `rdaa:` property attachment (`link_gnd_agents.py`), without requiring a separate reconciliation step. `dcterms:creator` is used for the IRI triple (agent as entity); `rdam:P30329` is reserved for the Manifestation-scoped plain-literal fallback (D2).

**Closes open question from D2.**

---

## Decision 10: dc:description — class-specific note predicate dispatch

**Decision**: `dc:description` is mapped per target class via `output/config/lookup_class_prop_alignment.csv`:

| target_class | wemi | target_prop |
|---|---|---|
| `rdac:C10007`, `mocho:Manifestation` | M | `rdam:P30137` "has note on manifestation" |
| `rdac:C10001` | W | `rdaw:P10330` "has note on work" |
| `vra:Image`, `vra:Work` | M/W | `vra:description` |
| `rico:RecordSet`, `rico:Record`, `rico:RecordPart` | — | `rico:note` |
| all others (aco, mo, doco, ec, mocho subclasses) | M/W | `dc:description` |

**Rationale**:
- `rdam:P30137` is the most generic Manifestation-level note property in `mapping_dct_to_rda.csv`; no narrower property (e.g. `rdaw:P10109` "has summary") is warranted without knowing whether the source value is a summary, a content note, or a scope note.
- `rdaw:P10330` is the Work-level parallel for `rdac:C10001` W nodes.
- VRA classes use `vra:description` — a free-text description field with no structural RDA equivalent (confirmed "no match" in `mapping_vra_to_rda.csv`).
- RiC-O classes use `rico:note` — native archival note property with no RDA equivalent (confirmed "no match" in `mapping_rico_to_rda.csv`).
- All other classes (aco, mo, doco, ec, mocho subclasses) have no description property in their respective vocabularies; the source predicate `dc:description` is retained.

**Source**: `mapping_dct_to_rda.csv`, `mapping_vra_to_rda.csv`, `mapping_rico_to_rda.csv`.

---

## Decision 12: dcterms:isPartOf — class-specific part-relation dispatch

**Decision**: `dcterms:isPartOf` is mapped per target class via `output/config/lookup_class_prop_alignment.csv`:

| target_class | wemi | target_prop |
|---|---|---|
| `rdac:C10007`, `mocho:Manifestation` | M | `rdam:P30020` "is part of manifestation" |
| `rdac:C10001` | W | `rdaw:P10019` "is part of work" |
| `vra:Image`, `vra:Work` | M/W | `vra:partOf` |
| `rico:RecordSet`, `rico:Record`, `rico:RecordPart` | — | `dcterms:isPartOf` |
| all others (aco, mo, doco, ec, mocho subclasses) | M/W | `dcterms:isPartOf` |

**Corpus range** (full corpus, 70,311 values, `data/analysis/ispartof_coverage.csv`):

| Kind | n | % |
|---|---|---|
| Full DDB item URL (`http://…/item/<UUID>`) | 43,814 | 62.3% |
| Bare 32-char UUID | 22,265 | 31.7% |
| Label-only (no resource) | 4,232 | 6.0% |

**IRI sanitisation**: bare 32-char UUIDs must be prefixed with `http://www.deutsche-digitale-bibliothek.de/item/` before emitting. Full DDB URLs are used as-is.

**Rationale**:
- `rdam:P30020` "is part of manifestation" and `rdaw:P10019` "is part of work" are the direct RDA equivalents at M and W level respectively (confirmed in `mapping_dct_to_rda.csv`).
- VRA uses `vra:partOf` — maps to `rdaw:P10019` per `mapping_vra_to_rda.csv`; applies to both `vra:Image` and `vra:Work`.
- RiC-O: `rico:isOrWasComponentOf` domain is restricted to `rico:Instantiation`, not `rico:RecordResource` — no clean native equivalent for the Record hierarchy. `dcterms:isPartOf` is kept as a valid queryable fallback.
- All other classes have no part-relation property in their vocabularies; `dcterms:isPartOf` is kept.
- Label-only values carry no resolvable IRI — emitting a literal object for a property whose range is an IRI would violate the graph model; graph/ddbedm passthrough preserves them without loss.

**Source**: `mapping_dct_to_rda.csv`, `mapping_vra_to_rda.csv`, `mapping_rico_to_rda.csv`, `scripts/analyse_ispartof.py`.

---

## Decision 11: dcterms:language — re-cast LOC URI as rico:Language for RiC-O classes

**Decision**: For RiC-O target classes (`rico:RecordSet`, `rico:Record`, `rico:RecordPart`), `dcterms:language` is emitted using `rico:hasOrHadLanguage`. The LOC ISO 639-2 URI (e.g. `http://id.loc.gov/vocabulary/iso639-2/ger`) is additionally typed as `rico:Language` via a stub triple.

**Emitted triples** (RiC-O classes only):
```turtle
<cho>     rico:hasOrHadLanguage <http://id.loc.gov/vocabulary/iso639-2/ger> .
<loc-uri> a rico:Language .
```

For all non-RiC-O classes, `dcterms:language <LOC-URI>` is kept as-is (range `dcterms:LinguisticSystem`).

**Rationale**: The LOC ISO 639-2 URIs are authoritative language identifiers. Asserting `rico:Language` on them is a projection into mocho's class space, consistent with how GND URIs are asserted as `mocho:Agent` stubs. The LOC MADS/RDF definition (`madsrdf:Language`) does not prevent additional typing. This enables well-typed `rico:hasOrHadLanguage` triples without minting new URIs.

**dc:language** (literal): kept as `dc:language` for all classes including RiC-O. `rico:hasOrHadLanguage` expects a `rico:Language` instance (a URI), not a literal — the literal form has no direct RiC-O equivalent. The literal is retained as a cross-WEMI query handle.

**WEMI mismatch note**: Both `dc:language` and `dcterms:language` are Expression-level in RDA (`rdae:P20006` "has language of expression"); no `rdam:` equivalent exists. Language triples are emitted on the Manifestation node as a pragmatic shortcut until Expression nodes are minted. See `transform-future-plan.md §2`.

---

## Decision 13: edm:Agent — property mapping to mocho:Agent stub

**Decision**: All `edm:Agent` nodes are typed as `mocho:Agent` (Phase 0 stub). Properties are mapped per `output/config/lookup_class_prop_alignment.csv` (rows 549–572), generated by `scripts/gen_agent_alignment_rows.py`.

**Source namespace note**: DDB uses `gndo:` directly for agent-demographic properties (`gndo:dateOfBirth`, `gndo:dateOfDeath`, `gndo:dateOfEstablishment`, `gndo:dateOfTermination`, `gndo:gender`, `gndo:placeOfBirth`, `gndo:placeOfDeath`, `gndo:professionOrOccupation`, `gndo:biographicalOrHistoricalInformation`). These are passthrough — `edm_prop == target_prop`. The `align_ddbedm_to_mocho.py` script incorrectly resolved these as `edm:` (fallback for properties absent from `ddbedm_1.0.ttl`); the correct namespace is confirmed from `~/Documents/claude/mocho/ontology/gnd_20251218.ttl`.

**Non-trivial remappings**:

| edm_prop | target_prop | Reason |
|---|---|---|
| `dc:identifier` | `gndo:gndIdentifier` | GND number literal → GND-native identifier property |
| `edm:altLabel` | `skos:altLabel` | `edm:altLabel rdfs:subPropertyOf skos:altLabel`; promote to superclass |
| `edm:sameAs` | `owl:sameAs` | EDM declares these equivalent |

**Passthrough properties** (no transformation): `gndo:dateOfBirth`, `gndo:dateOfDeath`, `gndo:dateOfEstablishment`, `gndo:dateOfTermination`, `gndo:gender`, `gndo:placeOfBirth`, `gndo:placeOfDeath`, `gndo:professionOrOccupation`, `gndo:biographicalOrHistoricalInformation`, `skos:prefLabel`, `skos:note`, `foaf:name`, `dc:date`, `dc:type`, `dct:hasPart`, `dct:isPartOf`, `edm:begin`, `edm:end`, `edm:hasMet`, `edm:isRelatedTo`, `edm:wasPresentAt`.

**Domain mismatches deferred**: `gndo:dateOfEstablishment` / `gndo:dateOfTermination` apply to `gndo:CorporateBody` and `gndo:ConferenceOrEvent`, not `gndo:DifferentiatedPerson`. `edm:begin` / `edm:end` are generic temporals that map to type-specific gndo date properties. Both are emitted as-is under `mocho:Agent` until agent type is resolved. See `transform-future-plan.md §10`.

**Source**: `~/Documents/claude/mocho/ontology/gnd_20251218.ttl` (property domain analysis); `output/config/lookup_class_prop_alignment.csv`.

---

## Decision 6: aggregationEntity and hierarchyPosition — skip, no triple emitted

**Decision**: `ddb:aggregationEntity` (boolean string `"true"`/`"false"`) and `ddb:hierarchyPosition` (zero-padded sort key, e.g. `"000000000014848"`) are not emitted as triples.

**Rationale**: Both are DDB-internal fields with no mocho/RDA equivalent. `aggregationEntity` is a grouping flag used by the DDB portal UI. `hierarchyPosition` is a sort key for the display hierarchy. Neither carries semantic content useful to downstream graph consumers.

---

## Decision 14: edm:hasMet on ProvidedCHO — skip in mocho graph, passthrough in ddbedm

**Decision**: The `hasMet` JSON key on `ProvidedCHO` is excluded from the mocho graph via `_MOCHO_SKIP`. It is retained verbatim in the ddbedm passthrough graph as `ddbedm:hasMet` (`http://www.deutsche-digitale-bibliothek.de/edm/hasMet`).

**Background**: `edm:hasMet` on a ProvidedCHO links the object to related events, places, or concepts encountered by the object (or its creator). In the mocho graph there is no RDA/mocho alignment for this relationship at the CHO level. The property also appears in the LIDO contributor resolution chain (D3), where `ProvidedCHO.hasMet[].resource` is used to navigate to the `edm:Event` node — but the `hasMet` triple itself is not emitted on the mocho CHO.

**Note**: `edm:hasMet` on `edm:Agent` stubs is passthrough per D13 — the skip applies only to ProvidedCHO in the mocho graph.

**Implementation**: `"hasMet"` added to `_MOCHO_SKIP` in `constants.py`. `emit_ddbedm_triples` continues to emit it unchanged on the ddbedm graph subject.

---

## Decision 15: ddbedm:hierarchyType — emit as vocnet-htype: IRI in mocho graph

**Decision**: When `ProvidedCHO.hierarchyType` is present, `retype_entities()` emits one additional triple in the mocho graph:

```turtle
<cho> ddbedm:hierarchyType vocnet-htype:htype_021 .
```

where:
- `ddbedm:hierarchyType` = `http://www.deutsche-digitale-bibliothek.de/edm/hierarchyType`
- `vocnet-htype:` = `http://ddb.vocnet.org/hierarchietyp/`

The htype code (e.g. `"htype_021"`) is the local name of the vocnet-htype individual. This triple is emitted for every record with a non-empty `hierarchyType` value, regardless of whether the code drove the rdf:type dispatch (i.e. whether `use_htype=True` for the record's sector/mediatype row). The rdf:type dispatch outcome is independent — a record may receive a htype-derived class (layer 1 in `retype_entities`) and still always receive the `ddbedm:hierarchyType` triple.

**Rationale**: The htype code identifies the DDB document hierarchy position type (e.g. volume, chapter, article) and is the primary facet for hierarchical navigation in GeMeA. Emitting it as an IRI (not a literal) enables direct join to the vocnet-htype individuals in QLever without string normalization. The property IRI `ddbedm:hierarchyType` (in the DDB EDM extension namespace) is also used in the ddbedm passthrough graph, ensuring the predicate is consistent across both graphs.

**IRI correction**: The earlier `_DDBEDM_PROP` entry used `http://www.deutsche-digitale-bibliothek.de/hierarchyType` (no `/edm/`). This was corrected to `http://www.deutsche-digitale-bibliothek.de/edm/hierarchyType` as part of this decision. `DDBEDM_NS = "http://www.deutsche-digitale-bibliothek.de/edm/"` is defined as a named constant and `ddbedm:` added to `_PREFIXES`.

**Scope**: `hierarchyType` is in `_MOCHO_SKIP` — the generic property loop does not emit it. The triple is emitted exclusively by `retype_entities`. `hierarchyPosition` and `aggregationEntity` remain skipped per D6.

---

## Decision 16: Stub label predicate — `rdfs:label` over `skos:prefLabel`

**Decision**: Label stubs emitted in the mocho graph for referenced context entities (Agent, Place, Concept) use `rdfs:label`, not `skos:prefLabel`, even when the source JSON field is named `prefLabel`.

**Affected emitters**: `emit_subject_triples`, `emit_hastype_triples`, `emit_current_location_triples`, `emit_creator_triples`, `emit_contributor_triples`, `emit_place_stubs`.

**Rationale**:
- The JSON `prefLabel` field name is a DDB EDM convention, not a SKOS assertion. The mocho graph does not copy the SKOS semantics implied by that field name.
- These are convenience stubs for display and search — not full SKOS concept descriptions. Full `skos:prefLabel` triples will come from the GND enrichment graph (Phase 1b).
- QLever text-indexes against `rdfs:label` by default. Using it directly ensures stubs are full-text-searchable without configuration changes.
- QLever does not materialise RDFS/OWL entailments (`skos:prefLabel rdfs:subPropertyOf rdfs:label`), so asserting `skos:prefLabel` would not automatically make stubs retrievable via `rdfs:label` queries. Asserting `rdfs:label` is the pragmatic approach.

**Harm of not using `skos:prefLabel`**: SPARQL queries filtering specifically on `skos:prefLabel` will not find mocho stub labels. This is acceptable: once the GND enrichment graph is loaded, proper `skos:prefLabel` triples from GND will be available there; the mocho stub is intentionally a weaker, provisional label.

---

## Decision 17: Context entity stubs — no `rdf:type` in mocho for Place, Concept, Timespan

**Decision**: When the mocho graph emits stub triples for context entities referenced by a CHO (a label or property triple whose subject is not the CHO itself), the following typing policy applies:

| Context entity | Type asserted in mocho | Reason |
|---|---|---|
| Agent (creator, contributor) | `mocho:Agent` | mocho-specific alignment type; uniform query handle across WEMI levels; distinct from `edm:Agent` / `foaf:Agent` in ddbedm |
| Place (`edm:currentLocation`) | none | `edm:Place` already asserted in ddbedm; no mocho-specific Place type |
| Concept (`dcterms:subject`, `edm:hasType`) | none | `skos:Concept` already asserted in ddbedm; no mocho-specific Concept type |
| Timespan | none | not yet emitted as stubs; same policy will apply when introduced |

**Rationale**:
- QLever's default graph is the union of all named graphs. A query without a `GRAPH` filter will find `edm:Place` from ddbedm, `mocho:Agent` from mocho, and `skos:Concept` from ddbedm — cross-graph typing is transparent to consumers.
- Agent stubs are an exception because `mocho:Agent` is a mocho-internal type with no counterpart in EDM or any source vocabulary. It is needed for uniform federated querying (`?x a mocho:Agent`) across records that use different Agent superclasses (`foaf:Agent`, GND entity classes). Re-asserting it from the ddbedm graph is not possible since ddbedm uses `edm:Agent`, not `mocho:Agent`.
- Adding `edm:Place` or `skos:Concept` in mocho would be redundant cross-graph re-assertions with no query benefit. It would also inflate the mocho graph with triples whose authority belongs in ddbedm.

---

## Decision 18: Drop `dcTermSubject` — redundant corpus typo variant of `dcTermsSubject`

**Decision** (2026-05-08): Remove `dcTermSubject` from `_DDBEDM_PROP`, `SUBJECT_KEYS`, and `_MOCHO_SKIP` in `constants.py`. It is not emitted in either the ddbedm or mocho graph.

**Evidence**: Corpus analysis of `items-all-goethe-faust.json` (115,432 records):
- 69,588 records contain `dcTermSubject`
- All 69,588 also contain `dcTermsSubject`
- In all 69,588 cases the resource URIs are **identical** — zero records where `dcTermSubject` carries a value not already present in `dcTermsSubject`

Without this decision, `emit_ddbedm_triples` emits duplicate `dcterms:subject` triples for those 69,588 records (once per aliased key, no cross-field deduplication in the passthrough loop).

**Rationale**: `dcTermSubject` is a typo variant that appears only as a redundant copy of `dcTermsSubject`. Dropping it removes ~69,588 duplicate triples from the ddbedm graph with no information loss.
