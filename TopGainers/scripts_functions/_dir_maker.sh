#!/bin/bash

# dir_maker.sh - Create a directory with date-based name (DD-MMM-YYYY format)
# Usage: ./dir_maker.sh --location /path/to/parent/directory

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display usage
usage() {
    cat <<EOF
Usage: $0 --location PATH

Create a directory with date-based name in DD-MMM-YYYY format.

Options:
  --location PATH    Parent directory where the date-named directory will be created (required)
  -h, --help         Show this help message

Examples:
  $0 --location /home/user/backups
  $0 --location ./data
  $0 --location /var/log/trading

Notes:
  - Directory name format: DD-MMM-YYYY (e.g., 13-Nov-2025)
  - Uses current system date for directory naming
  - If directory already exists, script will skip creation and notify you
  - Parent directory must exist and be writable
EOF
    exit 0
}

# Function to print error messages
error() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

# Function to print info messages
info() {
    echo -e "${BLUE}$1${NC}"
}

# Function to print success messages
success() {
    echo -e "${GREEN}$1${NC}"
}

# Function to print warning messages
warning() {
    echo -e "${YELLOW}$1${NC}"
}

# Function to get current date in DD-MMM-YYYY format
get_date_dirname() {
    date +"%d-%b-%Y"
}

# Function to validate location path
validate_location() {
    local location="$1"

    # Check if parent directory exists
    if [[ ! -d "$location" ]]; then
        error "Location does not exist: $location"
    fi

    # Check if parent directory is writable
    if [[ ! -w "$location" ]]; then
        error "Location is not writable: $location"
    fi

    return 0
}

# Parse command line arguments
LOCATION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --location)
            LOCATION="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            error "Unknown option: $1. Use --help for usage information."
            ;;
    esac
done

# Validate required arguments
if [[ -z "$LOCATION" ]]; then
    error "Missing required argument: --location"
fi

# Validate location path
validate_location "$LOCATION"

# Get current date in DD-MMM-YYYY format
DATE_DIR=$(get_date_dirname)
FULL_PATH="${LOCATION}/${DATE_DIR}"

# Display what we're about to do
info "Creating directory: $FULL_PATH"

# Check if directory already exists
if [[ -d "$FULL_PATH" ]]; then
    warning "Directory already exists: $FULL_PATH"
    info "Skipping creation - directory is already present."
    exit 0
fi

# Create the directory
mkdir "$FULL_PATH"

# Verify creation was successful
if [[ -d "$FULL_PATH" ]]; then
    success "========================================="
    success "Directory created successfully!"
    success "========================================="
    success "Path: $FULL_PATH"
else
    error "Failed to create directory: $FULL_PATH"
fi

exit 0
