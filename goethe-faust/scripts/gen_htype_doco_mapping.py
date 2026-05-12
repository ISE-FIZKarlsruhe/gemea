"""
gen_htype_doco_mapping.py

Match htype label_en values against DoCO ontology class labels using four
strategies and write a ranked mapping table.

Matching strategies (applied in order; first hit wins per htype):
  1. exact        — case-insensitive exact match
  2. levenshtein  — RapidFuzz ratio >= 88 %
  3. translated   — translate label_de (Helsinki-NLP/opus-mt-de-en), then
                    exact match against DoCO labels
  4. embedding    — cosine similarity of sentence-transformers/all-MiniLM-L6-v2
                    embeddings; top candidate reported regardless of threshold

Usage:
    python scripts/gen_htype_doco_mapping.py
    python scripts/gen_htype_doco_mapping.py \\
        --htypes   data/htype.csv \\
        --doco     data/schemas/doco.owl \\
        --out-csv  ~/Documents/claude/mocho/output/mapping_htype_doco.csv \\
        --out-json ~/Documents/claude/mocho/output/mapping_htype_doco.json \\
        --hf-cache data/hf-cache

Inputs:
    data/htype.csv              htype_code, label_de, label_en
    data/schemas/doco.owl       DoCO ontology (RDF/XML or Turtle)

Outputs:
    mapping_htype_doco.csv / .json

Columns:
    htype_code, label_de, label_en, match_method, confidence,
    doco_class, doco_label, doco_uri, translated_en

Dependencies:
    rdflib, rapidfuzz, sentence-transformers, transformers (HF), deep-translator
"""

import argparse
import csv
import json
import os
import warnings
from pathlib import Path

# Suppress noisy warnings from torch / urllib3
warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ["TRANSFORMERS_OFFLINE"] = "1"   # use only locally cached models
os.environ["HF_DATASETS_OFFLINE"]  = "1"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

WORKING_DIR  = Path.home() / "Documents/claude/goethe-faust"
DEFAULT_OUT  = Path.home() / "Documents/claude/mocho/output"

def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--htypes",   default=str(WORKING_DIR / "data/htype.csv"))
    p.add_argument("--doco",     default=str(WORKING_DIR / "data/schemas/doco.owl"))
    p.add_argument("--out-csv",  default=str(DEFAULT_OUT / "mapping_htype_doco.csv"))
    p.add_argument("--out-json", default=str(DEFAULT_OUT / "mapping_htype_doco.json"))
    p.add_argument("--hf-cache", default=str(WORKING_DIR / "data/hf-cache"),
                   help="Hugging Face cache directory")
    p.add_argument("--embed-threshold", type=float, default=0.0,
                   help="Min cosine similarity for embedding match (default: report best regardless)")
    p.add_argument("--lev-threshold", type=float, default=88.0,
                   help="Min Levenshtein ratio %% for fuzzy match (default: %(default)s)")
    return p.parse_args()

# ---------------------------------------------------------------------------
# DoCO label extraction
# ---------------------------------------------------------------------------

def load_doco_labels(owl_path: str) -> list[dict]:
    """Return list of {local, label, uri} for all DoCO classes."""
    from rdflib import Graph, RDFS, RDF, OWL, Namespace
    DOCO = Namespace("http://purl.org/spar/doco/")
    g = Graph()
    g.parse(owl_path)
    rows = []
    for subj, _, label in g.triples((None, RDFS.label, None)):
        uri = str(subj)
        if not uri.startswith(str(DOCO)):
            continue
        local = uri.split("/")[-1]
        rows.append({"local": local, "label": str(label), "uri": uri})
    return sorted(rows, key=lambda r: r["label"])

# ---------------------------------------------------------------------------
# Strategy 1: exact match
# ---------------------------------------------------------------------------

def match_exact(query, candidates):
    q = query.strip().lower()
    for c in candidates:
        if c["label"].lower() == q:
            return {"method": "exact", "confidence": 1.0, **c}
    return None

# ---------------------------------------------------------------------------
# Strategy 2: Levenshtein (RapidFuzz)
# ---------------------------------------------------------------------------

def match_levenshtein(query, candidates, threshold):
    from rapidfuzz import fuzz
    q = query.strip().lower()
    best, best_score = None, 0.0
    for c in candidates:
        score = fuzz.ratio(q, c["label"].lower())
        if score > best_score:
            best_score = score
            best = c
    if best and best_score >= threshold:
        return {"method": "levenshtein", "confidence": round(best_score / 100, 4), **best}
    return None

# ---------------------------------------------------------------------------
# Strategy 3: translate label_de → English, then exact match
# ---------------------------------------------------------------------------

_translator = None

def get_translator(hf_cache: str):
    global _translator
    if _translator is None:
        from transformers import MarianMTModel, MarianTokenizer
        model_name = "Helsinki-NLP/opus-mt-de-en"
        print("  Loading translation model …")
        # Resolve snapshot directory from hub cache layout
        import glob as _glob
        snaps = sorted(_glob.glob(
            str(Path(hf_cache) / "hub/models--Helsinki-NLP--opus-mt-de-en/snapshots/*/config.json")
        ))
        if not snaps:
            raise FileNotFoundError(
                f"opus-mt-de-en not found in {hf_cache}. "
                "Run once with network access to download it."
            )
        model_path = str(Path(snaps[-1]).parent)
        tokenizer = MarianTokenizer.from_pretrained(model_path, local_files_only=True)
        model     = MarianMTModel.from_pretrained(model_path, local_files_only=True)
        _translator = (tokenizer, model)
    return _translator

def translate_de_en(texts: list[str], hf_cache: str) -> list[str]:
    """Batch-translate German texts to English."""
    tokenizer, model = get_translator(hf_cache)
    inputs = tokenizer(texts, return_tensors="pt", padding=True, truncation=True)
    outputs = model.generate(**inputs)
    return [tokenizer.decode(o, skip_special_tokens=True) for o in outputs]

def match_translated(label_de, candidates, hf_cache,
                     translation_cache):
    """Translate label_de if not cached, then exact-match against DoCO labels."""
    if label_de not in translation_cache:
        translation_cache[label_de] = translate_de_en([label_de], hf_cache)[0]
    translated = translation_cache[label_de]
    hit = match_exact(translated, candidates)
    if hit:
        return {**hit, "method": "translated"}, translated
    return None, translated

# ---------------------------------------------------------------------------
# Strategy 4: sentence embedding cosine similarity
# ---------------------------------------------------------------------------

_embed_model = None

def get_embed_model(hf_cache: str):
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        print("  Loading embedding model …")
        # all-MiniLM-L6-v2 is in the default HF cache (~/.cache/huggingface);
        # opus-mt-de-en is in the project hf-cache.
        _embed_model = SentenceTransformer(
            "sentence-transformers/all-MiniLM-L6-v2",
            local_files_only=True,
        )
    return _embed_model

def match_embedding(query, candidates, hf_cache,
                    candidate_embeddings, threshold):
    import numpy as np
    model = get_embed_model(hf_cache)
    q_emb = model.encode([query.strip()], normalize_embeddings=True)[0]
    sims  = candidate_embeddings @ q_emb          # dot product = cosine (normalized)
    idx   = int(np.argmax(sims))
    score = float(sims[idx])
    if score >= threshold:
        c = candidates[idx]
        return {"method": "embedding", "confidence": round(score, 4), **c}
    return None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    hf_cache   = args.hf_cache
    lev_thresh = args.lev_threshold
    emb_thresh = args.embed_threshold

    # Load inputs
    print("Loading DoCO labels …")
    candidates = load_doco_labels(args.doco)
    print(f"  {len(candidates)} DoCO classes")

    htypes = []
    with open(args.htypes, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            htypes.append(row)
    print(f"  {len(htypes)} htype rows")

    # Pre-compute candidate embeddings once
    print("Computing DoCO label embeddings …")
    model = get_embed_model(hf_cache)
    import numpy as np
    cand_labels     = [c["label"] for c in candidates]
    cand_embeddings = model.encode(cand_labels, normalize_embeddings=True,
                                   show_progress_bar=False)

    translation_cache: dict[str, str] = {}
    rows = []

    print("Matching …")
    for ht in htypes:
        code     = ht["htype_code"].strip()
        label_de = ht["label_de"].strip()
        label_en = ht["label_en"].strip()

        translated_en = ""
        hit = None

        # 1. Exact
        hit = match_exact(label_en, candidates)

        # 2. Levenshtein
        if not hit:
            hit = match_levenshtein(label_en, candidates, lev_thresh)

        # 3. Translated + exact
        if not hit:
            hit, translated_en = match_translated(label_de, candidates,
                                                   hf_cache, translation_cache)

        # 4. Embedding (always runs; fills translated_en if not already set)
        if not hit:
            if not translated_en:
                if label_de not in translation_cache:
                    translation_cache[label_de] = translate_de_en(
                        [label_de], hf_cache)[0]
                translated_en = translation_cache[label_de]
            hit = match_embedding(label_en, candidates, hf_cache,
                                  cand_embeddings, emb_thresh)

        row = {
            "htype_code":   code,
            "label_de":     label_de,
            "label_en":     label_en,
            "match_method": hit["method"]     if hit else "no_match",
            "confidence":   hit["confidence"] if hit else 0.0,
            "doco_class":   hit["local"]      if hit else "",
            "doco_label":   hit["label"]      if hit else "",
            "doco_uri":     hit["uri"]        if hit else "",
            "translated_en": translated_en,
        }
        rows.append(row)
        status = f"{row['match_method']:12s}  {row['confidence']:.2f}  {row['doco_class']}"
        print(f"  {code}  {label_en:<30s}  →  {status}")

    # Write outputs
    out_csv  = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["htype_code", "label_de", "label_en", "match_method",
                  "confidence", "doco_class", "doco_label", "doco_uri",
                  "translated_en"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    summary = {m: sum(1 for r in rows if r["match_method"] == m)
               for m in ("exact", "levenshtein", "translated", "embedding", "no_match")}
    out_json.write_text(
        json.dumps({"summary": summary, "mappings": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nWrote {out_csv}")
    print(f"Wrote {out_json}")
    print("Summary:", summary)


if __name__ == "__main__":
    main()
