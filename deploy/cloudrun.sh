#!/usr/bin/env bash
# Deploy FlightSaver to Google Cloud Run (free tier covers personal use).
# Run in Google Cloud Shell (https://shell.cloud.google.com) from the repo root:
#   bash deploy/cloudrun.sh
set -euo pipefail

SERVICE=${SERVICE:-flightsaver}
REGION=${REGION:-europe-west2}   # London

PROJECT=$(gcloud config get-value project 2>/dev/null || true)
if [ -z "$PROJECT" ]; then
  echo "No project selected. Run: gcloud config set project <your-project-id>" >&2
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  read -rsp "Claude API key (ANTHROPIC_API_KEY): " ANTHROPIC_API_KEY; echo
fi
TOKEN=${FLIGHTSAVER_ACCESS_TOKEN:-$(openssl rand -hex 8)}

echo "Enabling Cloud Run and Cloud Build in $PROJECT ..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# 2 GiB fits two headless Chromium pages; one instance keeps chat sessions in
# one place and caps cost; scale to zero when idle.
ENV="FLIGHTSAVER_ACCESS_TOKEN=$TOKEN,FLIGHTSAVER_BROWSER_CONCURRENCY=2"
ENV="$ENV,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,FLIGHTSAVER_HISTORY=/tmp/history.sqlite3"
if [ -n "${TYPESAFE_API_KEY:-}" ]; then ENV="$ENV,TYPESAFE_API_KEY=$TYPESAFE_API_KEY"; fi

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --memory 2Gi --cpu 2 \
  --min-instances 0 --max-instances 1 \
  --timeout 300 \
  --allow-unauthenticated \
  --set-env-vars "$ENV"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
echo
echo "FlightSaver is live: $URL"
echo "Access token (enter it on first visit): $TOKEN"
