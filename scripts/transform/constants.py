"""Constants: IRIs, prefix tables, dispatch tables, path defaults, and type aliases."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[1]   # scripts/transform/ → scripts/ → project root

DEFAULT_JSONL        = PROJECT_DIR / "data"   / "items-all-goethe-faust.json"
DEFAULT_IDS          = PROJECT_DIR / "data"   / "ids-all-goethe-faust.txt"
DEFAULT_ALIGNMENT    = PROJECT_DIR / "output" / "config" / "lookup_class_prop_alignment.csv"
DEFAULT_LIDO         = PROJECT_DIR / "output" / "config" / "lido_event_types.csv"
DEFAULT_HTYPE        = PROJECT_DIR / "output" / "config" / "lookup_htype_doco_rico.csv"
DEFAULT_MEDIATYPE    = PROJECT_DIR / "output" / "config" / "lookup_mediatype_class.csv"
DEFAULT_AUDIO        = PROJECT_DIR / "output" / "config" / "audio_type2class.json"
DEFAULT_OUTPUT_BASE  = PROJECT_DIR / "output" / "transform"

# ─── Type aliases ─────────────────────────────────────────────────────────────

NQuad     = str
NQList    = List[NQuad]
PropAlign = Dict[Tuple[str, str], str]   # (target_class, edm_prop) → target_prop_iri
AgentDict = Dict[str, object]

# ─── Named graphs ─────────────────────────────────────────────────────────────

GRAPH_DDBEDM     = "https://gemea.ise.fiz-karlsruhe.de/graph/ddbedm"
GRAPH_MOCHO      = "https://gemea.ise.fiz-karlsruhe.de/graph/mocho"
GRAPH_PROV       = "https://gemea.ise.fiz-karlsruhe.de/graph/prov"
GRAPH_LANG_TITLE = "https://gemea.ise.fiz-karlsruhe.de/graph/lang-title"

# ─── URI bases ────────────────────────────────────────────────────────────────

GEMEA_BASE    = "https://gemea.ise.fiz-karlsruhe.de/mocho/"
DDB_ITEM_BASE = "http://www.deutsche-digitale-bibliothek.de/item/"
DDB_BASE           = "http://www.deutsche-digitale-bibliothek.de"
DDB_API_BASE       = "https://api.deutsche-digitale-bibliothek.de/2/"
DDBEDM_NS          = "http://www.deutsche-digitale-bibliothek.de/edm/"
DDB_HIERARCHY_TYPE = DDBEDM_NS + "hierarchyType"

# ─── Vocab prefixes ───────────────────────────────────────────────────────────

_MEDIATYPE_PREFIX = "http://ddb.vocnet.org/medientyp/"
_SECTOR_PREFIX    = "http://ddb.vocnet.org/sparte/"
_HTYPE_PREFIX     = "http://ddb.vocnet.org/hierarchietyp/"
MT007_IRI         = "http://ddb.vocnet.org/medientyp/mt007"

# ─── Ontology namespaces ──────────────────────────────────────────────────────

EDM_NS   = "http://www.europeana.eu/schemas/edm/"
GNDO_NS  = "https://d-nb.info/standards/elementset/gnd#"
CIDOC_NS = "http://www.cidoc-crm.org/cidoc-crm/"
MOCHO_NS = "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#"

# ─── Ontology IRIs ────────────────────────────────────────────────────────────

RDF_TYPE        = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL      = "http://www.w3.org/2000/01/rdf-schema#label"
OWL_SAMEAS      = "http://www.w3.org/2002/07/owl#sameAs"
SKOS_PREF_LABEL = "http://www.w3.org/2004/02/skos/core#prefLabel"
SKOS_CONCEPT    = "http://www.w3.org/2004/02/skos/core#Concept"
DCTERMS_SOURCE  = "http://purl.org/dc/terms/source"
FOAF_THUMBNAIL  = "http://xmlns.com/foaf/0.1/thumbnail"
FOAF_ORG        = "http://xmlns.com/foaf/0.1/Organization"
FOAF_NAME       = "http://xmlns.com/foaf/0.1/name"
EDM_DATA_PROVIDER = EDM_NS + "dataProvider"
EDM_HAS_TYPE      = EDM_NS + "hasType"
SCHEMA_URL      = "https://schema.org/url"
MOCHO_ISIL      = "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#isil"
MOCHO_AGENT     = MOCHO_NS + "Agent"
RICO_HAS_RST    = "http://www.ica.org/standards/RiC/ontology#hasRecordSetType"

PROV_ENTITY     = "http://www.w3.org/ns/prov#Entity"
PROV_AGENT      = "http://www.w3.org/ns/prov#Agent"
PROV_SW_AGENT   = "http://www.w3.org/ns/prov#SoftwareAgent"
PROV_DERIVED    = "http://www.w3.org/ns/prov#wasDerivedFrom"
PROV_ATTRIBUTED = "http://www.w3.org/ns/prov#wasAttributedTo"
PROV_GEN_TIME   = "http://www.w3.org/ns/prov#generatedAtTime"
PROV_ON_BEHALF  = "http://www.w3.org/ns/prov#actedOnBehalfOf"
DCAT_DATASET    = "http://www.w3.org/ns/dcat#Dataset"
DCTERMS_ID      = "http://purl.org/dc/terms/identifier"
DCTERMS_TYPE    = "http://purl.org/dc/terms/type"
DCTERMS_HAS_VER = "http://purl.org/dc/terms/hasVersion"
DCTERMS_REF     = "http://purl.org/dc/terms/references"
DCTERMS_RIGHTS  = "http://purl.org/dc/terms/rights"
DC_ID           = "http://purl.org/dc/elements/1.1/identifier"
DC_TITLE        = "http://purl.org/dc/elements/1.1/title"
DC_DESCRIPTION  = "http://purl.org/dc/elements/1.1/description"

DCTERMS_CREATOR = "http://purl.org/dc/terms/creator"
DC_CONTRIBUTOR  = "http://purl.org/dc/elements/1.1/contributor"
DC_SUBJECT      = "http://purl.org/dc/elements/1.1/subject"
DCTERMS_SUBJECT = "http://purl.org/dc/terms/subject"
XSD_DATETIME    = "http://www.w3.org/2001/XMLSchema#dateTime"

# ─── Property skip sets ───────────────────────────────────────────────────────

SUBJECT_KEYS = frozenset({"dcSubject", "dcTermsSubject"})

_MOCHO_SKIP = frozenset({
    "about", "hierarchyType",
    "creator", "contributor",
    "dcSubject", "dcTermsSubject",
    "dcType",
    "edmType",         # edm:type literal ("IMAGE" etc.); replaced by mocho:mediaType vocnet IRI
    "aggregationEntity", "hierarchyPosition",
    "hasMet",          # edm:hasMet is an EDM Event property; no mocho alignment, skip in mocho graph
    "hasType",         # handled by emit_hastype_triples(); IRI-with-label-stub (D16/D17)
    "currentLocation", # handled by emit_current_location_triples(); IRI-with-label-stub (D16/D17)
})

# ─── Prefix expansion table ───────────────────────────────────────────────────

_PREFIXES = {
    "rdam":    "http://rdaregistry.info/Elements/m/",
    "rdaw":    "http://rdaregistry.info/Elements/w/",
    "rdae":    "http://rdaregistry.info/Elements/e/",
    "rdac":    "http://rdaregistry.info/Elements/c/",
    "rdact":   "http://rdaregistry.info/termList/RDACarrierType/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "vra":     "http://purl.org/vra/",
    "rico":    "http://www.ica.org/standards/RiC/ontology#",
    "ric-rst": "http://www.ica.org/standards/RiC/vocabularies/recordSetTypes#",
    "skos":    "http://www.w3.org/2004/02/skos/core#",
    "owl":     "http://www.w3.org/2002/07/owl#",
    "rdfs":    "http://www.w3.org/2000/01/rdf-schema#",
    "foaf":    "http://xmlns.com/foaf/0.1/",
    "edm":     EDM_NS,
    "mo":      "http://purl.org/ontology/mo/",
    "aco":     "https://w3id.org/ac-ontology/aco#",
    "ec":      "http://www.ebu.ch/metadata/ontologies/ebucoreplus#",
    "doco":    "http://purl.org/spar/doco/",
    "mocho":   MOCHO_NS,
    "gndo":    GNDO_NS,
    "ddb":         "http://www.deutsche-digitale-bibliothek.de/",
    "ddbedm":      DDBEDM_NS,
    "vocnet-htype": _HTYPE_PREFIX,
    "ore":         "http://www.openarchives.org/ore/terms/",
}

# ─── EDM entity type map ──────────────────────────────────────────────────────

_EDM_ENTITY_TYPES = {
    "ProvidedCHO":  EDM_NS + "ProvidedCHO",
    "Agent":        EDM_NS + "Agent",
    "Place":        EDM_NS + "Place",
    "TimeSpan":     EDM_NS + "TimeSpan",
    "WebResource":  EDM_NS + "WebResource",
    "Aggregation":  "http://www.openarchives.org/ore/terms/Aggregation",
    "Concept":      "http://www.w3.org/2004/02/skos/core#Concept",
    "PhysicalThing": EDM_NS + "PhysicalThing",
    "Event":        EDM_NS + "Event",
}

# ─── Namespace tuple (used for mocho_vocab properties_new tracking) ─────────

_NEW_NS: tuple[str, ...] = (
    "http://rdaregistry.info/Elements/",
    "http://www.ica.org/standards/RiC/",
    MOCHO_NS,
    "http://purl.org/vra/",
    "http://purl.org/ontology/mo/",
    "https://w3id.org/ac-ontology/",
    "http://www.ebu.ch/metadata/ontologies/ebucoreplus#",
    "http://purl.org/spar/doco/",
)

# ─── W-slot classes (trigger werk_staging row) ────────────────────────────────

_W_SLOT_CLASSES: frozenset[str] = frozenset({
    "http://rdaregistry.info/Elements/c/C10001",  # rdac:C10001 Work
    "http://purl.org/ontology/mo/MusicalWork",    # mo:MusicalWork
})

# ─── Primary WEMI level per class ─────────────────────────────────────────────

_CLASS_WEMI: dict[str, str] = {
    # W — Work
    "http://rdaregistry.info/Elements/c/C10001":                       "W",
    MOCHO_NS + "ImmovableWork":                                        "W",
    MOCHO_NS + "ImageWork":                                            "W",
    "http://purl.org/ontology/mo/MusicalWork":                         "W",
    "http://purl.org/vra/Work":                                        "W",
    "http://www.ebu.ch/metadata/ontologies/ebucoreplus#EditorialWork":  "W",
    # M — Manifestation
    "http://rdaregistry.info/Elements/c/C10007":                       "M",
    MOCHO_NS + "Manifestation":                                        "M",
    MOCHO_NS + "ImageManifestation":                                   "M",
    "https://w3id.org/ac-ontology/aco#AudioManifestation":             "M",
    "http://purl.org/ontology/mo/MusicalManifestation":                "M",
    "http://www.ebu.ch/metadata/ontologies/ebucoreplus#MediaResource":  "M",
    "http://purl.org/vra/Image":                                       "M",
    # doco fragment types (Manifestation-level document parts)
    "http://purl.org/spar/doco/Section":         "M",
    "http://purl.org/spar/doco/Appendix":        "M",
    "http://purl.org/spar/doco/Part":            "M",
    "http://purl.org/spar/doco/Chapter":         "M",
    "http://purl.org/spar/doco/Figure":          "M",
    "http://purl.org/spar/doco/Index":           "M",
    "http://purl.org/spar/doco/TableOfContents": "M",
    "http://purl.org/spar/doco/TextChunk":       "M",
    "http://purl.org/spar/doco/Stanza":          "M",
    "http://purl.org/spar/doco/Preface":         "M",
    # RiC — no WEMI slot
    "http://www.ica.org/standards/RiC/ontology#RecordSet":  "",
    "http://www.ica.org/standards/RiC/ontology#Record":     "",
    "http://www.ica.org/standards/RiC/ontology#RecordPart": "",
}

# ─── Contributor column selection: (wemi, target_class) → lido_event_types col ─

_CONTRIBUTOR_COL: dict[tuple[str, str], str] = {
    ("M", "http://rdaregistry.info/Elements/c/C10007"):        "rdam_agent_prop",
    ("M", MOCHO_NS + "Manifestation"):                         "rdam_agent_prop",
    ("W", "http://rdaregistry.info/Elements/c/C10001"):        "rdaw_agent_prop",
    ("M", "http://purl.org/vra/Image"):                        "vra_image_agent_prop",
    ("W", "http://purl.org/vra/Work"):                         "vra_work_agent_prop",
    ("",  "http://www.ica.org/standards/RiC/ontology#RecordSet"):  "rico_agent_prop",
    ("",  "http://www.ica.org/standards/RiC/ontology#Record"):     "rico_agent_prop",
    ("",  "http://www.ica.org/standards/RiC/ontology#RecordPart"): "rico_agent_prop",
}

# ─── JSON key → predicate IRI (ddbedm passthrough and mocho alignment lookup) ─

_DDBEDM_PROP: dict[str, str] = {
    # DC elements 1.1
    "title":               "http://purl.org/dc/elements/1.1/title",
    "creator":             "http://purl.org/dc/elements/1.1/creator",
    "contributor":         "http://purl.org/dc/elements/1.1/contributor",
    "date":                "http://purl.org/dc/elements/1.1/date",
    "description":         "http://purl.org/dc/elements/1.1/description",
    "format":              "http://purl.org/dc/elements/1.1/format",
    "identifier":          "http://purl.org/dc/elements/1.1/identifier",
    "language":            "http://purl.org/dc/elements/1.1/language",
    "publisher":           "http://purl.org/dc/elements/1.1/publisher",
    "relation":            "http://purl.org/dc/elements/1.1/relation",
    "rights":              "http://purl.org/dc/elements/1.1/rights",
    "source":              "http://purl.org/dc/elements/1.1/source",
    "coverage":            "http://purl.org/dc/elements/1.1/coverage",
    "dcSubject":           "http://purl.org/dc/elements/1.1/subject",
    "dcType":              "http://purl.org/dc/elements/1.1/type",
    # DC terms
    "alternative":         "http://purl.org/dc/terms/alternative",
    "dcTermsSubject":      "http://purl.org/dc/terms/subject",
    "dcTermsLanguage":     "http://purl.org/dc/terms/language",
    "isPartOf":            "http://purl.org/dc/terms/isPartOf",
    "issued":              "http://purl.org/dc/terms/issued",
    "extent":              "http://purl.org/dc/terms/extent",
    "medium":              "http://purl.org/dc/terms/medium",
    "tableOfContents":     "http://purl.org/dc/terms/tableOfContents",
    "hasPart":             "http://purl.org/dc/terms/hasPart",
    "spatial":             "http://purl.org/dc/terms/spatial",
    "dcTermsRights":       "http://purl.org/dc/terms/rights",
    # EDM
    "currentLocation":     EDM_NS + "currentLocation",
    "hasMet":              EDM_NS + "hasMet",
    "hasType":             EDM_NS + "hasType",
    "isNextInSequence":    EDM_NS + "isNextInSequence",
    "isShownAt":           EDM_NS + "isShownAt",
    "isShownBy":           EDM_NS + "isShownBy",
    "wasPresentAt":        EDM_NS + "wasPresentAt",
    "isRelatedTo":         EDM_NS + "isRelatedTo",
    "edmType":             EDM_NS + "type",
    "object":              EDM_NS + "object",
    "aggregatedCHO":       EDM_NS + "aggregatedCHO",
    "aggregator":          EDM_NS + "aggregator",
    "dataProvider":        EDM_NS + "dataProvider",
    "edmRights":           EDM_NS + "rights",
    "provider":            EDM_NS + "provider",
    "hasView":             EDM_NS + "hasView",
    "begin":               EDM_NS + "begin",
    "end":                 EDM_NS + "end",
    "occurredAt":          EDM_NS + "occurredAt",
    "occuredAt":           EDM_NS + "occurredAt",  # typo variant in corpus
    "happenedAt":          EDM_NS + "happenedAt",
    # SKOS
    "prefLabel":           "http://www.w3.org/2004/02/skos/core#prefLabel",
    "altLabel":            "http://www.w3.org/2004/02/skos/core#altLabel",
    "note":                "http://www.w3.org/2004/02/skos/core#note",
    "notation":            "http://www.w3.org/2004/02/skos/core#notation",
    # RDF / OWL
    "type":                "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
    "sameAs":              "http://www.w3.org/2002/07/owl#sameAs",
    # FOAF
    "name":                "http://xmlns.com/foaf/0.1/name",
    # GND
    "biographicalInformation": GNDO_NS + "biographicalInformation",
    "dateOfBirth":             GNDO_NS + "dateOfBirth",
    "dateOfDeath":             GNDO_NS + "dateOfDeath",
    "dateOfEstablishment":     GNDO_NS + "dateOfEstablishment",
    "dateOfTermination":       GNDO_NS + "dateOfTermination",
    "gender":                  GNDO_NS + "gender",
    "placeOfBirth":            GNDO_NS + "placeOfBirth",
    "placeOfDeath":            GNDO_NS + "placeOfDeath",
    "professionOrOccupation":  GNDO_NS + "professionOrOccupation",
    # CIDOC-CRM (LIDO events)
    "P11_had_participant": CIDOC_NS + "P11_had_participant",
    # DDB-internal structural fields (preserved in ddbedm, skipped in mocho)
    "hierarchyType":      DDBEDM_NS + "hierarchyType",
    "hierarchyPosition":  "http://www.deutsche-digitale-bibliothek.de/hierarchyPosition",
    "aggregationEntity":  "http://www.deutsche-digitale-bibliothek.de/aggregationEntity",
}
