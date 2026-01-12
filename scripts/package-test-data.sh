#!/bin/bash
# Package ANSM test data for GitHub release upload
#
# This script creates a zip file of the test_data directory
# that can be uploaded as a GitHub release asset.
#
# Usage:
#   ./scripts/package-test-data.sh
#
# The resulting ansm-test-data.zip should be uploaded to a GitHub release
# tagged 'test-data' for the CI workflow to download.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TEST_DATA_DIR="$PROJECT_DIR/test_data"
OUTPUT_FILE="$PROJECT_DIR/ansm-test-data.zip"

if [ ! -d "$TEST_DATA_DIR" ]; then
    echo "Error: test_data directory not found at $TEST_DATA_DIR"
    exit 1
fi

echo "Packaging test data from: $TEST_DATA_DIR"

# Create zip file (from project root to preserve directory structure)
cd "$PROJECT_DIR"
zip -r "$OUTPUT_FILE" test_data -x "*.DS_Store" -x "*__pycache__*"

echo ""
echo "Created: $OUTPUT_FILE"
echo "Size: $(du -h "$OUTPUT_FILE" | cut -f1)"
echo ""
echo "Next steps:"
echo "1. Create a GitHub release with tag 'test-data'"
echo "2. Upload ansm-test-data.zip as a release asset"
echo "3. The NPS validation workflow will automatically download it"
