# SentenceSplitter.py Implementation Guide

## Overview

**SentenceSplitter.py** is a sentence segmentation tool that processes JSON input files containing text fields and performs sentence boundary detection using spaCy. It was refactored from a file-based system (separate text files for headline, summary, content) to a modern JSON-based input system.

### Key Improvements
- **Single JSON Input**: Replaces multiple text files with a single JSON input file
- **Dynamic Field Handling**: Processes only the fields present in the JSON (headline, summary, content are optional)
- **Parallel Processing**: Uses ThreadPoolExecutor to process multiple fields concurrently
- **Sequential Output**: Maintains consistent ordering (headline → summary → content) regardless of processing order
- **Rich Metadata**: Tracks source origin, character positions, and field availability

## Architecture

### Main Components

#### 1. **load_spacy_model()** (lines 29-45)
Loads the spaCy English model for sentence boundary detection.
- Model: `en_core_web_sm`
- Handles model download/installation errors gracefully
- Required for accurate sentence segmentation

#### 2. **load_json_input()** (lines 48-87)
Loads and validates the JSON input file.
- **Input**: Path to JSON file
- **Output**: Dictionary with available text fields
- **Validation**:
  - File must exist and be readable
  - JSON must be valid
  - Must contain at least one of: `headline`, `summary`, `content`
  - Fields are extracted in consistent order (headline → summary → content)
- **Error Handling**: Exits gracefully with descriptive error messages

#### 3. **process_field()** (lines 90-141)
Processes individual text fields and segments sentences.
- **Input**: Field name, text content, spaCy model
- **Output**: Tuple of (field_name, sentences_list, field_index) or None
- **Processing**:
  - Uses spaCy's sentence segmentation
  - Extracts sentence boundaries using character positions
  - Maintains sentence index within field
  - Gracefully handles empty fields
- **Concurrency**: Designed for parallel execution via ThreadPoolExecutor

#### 4. **segment_sentences()** (lines 144-196)
Orchestrates parallel processing of all available fields.
- **Concurrency**: Uses ThreadPoolExecutor with max_workers=3
- **Ordering**: Sorts results by field_index (0=headline, 1=summary, 2=content)
- **Metadata Creation**: Tracks:
  - Total sentence count
  - Sentence count per source field
  - Which fields were processed
  - Which fields are missing
- **Returns**: (metadata_dict, sentences_list) tuple

#### 5. **create_output_mapping()** (lines 199-226)
Creates the final output structure with sequential IDs.
- Assigns unique `id` field to each sentence
- Strips `sent_idx` field from individual sentences
- Maintains all essential metadata

#### 6. **save_output()** (lines 229-251)
Saves output to JSON file.
- **Output Naming**: `{input_filename}_sentences.json`
  - Example: `FEED_28-jan-2026_cleaned_pronouns.json` → `FEED_28-jan-2026_cleaned_pronouns_sentences.json`
- **Formatting**: Pretty-printed JSON with 2-space indentation
- **Encoding**: UTF-8 with `ensure_ascii=False` for proper character handling

#### 7. **main()** (lines 254-309)
CLI entry point that orchestrates the entire workflow.

## JSON Input File Structure

### Required Format
```json
{
  "headline": "Optional headline text",
  "summary": "Optional summary text",
  "content": "Optional content text"
}
```

### Field Requirements
- **At least one field must be present** (headline, summary, or content)
- Fields can be `null`, empty string `""`, or absent from JSON
- Fields with falsy values (null, empty string) are automatically skipped
- Text content should be plain text (not HTML or markdown)

### Example Variations

**All three fields:**
```json
{
  "headline": "News Headline",
  "summary": "Brief summary",
  "content": "Full article content"
}
```

**Only headline and summary:**
```json
{
  "headline": "News Headline",
  "summary": "Brief summary"
}
```

**Only headline:**
```json
{
  "headline": "News Headline"
}
```

**Skipping fields with null:**
```json
{
  "headline": "News Headline",
  "summary": null,
  "content": "Full article content"
}
```

## Output File Format

### Structure
```json
{
  "metadata": {
    "total_sentences": 12,
    "source_counts": {
      "headline": 1,
      "summary": 1,
      "content": 10
    },
    "processed_fields": ["headline", "summary", "content"],
    "missing_fields": [],
    "input_file": "FEED_28-jan-2026_cleaned_pronouns.json"
  },
  "sentences": [
    {
      "id": 0,
      "text": "First sentence from headline",
      "source": "headline",
      "char_start": 0,
      "char_end": 30
    },
    {
      "id": 1,
      "text": "First sentence from summary",
      "source": "summary",
      "char_start": 0,
      "char_end": 27
    }
  ]
}
```

### Metadata Fields
- **total_sentences**: Total number of extracted sentences
- **source_counts**: Dictionary with sentence counts per field
- **processed_fields**: List of fields that were successfully processed
- **missing_fields**: List of fields that were not present or were empty
- **input_file**: Original input JSON filename

### Sentence Fields
- **id**: Sequential unique identifier (0, 1, 2, ...)
- **text**: Full sentence text as extracted by spaCy
- **source**: Field where sentence came from (headline, summary, or content)
- **char_start**: Character position relative to the start of that field
- **char_end**: Character position relative to the start of that field

### Ordering
Sentences are ordered as: headline sentences → summary sentences → content sentences

Character positions (`char_start`, `char_end`) reset for each field (they're relative to each field's text, not the entire document).

## Usage

### Basic Usage
```bash
# Process a single JSON file
python3 SentenceSplitter.py --input FEED_28-jan-2026_cleaned_pronouns.json

# Process from different directory
python3 SentenceSplitter.py --input /path/to/news_feed.json
```

### Requirements
1. Python 3.7+
2. spaCy with English model installed:
   ```bash
   pip install spacy
   python -m spacy download en_core_web_sm
   ```

### Output
- Console output with processing progress
- Output JSON file in same directory as input: `{input}_sentences.json`

## Error Handling and Validation

### Input File Validation
1. **File exists**: Checked with `Path.exists()`
2. **Readable**: Attempts to open with UTF-8 encoding
3. **Valid JSON**: Validated with `json.load()`
4. **Required fields**: At least one of (headline, summary, content) must be present
5. **Non-empty content**: Empty strings are skipped with warning

### Processing Errors
- Empty text fields generate a warning but don't stop processing
- Other fields continue processing even if one fails
- Processing errors are caught and logged but don't halt execution
- Missing spaCy model suggests installation instructions

### Output Validation
- Checks that sentences were extracted (fails if result is empty)
- Validates output directory is writable
- Creates new file (overwrites if exists)

## Implementation Notes

### Threading Strategy
- Uses `ThreadPoolExecutor` with `max_workers=3` (one per field type)
- Results collected via `as_completed()` to process as soon as ready
- Results re-sorted by field_index to maintain consistent ordering
- This approach ensures deterministic output despite parallel processing

### spaCy Sentence Segmentation
- Uses spaCy's built-in dependency parser for sentence boundaries
- Respects abbreviations and special cases through spaCy's model
- Character positions are preserved from original text
- More accurate than simple punctuation-based splitting

### Character Position Tracking
- `char_start` and `char_end` are provided by spaCy's `Span` object
- Positions are relative to each field's text independently
- Enables precise re-location of sentences in original field text
- Useful for downstream processing or HTML/markdown annotation

### Unicode Handling
- Uses UTF-8 encoding for all file I/O
- `ensure_ascii=False` in JSON output preserves special characters
- Handles international text properly without escaping

## Testing Scenarios

### Test Case 1: All Fields Present
**Input**: JSON with headline, summary, and content
**Expected**:
- All three fields processed
- Sentences grouped by source
- Metadata shows 0 missing fields

### Test Case 2: Subset of Fields
**Input**: JSON with only headline and summary
**Expected**:
- Only headline and summary processed
- Content field in missing_fields
- Source breakdown shows 0 for content

### Test Case 3: Single Field
**Input**: JSON with only headline
**Expected**:
- Only headline processed
- Summary and content in missing_fields
- Total_sentences = 1 (assuming headline is single sentence)

### Test Case 4: Empty Fields
**Input**: JSON with null or empty string values
**Expected**:
- Empty fields skipped with warning
- Only non-empty fields processed
- Output matches fields with content

### Test Case 5: Output Naming
**Input**: `my_news_feed.json`
**Expected Output File**: `my_news_feed_sentences.json`

## Performance Characteristics

- **Concurrency Benefit**: Processing 3 fields in parallel typically 1.5-2x faster than sequential
- **Memory Usage**: Entire file loaded into memory; suitable for news articles (typical size < 100KB)
- **Sentence Count**: Typical news articles produce 5-20 sentences
- **Processing Time**: Usually completes in < 1 second for typical articles

## Future Enhancements

Potential improvements (not currently implemented):
- Batch processing multiple JSON files
- Configurable field priority ordering
- Custom sentence filtering rules
- Support for additional text fields
- Direct integration with NER processing pipeline
- Streaming output for very large files

## Troubleshooting

### "spaCy model not found"
```bash
python -m spacy download en_core_web_sm
```

### "Input file not found"
Verify file path and ensure it exists in the specified location

### "No sentences extracted"
Check that input JSON contains valid text in at least one field

### "Invalid JSON file"
Verify JSON syntax and formatting; use a JSON validator

### "Field is empty"
This is a warning; processing continues with other fields
