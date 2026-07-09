#!/usr/bin/env bash
set -euo pipefail

queues=(
  flashback-extraction
  flashback-embedding
  flashback-artifact
  flashback-thread-detector
  flashback-trait-synthesizer
  flashback-profile-summary
  flashback-producers-per-session
  flashback-producers-weekly
  flashback_agent-profile-picture
  flashback-tribute-render
  flashback-storybook-render
)

for queue in "${queues[@]}"; do
  awslocal sqs create-queue --queue-name "$queue" >/dev/null
done

awslocal sqs list-queues
