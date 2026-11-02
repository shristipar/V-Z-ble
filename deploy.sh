#!/usr/bin/env bash
# Deploys V-Z-ble to Google Cloud Run.
#
#   ./deploy.sh                       first deploy (asks for anything it needs)
#   ./deploy.sh                       later deploys: same command, it reuses your settings
#
# Optional settings (environment variables):
#   PROJECT_ID       your Google Cloud project (default: the current gcloud project)
#   REGION           default us-central1
#   SERVICE          default v-z-ble
#   PUBLIC_URL       your custom domain, e.g. https://getvzble.com (default: the Cloud Run address)
#   BILLING_ACCOUNT  billing account ID; if set, creates a $5/month budget alert (first run only)
#
# Safe defaults: at most 1 instance (caps cost, keeps the daily AI review limit accurate),
# scales to zero when idle, API key kept in Secret Manager.
set -euo pipefail

REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-v-z-ble}"
SECRET="anthropic-api-key"
cd "$(dirname "$0")"

command -v gcloud >/dev/null || { echo "Install the Google Cloud CLI first: https://cloud.google.com/sdk/docs/install"; exit 1; }
gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . || gcloud auth login

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "$PROJECT_ID" ]; then
  read -rp "Google Cloud project ID: " PROJECT_ID
fi
gcloud config set project "$PROJECT_ID" >/dev/null
echo "Deploying $SERVICE to project $PROJECT_ID in $REGION"

echo "Turning on the needed Google Cloud services (first run takes a minute)…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com --quiet

# Newer projects build with the default compute service account, which can't read the uploaded
# source until it has the Cloud Run builder role.
NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
BUILD_SA="$NUMBER-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --quiet --condition=None \
  --member="serviceAccount:$BUILD_SA" --role="roles/run.builder" >/dev/null

# The Anthropic API key, kept in Secret Manager. Skipping it leaves the AI review off.
SECRET_FLAG=()
if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  SECRET_FLAG=(--set-secrets "ANTHROPIC_API_KEY=$SECRET:latest")
else
  read -rsp "Anthropic API key (press Enter to skip and keep the AI review off): " KEY; echo
  if [ -n "$KEY" ]; then
    printf %s "$KEY" | gcloud secrets create "$SECRET" --data-file=- --replication-policy=automatic --quiet
    unset KEY
    gcloud secrets add-iam-policy-binding "$SECRET" --quiet \
      --member="serviceAccount:$BUILD_SA" \
      --role="roles/secretmanager.secretAccessor" >/dev/null
    SECRET_FLAG=(--set-secrets "ANTHROPIC_API_KEY=$SECRET:latest")
  fi
fi

ENV_VARS="HOSTED=1,REVIEWS_PER_DAY=${REVIEWS_PER_DAY:-3}"
[ -n "${PUBLIC_URL:-}" ] && ENV_VARS="$ENV_VARS,PUBLIC_URL=$PUBLIC_URL"

# Builds the Dockerfile with Cloud Build and deploys it.
gcloud run deploy "$SERVICE" --source . --region "$REGION" --quiet \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 40 \
  --cpu 1 --memory 512Mi --timeout 300 \
  --update-env-vars "$ENV_VARS" ${SECRET_FLAG[@]+"${SECRET_FLAG[@]}"}  # empty-safe on macOS bash 3.2

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format="value(status.url)")
if [ -z "${PUBLIC_URL:-}" ]; then
  # Share cards show the Cloud Run address until you add a custom domain.
  gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUBLIC_URL=$URL" --quiet >/dev/null
fi

if [ -n "${BILLING_ACCOUNT:-}" ]; then
  gcloud services enable billingbudgets.googleapis.com --quiet
  gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="V-Z-ble" \
    --budget-amount=5USD --threshold-rule=percent=0.2 --threshold-rule=percent=0.5 \
    --threshold-rule=percent=1.0 --quiet && echo "Budget alert set: emails at \$1, \$2.50 and \$5 a month."
fi

echo
echo "V-Z-ble is live at $URL"
echo "Next: tell Claude this address so the browser extension can send LinkedIn imports to it."
