"""Score a local ASR recognition JSON against a plain-text reference.

The caller must label the provenance and quality of the reference separately;
this script computes edit-distance metrics and does not certify ground truth.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Sequence


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"[\w']+", normalized, flags=re.UNICODE)


def _characters(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [char for char in normalized if char.isalnum()]


def _edit_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> dict[str, int]:
    rows, columns = len(reference) + 1, len(hypothesis) + 1
    costs = [[0] * columns for _ in range(rows)]
    steps = [[""] * columns for _ in range(rows)]
    for i in range(1, rows):
        costs[i][0], steps[i][0] = i, "delete"
    for j in range(1, columns):
        costs[0][j], steps[0][j] = j, "insert"
    for i in range(1, rows):
        for j in range(1, columns):
            substitution = costs[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1])
            deletion = costs[i - 1][j] + 1
            insertion = costs[i][j - 1] + 1
            costs[i][j] = min(substitution, deletion, insertion)
            steps[i][j] = (
                "match" if reference[i - 1] == hypothesis[j - 1] and costs[i][j] == substitution
                else "substitute" if costs[i][j] == substitution
                else "delete" if costs[i][j] == deletion
                else "insert"
            )
    counts = {"substitutions": 0, "deletions": 0, "insertions": 0}
    i, j = len(reference), len(hypothesis)
    while i or j:
        step = steps[i][j]
        if step == "match":
            i -= 1
            j -= 1
        elif step == "substitute":
            counts["substitutions"] += 1
            i -= 1
            j -= 1
        elif step == "delete":
            counts["deletions"] += 1
            i -= 1
        elif step == "insert":
            counts["insertions"] += 1
            j -= 1
        else:
            raise RuntimeError(f"invalid edit path at {i},{j}")
    return counts


def score(recognition_path: Path, reference_path: Path, output_path: Path) -> dict:
    recognition = json.loads(recognition_path.read_text(encoding="utf-8"))
    reference_text = reference_path.read_text(encoding="utf-8").strip()
    hypothesis_text = " ".join(
        str(segment.get("text", "")).strip()
        for segment in recognition.get("segments", [])
        if str(segment.get("text", "")).strip()
    )
    reference_words, hypothesis_words = _tokens(reference_text), _tokens(hypothesis_text)
    word_edits = _edit_counts(reference_words, hypothesis_words)
    reference_chars, hypothesis_chars = _characters(reference_text), _characters(hypothesis_text)
    char_edits = _edit_counts(reference_chars, hypothesis_chars)
    result = {
        "schemaVersion": 1,
        "recognitionFile": recognition_path.as_posix(),
        "referenceFile": reference_path.as_posix(),
        "referenceProvenance": "must_be_declared_by_acceptance_record",
        "referenceText": reference_text,
        "hypothesisText": hypothesis_text,
        "referenceWordCount": len(reference_words),
        "hypothesisWordCount": len(hypothesis_words),
        "wordEdits": word_edits,
        "wordErrorRate": round(sum(word_edits.values()) / max(1, len(reference_words)), 6),
        "referenceCharacterCount": len(reference_chars),
        "hypothesisCharacterCount": len(hypothesis_chars),
        "characterEdits": char_edits,
        "characterErrorRate": round(sum(char_edits.values()) / max(1, len(reference_chars)), 6),
        "metricMethod": "casefolded NFKC; word tokens ignore punctuation; CER uses alphanumeric characters",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recognition", type=Path, help="verify_local_asr_acceptance.py recognition.json")
    parser.add_argument("reference", type=Path, help="UTF-8 plain-text reference transcript")
    parser.add_argument("output", type=Path, help="JSON metric report path")
    args = parser.parse_args()
    print(json.dumps(score(args.recognition, args.reference, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
