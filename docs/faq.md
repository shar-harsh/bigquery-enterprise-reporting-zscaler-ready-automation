# FAQ

## Is this a reporting tool or an ETL framework?
This is a **reporting automation** tool: schedule SQL report exports and deliver CSV outputs reliably.

## Does it require pandas or other packages?
No. It is intentionally **stdlib-only** to be enterprise-friendly.

## Does it require a service account?
Not required. It can use an interactive `gcloud auth login` session.

## Where are outputs stored?
`output_folder/dd.mm.yy/` per job.

## How are temporary resources cleaned?
- BigQuery temp tables prefixed `TEMP_AUTO_` are cleaned if stale.
- GCS temp exports matching `TEMP_AUTO_*.csv` are cleaned if stale.
- Local temp CSVs are cleaned if stale.
