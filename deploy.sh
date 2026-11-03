#!/usr/bin/env bash
# Zet de Leverplanner online op Google Cloud Run (project houtenhak-leverplanner), voor de Route-tab
# in het beheer van Houtenhak. Draai dit zelf, ingelogd met `gcloud auth login`:
#
#   ./deploy.sh
#
# Google bouwt het programma (Cloud Build), bewaart het (Artifact Registry) en start het als dienst
# "leverplanner" in Nederland (europe-west4). Instellingen staan in deploy/env.yaml.
set -euo pipefail
cd "$(dirname "$0")"

PROJECT=houtenhak-leverplanner
REGION=europe-west4
SERVICE=leverplanner
ACCOUNT="$SERVICE@$PROJECT.iam.gserviceaccount.com"

gcloud projects describe "$PROJECT" --format="value(projectId)" >/dev/null

# Eigen hulpaccount zonder rechten: de planner hoeft nergens bij in Google Cloud, alleen naar buiten.
if ! gcloud iam service-accounts describe "$ACCOUNT" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE" --project "$PROJECT" --display-name "Leverplanner (geen rechten)"
fi

# Iedereen mag de dienst bereiken (de browser van de beheerder), maar de planner zelf laat
# alleen ingelogde beheerders van Houtenhak toe (app/auth.py). Eén exemplaar tegelijk houdt de
# kosten binnen het gratis deel en de OSRM-rem (1 verzoek per seconde) voor iedereen samen.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$ACCOUNT" \
  --allow-unauthenticated \
  --env-vars-file deploy/env.yaml \
  --cpu 1 --memory 1Gi \
  --min-instances 0 --max-instances 1 --concurrency 4 \
  --timeout 120 \
  --quiet

# Alleen de laatste versies van het programma bewaren (opslag boven 0,5 GB kost geld).
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --project "$PROJECT" --location "$REGION" \
  --policy deploy/cleanup-policy.json --no-dry-run --quiet >/dev/null

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format "value(status.url)")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL/api/settings" || true)
echo
echo "Online: $URL"
if [ "$STATUS" = "401" ]; then
  echo "Controle: zonder inloggen geweigerd (401), zoals het hoort."
else
  echo "LET OP: zonder inloggen gaf de planner $STATUS in plaats van 401. Controleer deploy/env.yaml."
fi
