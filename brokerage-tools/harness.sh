#!/usr/bin/env bash
#
# The agentic test harness.
#
#   ./harness.sh                 every suite, offline, from the cassettes in
#                                test-harness/src/main/resources/cassettes
#   ./harness.sh --live          every suite against Claude; needs ANTHROPIC_API_KEY
#   ./harness.sh --record        against Claude, replacing the cassettes
#   ./harness.sh --suite trading just the suites whose file name contains "trading"
#   ./harness.sh --model claude-sonnet-5-5   a different model
#
# Reports land in test-harness/target/reports/. Exit status is 1 if any scenario failed.
#
set -euo pipefail
cd "$(dirname "$0")"
./mvnw -q -pl test-harness -am install -DskipTests
./mvnw -q -pl test-harness exec:java \
  -Dexec.mainClass=com.example.brokerage.harness.HarnessMain \
  -Dexec.args="$*"
