#!/usr/bin/env python3
"""
pronounCer - Client Script for Coreference Resolution

Reads a single JSON file containing headline, summary, and/or content fields,
sends each present field to the pronounCer service for coreference resolution
in parallel, and writes the results to a single output JSON file.

Usage:
    python pronounCer.py --inputs FEED_28-jan-2026_cleaned.json

Input:  FEED_28-jan-2026_cleaned.json          (one or more of headline/summary/content)
Output: FEED_28-jan-2026_cleaned_pronouns.json (same keys, with coreferences resolved)

Note: The pronounCer service must be running before using this script.
Start it with: python pronounCer_service.py
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Service configuration
SERVICE_URL = "http://localhost:5050"
SERVICE_HEALTH_CHECK = f"{SERVICE_URL}/health"
SERVICE_RESOLVE = f"{SERVICE_URL}/resolve"
SERVICE_CONFIG = f"{SERVICE_URL}/config"

# Recognised JSON fields (in processing order)
KNOWN_FIELDS = ["headline", "summary", "content"]


def check_service_running(timeout=2):
    """
    Check if the pronounCer service is running.

    Returns:
        True if service is running, False otherwise
    """
    try:
        response = requests.get(SERVICE_HEALTH_CHECK, timeout=timeout)
        return response.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def configure_service(mode):
    """
    Configure the pronounCer service resolution mode.

    Args:
        mode: "simple" or "full"

    Returns:
        True if configuration successful, False otherwise
    """
    try:
        response = requests.post(
            SERVICE_CONFIG,
            json={"mode": mode},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            logger.info(f"Service configured to '{data.get('mode')}' mode")
            if mode == "full" and not data.get('fastcoref_available'):
                logger.warning("="*60)
                logger.warning("FALLBACK TO SIMPLE MODE")
                logger.warning("Full mode requested but fastcoref is not installed")
                logger.warning("Install with: pip3 install --break-system-packages fastcoref")
                logger.warning("Simple mode only resolves pronouns, not noun phrases like 'The company'")
                logger.warning("="*60)
            return True
        else:
            logger.error(f"Failed to configure service: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Error configuring service: {e}")
        return False


def load_and_validate_json(json_path):
    """
    Load the input JSON and return only the recognised text fields.

    Args:
        json_path: Path to the input .json file

    Returns:
        dict: Subset of the JSON containing only recognised fields
              (e.g. {"headline": "...", "content": "..."})

    Raises:
        SystemExit: If the file is missing, not valid JSON, not a dict,
                    or contains none of the recognised fields.
    """
    path = Path(json_path)

    if not path.exists():
        logger.error(f"Input file not found: {path}")
        sys.exit(1)

    if path.suffix != '.json':
        logger.error(f"Input file must be a .json file: {path}")
        sys.exit(1)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        logger.error(f"Input JSON must be an object (dict), got {type(data).__name__}")
        sys.exit(1)

    fields = {k: v for k, v in data.items() if k in KNOWN_FIELDS}

    if not fields:
        logger.error(f"No recognised fields found in {path}")
        logger.error(f"  Expected at least one of: {KNOWN_FIELDS}")
        logger.error(f"  Found keys: {list(data.keys())}")
        sys.exit(1)

    return fields


def resolve_field(field_name, text):
    """
    Send a single text field to the service for resolution.

    Args:
        field_name: Key name (e.g. "headline", "summary", "content")
        text: The text to resolve

    Returns:
        dict: {"field": field_name, "status": "success"|"error",
               "resolved_text": str, "message": str}
    """
    if not text.strip():
        return {"field": field_name, "status": "success",
                "resolved_text": text, "message": f"Skipped (empty): {field_name}"}

    try:
        response = requests.post(
            SERVICE_RESOLVE,
            json={"text": text},
            timeout=30
        )
    except requests.Timeout:
        return {"field": field_name, "status": "error",
                "resolved_text": "", "message": f"Service timeout for {field_name}"}
    except requests.ConnectionError:
        return {"field": field_name, "status": "error",
                "resolved_text": "", "message": f"Service connection error for {field_name}"}

    if response.status_code != 200:
        return {"field": field_name, "status": "error",
                "resolved_text": "", "message": f"Service error ({response.status_code}) for {field_name}"}

    try:
        data = response.json()
        resolved_text = data.get("resolved_text", "")
    except json.JSONDecodeError as e:
        return {"field": field_name, "status": "error",
                "resolved_text": "", "message": f"Invalid JSON response for {field_name}: {e}"}

    return {"field": field_name, "status": "success",
            "resolved_text": resolved_text,
            "message": f"Resolved {field_name}: {len(resolved_text)} chars"}


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Resolve coreferences in a JSON file using the pronounCer service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pronounCer.py --inputs FEED_28-jan-2026_cleaned.json
  python pronounCer.py --inputs /path/to/FEED_28-jan-2026_cleaned.json --mode full

The input JSON must contain at least one of: headline, summary, content.
The output JSON will contain only the fields that were present in the input.

Note: The pronounCer service must be running first.
Start it with: python pronounCer_service.py
        """
    )

    parser.add_argument(
        '--inputs',
        type=str,
        required=True,
        help='Path to the input .json file (e.g., FEED_28-jan-2026_cleaned.json)'
    )

    parser.add_argument(
        '--mode',
        choices=['simple', 'full'],
        default='simple',
        help='Resolution mode: simple (pronouns only) or full (all coreferences). Default: simple'
    )

    args = parser.parse_args()
    json_path = args.inputs
    mode = args.mode

    # Derive output path: strip .json, append _pronouns.json
    output_path = str(Path(json_path).with_suffix("")) + "_pronouns.json"

    logger.info(f"pronounCer Client - Input:  {json_path}")
    logger.info(f"pronounCer Client - Output: {output_path}")
    logger.info(f"Resolution mode: {mode}")

    # Check if service is running
    if not check_service_running():
        logger.error("pronounCer service is not running!")
        logger.error(f"\nStart the service with:")
        logger.error(f"  python pronounCer_service.py")
        logger.error(f"\nThen run this script again.")
        sys.exit(1)

    logger.info("Service health check: OK")

    # Configure service mode
    if not configure_service(mode):
        logger.error("ERROR: Failed to configure service")
        sys.exit(1)

    # Load and validate input JSON
    fields = load_and_validate_json(json_path)
    logger.info(f"Input fields detected: {list(fields.keys())}")

    # Resolve fields in parallel
    logger.info(f"Processing {len(fields)} field(s) in parallel...")

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(resolve_field, name, text): name
            for name, text in fields.items()
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            if result["status"] == "success":
                logger.info(f"  {result['field']}: {result['message']}")
            else:
                logger.error(f"  {result['field']}: {result['message']}")

    # Summary
    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]

    if failed:
        logger.error(f"\nResults: {len(successful)} succeeded, {len(failed)} failed")
        logger.error("Some fields failed to resolve. See errors above.")
        sys.exit(1)

    # Build output JSON preserving input key order
    output = {r["field"]: r["resolved_text"] for r in sorted(results, key=lambda r: KNOWN_FIELDS.index(r["field"]))}

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"\nAll {len(successful)} field(s) resolved successfully")
    logger.info(f"Output written to: {output_path}")

    sys.exit(0)


if __name__ == '__main__':
    main()
