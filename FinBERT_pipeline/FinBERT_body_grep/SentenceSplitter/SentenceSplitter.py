#!/usr/bin/env python3
"""
SentenceSplitter.py - Sentence segmentation on JSON input with multiple text fields.

Performs sentence boundary detection using spaCy on JSON input containing
"headline", "summary", and/or "content" fields, outputting sentences in a
format compatible with SpaCy NER processing.

Usage:
    python SentenceSplitter.py --input FEED_28-jan-2026_cleaned_pronouns.json
"""

import argparse
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

try:
    import spacy
except ImportError:
    print("Error: spaCy is not installed. Please install it with:")
    print("  pip install spacy")
    print("  python -m spacy download en_core_web_sm")
    sys.exit(1)


def load_spacy_model() -> spacy.Language:
    """Load spaCy model for sentence boundary detection.

    Returns:
        spacy.Language: Loaded spaCy model

    Raises:
        SystemExit: If model cannot be loaded
    """
    try:
        nlp = spacy.load("en_core_web_sm")
        return nlp
    except OSError:
        print("Error: spaCy model 'en_core_web_sm' not found.")
        print("Please download it with:")
        print("  python -m spacy download en_core_web_sm")
        sys.exit(1)


def load_json_input(input_path: Path) -> Dict:
    """Load and validate JSON input file.

    Args:
        input_path: Path to JSON input file

    Returns:
        Dictionary with extracted fields (headline, summary, content)

    Raises:
        SystemExit: If file not found or invalid JSON structure
    """
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file: {input_path}")
        print(f"  {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file {input_path}: {e}")
        sys.exit(1)

    # Validate that at least one field exists
    available_fields = {}
    field_order = ["headline", "summary", "content"]

    for field in field_order:
        if field in data and data[field]:
            available_fields[field] = data[field]

    if not available_fields:
        print(f"Error: JSON file must contain at least one of: headline, summary, content")
        sys.exit(1)

    return available_fields


def process_field(
    field_name: str,
    text: str,
    nlp: spacy.Language
) -> Optional[Tuple[str, List[Dict], int]]:
    """Process a text field and segment sentences.

    Args:
        field_name: Name of the field (headline, summary, or content)
        text: Text content to process
        nlp: Loaded spaCy model

    Returns:
        Tuple of (field_name, sentences_list, field_index) or None if text empty
        where field_index determines ordering (0=headline, 1=summary, 2=content)
    """
    field_mapping = {
        "headline": 0,
        "summary": 1,
        "content": 2
    }

    field_index = field_mapping.get(field_name, 999)

    text = text.strip() if text else ""

    if not text:
        print(f"Warning: Field '{field_name}' is empty")
        return None

    try:
        # Process with spaCy for sentence segmentation
        doc = nlp(text)

        # Extract sentences with metadata
        sentences = []
        for sent_idx, sent in enumerate(doc.sents):
            sentence_data = {
                "text": sent.text,
                "source": field_name,
                "char_start": sent.start_char,
                "char_end": sent.end_char,
                "sent_idx": sent_idx  # Index within this field
            }
            sentences.append(sentence_data)

        print(f"Processed field '{field_name}': {len(sentences)} sentences")
        return (field_name, sentences, field_index)

    except Exception as e:
        print(f"Error processing field '{field_name}': {e}")
        return None


def segment_sentences(
    available_fields: Dict[str, str],
    nlp: spacy.Language
) -> Tuple[Dict, List[Dict]]:
    """Process all available fields in parallel and segment sentences.

    Args:
        available_fields: Dictionary of field names to text content
        nlp: Loaded spaCy model

    Returns:
        Tuple of (metadata dict, sentences list)
    """
    processed_fields = []
    all_sentences = []
    source_counts = {"headline": 0, "summary": 0, "content": 0}

    # Process fields in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(process_field, field_name, field_text, nlp): field_name
            for field_name, field_text in available_fields.items()
        }

        # Collect results, preserving order
        results = []
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

        # Sort by field_index to maintain consistent order: headline → summary → content
        results.sort(key=lambda x: x[2])

        # Process sorted results
        for field_name, sentences, _ in results:
            processed_fields.append(field_name)
            source_counts[field_name] = len(sentences)
            all_sentences.extend(sentences)

    # Identify missing fields
    all_field_names = ["headline", "summary", "content"]
    missing_fields = [f for f in all_field_names if f not in processed_fields]

    # Create metadata
    metadata = {
        "total_sentences": len(all_sentences),
        "source_counts": source_counts,
        "processed_fields": processed_fields,
        "missing_fields": missing_fields
    }

    return metadata, all_sentences


def create_output_mapping(
    metadata: Dict,
    sentences: List[Dict]
) -> Dict:
    """Create final output structure with sequential IDs.

    Args:
        metadata: Metadata dictionary
        sentences: List of sentence dictionaries

    Returns:
        Complete output dictionary with sequential IDs
    """
    output_sentences = []
    for idx, sent in enumerate(sentences):
        output_sent = {
            "id": idx,
            "text": sent["text"],
            "source": sent["source"],
            "char_start": sent["char_start"],
            "char_end": sent["char_end"]
        }
        output_sentences.append(output_sent)

    return {
        "metadata": metadata,
        "sentences": output_sentences
    }


def save_output(
    input_path: Path,
    output_data: Dict
) -> Path:
    """Save output to JSON file.

    Args:
        input_path: Path to the input JSON file
        output_data: Data to save

    Returns:
        Path to created output file
    """
    # Generate output filename by inserting _sentences before .json
    input_filename = input_path.stem
    output_filename = f"{input_filename}_sentences.json"
    output_path = input_path.parent / output_filename

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"Output saved to: {output_filename}")
    return output_path


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Segment sentences from JSON input file using spaCy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python SentenceSplitter.py --input FEED_28-jan-2026_cleaned_pronouns.json
  python SentenceSplitter.py --input /path/to/news_feed.json
        """
    )

    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to JSON input file (must contain headline, summary, and/or content fields)"
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()

    print(f"Loading spaCy model...")
    nlp = load_spacy_model()

    print(f"Processing JSON input: {input_path.name}")

    # Load and validate JSON input
    available_fields = load_json_input(input_path)

    print(f"Available fields: {', '.join(available_fields.keys())}")

    # Segment sentences from all available fields
    metadata, sentences = segment_sentences(available_fields, nlp)

    if not sentences:
        print("Error: No sentences were extracted from input.")
        sys.exit(1)

    # Add input file to metadata
    metadata["input_file"] = input_path.name

    # Create output structure
    output_data = create_output_mapping(metadata, sentences)

    # Save results
    output_path = save_output(input_path, output_data)

    print(f"\nProcessing complete!")
    print(f"Total sentences: {metadata['total_sentences']}")
    print(f"Source breakdown: {metadata['source_counts']}")

    if metadata['missing_fields']:
        print(f"Missing fields: {', '.join(metadata['missing_fields'])}")


if __name__ == "__main__":
    main()
