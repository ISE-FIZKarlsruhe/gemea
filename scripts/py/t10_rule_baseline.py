#!/usr/bin/env python3
# Purpose:      T10 -- deterministic ISBD rule-based baseline (JCDL 2026 short
#               camera-ready spine, notes/fiz/bibner-werk/jcdl-short-t10.md).
#               Reimplements sr01_rate_isbd_fields.py's regex flag detection
#               (has_dot_dash, f_other_title, f_person) and
#               build_silver_spans.py's extract_spans()/trim_span() span
#               derivation, applied directly to the 395-record gold set, to
#               produce a no-training baseline's predicted spans -- scored
#               downstream by eval_ner_gold.py against the same tier split
#               T5 already uses (b9_tier_breakdown.py). No cross-script
#               imports (sr-series convention, see build_silver_spans.py's
#               header): both pieces of logic are reimplemented here, not
#               imported.
# Usage:        python3 scripts/py/t10_rule_baseline.py
#                   [--gold PATH] [--output PATH]
# Inputs:       data/gold/gold_reconciled.jsonl (395 records: id, text, label
#                   -- label is not read; gold spans are only used downstream
#                   for scoring, not by this script)
# Outputs:      data/eval/predictions_rulebaseline.jsonl -- one line per doc:
#                   {"id": doc_id, "spans": [[start, end, label], ...]},
#                   matching eval_ner_gold.py score --predictions's schema
# Dependencies: none beyond the standard library
# Assumptions:  PERSON spans are triggered by raw f_person (` /` punctuation)
#                   rather than build_silver_spans.py's f_resp_person (which
#                   is itself computed by a FLERT NER tagger, ADR-06) --
#                   deliberate: using FLERT would make this a second neural
#                   system wearing a rule-baseline label, undermining the
#                   non-ML comparison point R2/R3/R4 asked for (jcdl-short-
#                   t10.md Sec.2). Consequence: this baseline over-predicts
#                   PERSON on non-person statements-of-responsibility
#                   (organizations, translator credits) that the silver
#                   pipeline's FLERT step would have filtered out -- an
#                   expected precision cost of being a genuine rule baseline,
#                   not a bug. Flag detection runs on gold text directly (the
#                   gold set was never passed through sr01_rate_isbd_fields.py
#                   against its ratings CSV); has_dot_dash/f_other_title/
#                   f_person are recomputed here identically to that script's
#                   rate(), row-wise rather than vectorised (395 records).

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GOLD = ROOT / "data" / "gold" / "gold_reconciled.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "eval" / "predictions_rulebaseline.jsonl"

# ---------------------------------------------------------------------------
# Flag detection -- identical patterns to sr01_rate_isbd_fields.py's rate();
# only has_dot_dash/f_other_title/f_person are needed here (the other f_*
# fields feed silver_tier assignment, which this script does not compute --
# tier comes from the existing folds_k10.json, joined downstream by
# b9_tier_breakdown.py).
# ---------------------------------------------------------------------------

RE_AREA_SEP = re.compile(r"\. - ")
RE_OTHER_TITLE = re.compile(r" :")
RE_PERSON = re.compile(r" /")


def detect_flags(title: str) -> tuple[bool, bool, bool]:
    """Return (has_dot_dash, f_other_title, f_person) for one title string.

    Mirrors sr01_rate_isbd_fields.py's rate(): structural records (has_dot_dash)
    search only the title area (before the first '. - '); heuristic records
    search the whole string.
    """
    has_dot_dash = bool(RE_AREA_SEP.search(title))
    if has_dot_dash:
        title_area = title.split(". - ", 1)[0]
        f_other_title = bool(RE_OTHER_TITLE.search(title_area))
        f_person = bool(RE_PERSON.search(title_area))
    else:
        f_other_title = bool(RE_OTHER_TITLE.search(title))
        f_person = bool(RE_PERSON.search(title))
    return has_dot_dash, f_other_title, f_person


# ---------------------------------------------------------------------------
# Span derivation -- ports build_silver_spans.py's extract_spans()/trim_span(),
# with f_person substituted for f_resp_person as the PERSON trigger (see
# module header).
# ---------------------------------------------------------------------------

OTHER_TITLE_MARK = " :"
PERSON_MARK = " /"
SUB_DELIM_RE = re.compile(r";")
LEADING_STRIP_CHARS = " \t/:;,"


def title_area_end(text: str, has_dot_dash: bool) -> int:
    if not has_dot_dash:
        return len(text)
    pos = text.find(". - ")
    return pos if pos != -1 else len(text)


def trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    span_text = text[start:end]
    left_trimmed = span_text.lstrip(LEADING_STRIP_CHARS)
    new_start = start + (len(span_text) - len(left_trimmed))
    trimmed = left_trimmed.rstrip()
    if trimmed.endswith("."):
        trimmed = trimmed[:-1].rstrip()
    return new_start, new_start + len(trimmed)


def extract_spans(
    text: str, has_dot_dash: bool, f_other_title: bool, f_person: bool
) -> list[tuple[int, int, str]]:
    """Derive TITLE/OTHER_TITLE/PERSON char-offset spans from ISBD punctuation.

    f_person (raw ` /` presence) drives the PERSON boundary here, not
    f_resp_person -- see module header.
    """
    area_end = title_area_end(text, has_dot_dash)
    area = text[:area_end]

    other_pos = area.find(OTHER_TITLE_MARK) if f_other_title else -1
    person_pos = area.find(PERSON_MARK) if f_person else -1

    boundaries = sorted(
        (pos, mark_len, label)
        for pos, mark_len, label in (
            (other_pos, len(OTHER_TITLE_MARK), "OTHER_TITLE"),
            (person_pos, len(PERSON_MARK), "PERSON"),
        )
        if pos != -1
    )

    title_end = boundaries[0][0] if boundaries else area_end
    spans = [(0, title_end, "TITLE")]

    for i, (pos, mark_len, label) in enumerate(boundaries):
        start = pos + mark_len
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else area_end
        if label == "PERSON":
            sub_match = SUB_DELIM_RE.search(area, start, end)
            if sub_match:
                end = sub_match.start()
        spans.append((start, end, label))

    trimmed = [(*trim_span(text, s, e), label) for s, e, label in spans]
    return [(s, e, label) for s, e, label in trimmed if e > s]


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_gold(path: Path) -> list[dict[str, object]]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def build_predictions(records: list[dict[str, object]]) -> list[dict[str, object]]:
    predictions = []
    for rec in records:
        text = str(rec["text"])
        has_dot_dash, f_other_title, f_person = detect_flags(text)
        spans = extract_spans(text, has_dot_dash, f_other_title, f_person)
        predictions.append({
            "id": rec["id"],
            "spans": [[s, e, label] for s, e, label in spans],
        })
    return predictions


def verify_predictions(
    predictions: list[dict[str, object]],
    gold_ids: set[object],
    text_by_id: dict[object, str],
) -> None:
    """Mechanical checks (jcdl-short-t10.md Sec.5, checks 1-4). Fails loudly."""
    pred_ids = {p["id"] for p in predictions}
    if pred_ids != gold_ids:
        missing = gold_ids - pred_ids
        extra = pred_ids - gold_ids
        raise ValueError(
            f"Predicted id set does not match gold id set: "
            f"missing={missing!r} extra={extra!r}"
        )

    for pred in predictions:
        spans = pred["spans"]
        assert isinstance(spans, list)
        text_len = len(text_by_id[pred["id"]])

        for start, end, _label in spans:
            if not (0 <= start < end <= text_len):
                raise ValueError(
                    f"Invalid span bounds in record {pred['id']!r}: {(start, end)}"
                )

        labels = {s[2] for s in spans}
        if "TITLE" not in labels:
            raise ValueError(f"Record {pred['id']!r} has no predicted TITLE span")

        spans_sorted = sorted(spans, key=lambda s: s[0])
        for i in range(len(spans_sorted) - 1):
            if spans_sorted[i][1] > spans_sorted[i + 1][0]:
                raise ValueError(
                    f"Overlapping predicted spans in record {pred['id']!r}: {spans}"
                )


def write_predictions(predictions: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    records = load_gold(args.gold)
    predictions = build_predictions(records)

    gold_ids = {rec["id"] for rec in records}
    text_by_id = {rec["id"]: str(rec["text"]) for rec in records}
    verify_predictions(predictions, gold_ids, text_by_id)

    write_predictions(predictions, args.output)
    print(f"Wrote {len(predictions)} predictions -> {args.output}")


if __name__ == "__main__":
    main()
