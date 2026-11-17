#!/usr/bin/env python3
# Purpose:      Run a Flair NER model over a sample of titles from a metadata parquet, and
#               record what it marks, to judge whether WORK_OF_ART spans could feed step 1
#               of Werk linking. It does not query GND.
# Usage:        <venv with flair>/bin/python scripts/py/flair_work_of_art_probe.py
#                   [--input PATH] [--output PATH] [--model NAME] [--sample-size 300] [--seed 20261105]
#                   [--include-obj-id ID] [--batch-size 16] [--shard 0 --num-shards 1] [--dry-run] [--force]
#               To use two GPUs, run two processes with CUDA_VISIBLE_DEVICES=0 and =1,
#               --num-shards 2 and --shard 0 or 1, and a different --output each.
# Inputs:       --input parquet with columns obj_id, title (default: the Goethe-Faust parquet
#                   in the sibling goethe-faust repo).
#               --model Flair model name (default: flair/ner-english-ontonotes-large).
# Outputs:      --output CSV, one row per predicted span, plus one row with empty label for
#                   each title without a span: obj_id, title, label, span, score
#                   (default: data/processed/nerl-gnd/flair_probe_goethe_faust_20261105.csv).
#               Exit codes: 0 success, 1 failure, 2 usage error.
#               A shard writes only its own titles: every num_shards-th title of the sample,
#               starting at --shard, so the shards together cover the sample once.
# Dependencies: pyarrow; flair (not needed for --dry-run).
# Assumptions:  A look at what the model marks, not an evaluation: there are no gold labels.
#               The default model was trained on English OntoNotes, while these titles are
#               mainly German and historical, so its WORK_OF_ART spans may be weak here.

from __future__ import annotations

import argparse
import csv
import logging
import random
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NoReturn

import pyarrow.parquet as pq  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT.parent / "goethe-faust" / "output" / "items-all-goethe-faust_meta.parquet"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "nerl-gnd" / "flair_probe_goethe_faust_20261105.csv"
DEFAULT_MODEL = "flair/ner-english-ontonotes-large"
DEFAULT_INCLUDE_OBJ_ID = "NEL6FUWBOR4IGNBIWEITW63XM7TWO46L"
DEFAULT_SAMPLE_SIZE = 300
DEFAULT_SEED = 20261105
DEFAULT_BATCH_SIZE = 16
OUTPUT_COLUMNS = ["obj_id", "title", "label", "span", "score"]

Prediction = tuple[str, str, float]
Tagger = Callable[[list[str]], list[list[Prediction]]]

logger = logging.getLogger("flair_probe")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read_titles(path: Path) -> list[tuple[str, str]]:
    """Read (obj_id, title) for every record with a title."""
    parquet = pq.ParquetFile(path)
    missing = [c for c in ("obj_id", "title") if c not in parquet.schema_arrow.names]
    if missing:
        die(f"{path} has no column {', '.join(missing)}; pass a metadata parquet with --input")
    titles = []
    for batch in parquet.iter_batches(columns=["obj_id", "title"]):
        for obj_id, title in zip(batch.column("obj_id").to_pylist(), batch.column("title").to_pylist()):
            if title and title.strip():
                titles.append((obj_id, title))
    return titles


def choose_sample(
    titles: list[tuple[str, str]], size: int, seed: int, include_obj_id: str
) -> list[tuple[str, str]]:
    """A repeatable random sample, with the included record first if it exists."""
    included = [t for t in titles if t[0] == include_obj_id]
    rest = [t for t in titles if t[0] != include_obj_id]
    return included + random.Random(seed).sample(rest, min(size, len(rest)))


def shard_sample(sample: list[tuple[str, str]], shard: int, num_shards: int) -> list[tuple[str, str]]:
    """Every num_shards-th title, starting at shard: shards cover the sample once, without overlap."""
    return sample[shard::num_shards]


def load_tagger(model: str, batch_size: int) -> Tagger:
    """Load the Flair model, as a function from titles to (label, span, score) lists."""
    try:
        from flair.data import Sentence
        from flair.models import SequenceTagger
    except ImportError as e:
        die(f"cannot import flair ({e}); run with a venv that has flair installed")
    tagger = SequenceTagger.load(model)

    def tag(texts: list[str]) -> list[list[Prediction]]:
        sentences = [Sentence(text) for text in texts]
        tagger.predict(sentences, mini_batch_size=batch_size)
        return [
            [(span.get_label("ner").value, span.text, float(span.get_label("ner").score))
             for span in sentence.get_spans("ner")]
            for sentence in sentences
        ]

    return tag


def predict_rows(sample: list[tuple[str, str]], tag: Tagger, batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[object]]:
    """One row per span, and one row with an empty label for a title without spans."""
    rows: list[list[object]] = []
    for start in range(0, len(sample), batch_size):
        batch = sample[start : start + batch_size]
        for (obj_id, title), predictions in zip(batch, tag([title for _, title in batch])):
            if not predictions:
                rows.append([obj_id, title, "", "", ""])
            for label, span, score in predictions:
                rows.append([obj_id, title, label, span, round(score, 4)])
        logger.info("%d of %d titles", min(start + batch_size, len(sample)), len(sample))
    return rows


def label_counts(rows: Iterable[list[object]]) -> Counter[str]:
    return Counter(str(row[2]) or "(no span)" for row in rows)


def write_csv(rows: list[list[object]], output: Path) -> None:
    """Write the rows atomically."""
    tmp = output.with_name(output.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)
    tmp.replace(output)


def preflight(args: argparse.Namespace) -> None:
    for flag, value in (("--sample-size", args.sample_size), ("--batch-size", args.batch_size), ("--num-shards", args.num_shards)):
        if value < 1:
            print(f"ERROR: {flag} must be at least 1", file=sys.stderr)
            sys.exit(2)
    if not 0 <= args.shard < args.num_shards:
        print(f"ERROR: --shard must be between 0 and {args.num_shards - 1}", file=sys.stderr)
        sys.exit(2)
    if not args.input.is_file():
        die(f"{args.input} not found; pass the metadata parquet with --input")
    if args.output.exists() and not args.force:
        die(f"{args.output} exists; pass --force to overwrite it")
    if not args.dry_run:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(f"cannot create {args.output.parent} ({e}); choose another --output")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record what a Flair NER model marks in a sample of titles.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--include-obj-id", default=DEFAULT_INCLUDE_OBJ_ID)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="titles per Flair mini-batch")
    parser.add_argument("--shard", type=int, default=0, help="which shard this process runs, from 0")
    parser.add_argument("--num-shards", type=int, default=1, help="number of processes sharing the sample")
    parser.add_argument("--dry-run", action="store_true", help="choose the sample and log it, but load no model")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --output")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    preflight(args)
    sample = choose_sample(read_titles(args.input), args.sample_size, args.seed, args.include_obj_id)
    logger.info("Sample: %d titles", len(sample))
    if args.num_shards > 1:
        sample = shard_sample(sample, args.shard, args.num_shards)
        logger.info("Shard %d of %d: %d titles", args.shard, args.num_shards, len(sample))
    if args.dry_run:
        logger.info("Dry run: loaded no model, wrote nothing")
        return 0
    rows = predict_rows(sample, load_tagger(args.model, args.batch_size), args.batch_size)
    logger.info("Labels: %s", dict(label_counts(rows)))
    write_csv(rows, args.output)
    logger.info("Wrote %d rows -> %s", len(rows), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
