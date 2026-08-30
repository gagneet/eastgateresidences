#!/bin/bash

##############################################################################
# Comprehensive Playwright Test Runner
#
# This script runs all Playwright tests for the East Gate Residences application
# Usage:
#   ./tests/run-all-tests.sh [options]
#
# Options:
#   --quick       Run only critical tests
#   --full        Run all tests (default)
#   --ui          Run with UI mode
#   --headed      Run in headed mode (show browser)
#   --debug       Run in debug mode
#   --report      Generate and show HTML report
#   --ci          CI mode (no interactive prompts)
#   --project     Specify browser (chromium, firefox, webkit)
##############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
MODE="full"
UI_MODE=false
HEADED=false
DEBUG=false
SHOW_REPORT=false
CI_MODE=false
PROJECT="chromium"
BASE_URL="${BASE_URL:-https://eastgateresidences.com.au}"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --quick)
      MODE="quick"
      shift
      ;;
    --full)
      MODE="full"
      shift
      ;;
    --ui)
      UI_MODE=true
      shift
      ;;
    --headed)
      HEADED=true
      shift
      ;;
    --debug)
      DEBUG=true
      shift
      ;;
    --report)
      SHOW_REPORT=true
      shift
      ;;
    --ci)
      CI_MODE=true
      shift
      ;;
    --project)
      PROJECT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  East Gate Residences - Playwright Test Suite${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Mode: ${YELLOW}${MODE}${NC}"
echo -e "  Base URL: ${YELLOW}${BASE_URL}${NC}"
echo -e "  Browser: ${YELLOW}${PROJECT}${NC}"
echo -e "  UI Mode: ${YELLOW}${UI_MODE}${NC}"
echo -e "  Headed: ${YELLOW}${HEADED}${NC}"
echo -e "  Debug: ${YELLOW}${DEBUG}${NC}"
echo ""

# Check if Playwright is installed
if ! command -v npx &> /dev/null; then
    echo -e "${RED}Error: npx not found. Please install Node.js${NC}"
    exit 1
fi

# Check if @playwright/test is installed
if ! npm list @playwright/test &> /dev/null; then
    echo -e "${YELLOW}Installing Playwright...${NC}"
    npm install -D @playwright/test
    npx playwright install ${PROJECT}
fi

# Build command
CMD="npx playwright test --project=${PROJECT}"

if [ "$UI_MODE" = true ]; then
    CMD="$CMD --ui"
fi

if [ "$HEADED" = true ]; then
    CMD="$CMD --headed"
fi

if [ "$DEBUG" = true ]; then
    CMD="$CMD --debug"
fi

if [ "$CI_MODE" = true ]; then
    CMD="$CMD --reporter=json,html"
fi

# Test suites
CRITICAL_TESTS=(
    "tests/e2e/auth-flows.spec.js"
    "tests/e2e/unit-management.spec.js"
    "tests/e2e/super-admin.spec.js"
    "tests/e2e/regression.spec.js"
)

ALL_TESTS=(
    "tests/e2e/auth-flows.spec.js"
    "tests/e2e/unit-management.spec.js"
    "tests/e2e/api-endpoints.spec.js"
    "tests/e2e/super-admin.spec.js"
    "tests/e2e/dashboard-ui.spec.js"
    "tests/e2e/payment-features.spec.js"
    "tests/e2e/regression.spec.js"
    "tests/console-errors.spec.js"
    "tests/financial-data-handling.spec.js"
    "tests/build-validation.spec.js"
)

# Select test files
if [ "$MODE" = "quick" ]; then
    TEST_FILES="${CRITICAL_TESTS[@]}"
    echo -e "${GREEN}Running critical tests only...${NC}"
else
    TEST_FILES="${ALL_TESTS[@]}"
    echo -e "${GREEN}Running all tests...${NC}"
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Starting Tests${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Run tests
START_TIME=$(date +%s)

if [ "$MODE" = "quick" ]; then
    $CMD ${TEST_FILES[@]}
else
    $CMD
fi

EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Test Results${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    echo -e "${GREEN}  Duration: ${DURATION}s${NC}"
else
    echo -e "${RED}✗ Some tests failed${NC}"
    echo -e "${RED}  Exit code: ${EXIT_CODE}${NC}"
    echo -e "${RED}  Duration: ${DURATION}s${NC}"
fi

echo ""

# Show report if requested
if [ "$SHOW_REPORT" = true ]; then
    echo -e "${GREEN}Opening test report...${NC}"
    npx playwright show-report
fi

# Summary
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Test Coverage Summary${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}✓${NC} Authentication & Registration"
echo -e "  ${GREEN}✓${NC} Unit Management (UA/TH format)"
echo -e "  ${GREEN}✓${NC} API Endpoints (all major endpoints)"
echo -e "  ${GREEN}✓${NC} SuperAdmin Dashboard (11 cards)"
echo -e "  ${GREEN}✓${NC} Dashboard UI/UX"
echo -e "  ${GREEN}✓${NC} Payment Features"
echo -e "  ${GREEN}✓${NC} Regression Tests (known bugs)"
echo -e "  ${GREEN}✓${NC} Console Error Detection"
echo -e "  ${GREEN}✓${NC} Financial Data Handling"
echo -e "  ${GREEN}✓${NC} Build Validation"
echo ""

# Report location
echo -e "${YELLOW}Test artifacts:${NC}"
echo -e "  Report: ${BLUE}tests/reports/html/index.html${NC}"
echo -e "  Screenshots: ${BLUE}tests/artifacts/${NC}"
echo -e "  Videos: ${BLUE}tests/artifacts/${NC}"
echo ""

exit $EXIT_CODE
