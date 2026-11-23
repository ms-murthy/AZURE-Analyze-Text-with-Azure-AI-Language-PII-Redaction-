"""
Batch version of the review analyzer.

Improvements over text-analysis.py:
  * Sends reviews to the service in batches instead of one call per review
  * Handles per-document errors without stopping the whole run
  * Saves structured results to output/results.json and privacy-safe
    redacted reviews to output/redacted/

Usage:
    python text_analysis_batch.py                 # analyze ./reviews
    python text_analysis_batch.py --input my_dir  # analyze another folder
"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.textanalytics import TextAnalyticsClient

# Entity recognition and PII detection accept at most 5 documents per request,
# so 5 is a safe batch size for all three operations.
BATCH_SIZE = 5


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_reviews(folder: Path):
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".txt")
    return [{"id": p.name, "text": p.read_text(encoding="utf8")} for p in files]


def analyze(client: TextAnalyticsClient, reviews):
    results = {r["id"]: {"file": r["id"]} for r in reviews}

    for batch in chunks(reviews, BATCH_SIZE):
        docs = [{"id": r["id"], "text": r["text"]} for r in batch]

        # Language detection
        for doc in client.detect_language(documents=docs):
            if doc.is_error:
                results[doc.id]["language_error"] = doc.error.message
                continue
            lang = doc.primary_language
            results[doc.id]["language"] = {
                "name": lang.name,
                "iso6391": lang.iso6391_name,
                "confidence": lang.confidence_score,
            }

        # Named entity recognition
        for doc in client.recognize_entities(documents=docs):
            if doc.is_error:
                results[doc.id]["entities_error"] = doc.error.message
                continue
            results[doc.id]["entities"] = [
                {
                    "text": e.text,
                    "category": e.category,
                    "subcategory": e.subcategory,
                    "confidence": e.confidence_score,
                }
                for e in doc.entities
            ]

        # PII detection and redaction
        for doc in client.recognize_pii_entities(documents=docs):
            if doc.is_error:
                results[doc.id]["pii_error"] = doc.error.message
                continue
            results[doc.id]["pii_entities"] = [
                {"text": e.text, "category": e.category, "confidence": e.confidence_score}
                for e in doc.entities
            ]
            results[doc.id]["redacted_text"] = doc.redacted_text

    return list(results.values())


def print_summary(results):
    for r in results:
        print("\n-------------\n" + r["file"])
        lang = r.get("language")
        print("Language:", lang["name"] if lang else r.get("language_error", "n/a"))
        if r.get("entities"):
            print("Entities:")
            for e in r["entities"]:
                print(f"\t{e['text']} ({e['category']})")
        if r.get("pii_entities"):
            print("PII Entities:")
            for e in r["pii_entities"]:
                print(f"\t{e['text']} ({e['category']})")


def save(results, out_dir: Path):
    redacted_dir = out_dir / "redacted"
    redacted_dir.mkdir(parents=True, exist_ok=True)

    # The JSON file contains the original PII values found in the text.
    # It is git-ignored; only share the redacted files.
    with open(out_dir / "results.json", "w", encoding="utf8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    for r in results:
        if "redacted_text" in r:
            (redacted_dir / r["file"]).write_text(r["redacted_text"], encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description="Batch text analysis with Azure AI Language")
    parser.add_argument("--input", default="reviews", help="Folder of .txt files to analyze")
    parser.add_argument("--output", default="output", help="Folder for results")
    args = parser.parse_args()

    load_dotenv()
    endpoint = os.getenv("FOUNDRY_ENDPOINT")
    if not endpoint:
        raise SystemExit("FOUNDRY_ENDPOINT is not set. Copy .env.example to .env and add your endpoint.")

    client = TextAnalyticsClient(endpoint=endpoint, credential=DefaultAzureCredential())

    reviews = load_reviews(Path(args.input))
    if not reviews:
        raise SystemExit(f"No .txt files found in '{args.input}'.")

    results = analyze(client, reviews)
    print_summary(results)
    save(results, Path(args.output))
    print(f"\nAnalyzed {len(results)} reviews. Results saved to '{args.output}/'.")


if __name__ == "__main__":
    main()
