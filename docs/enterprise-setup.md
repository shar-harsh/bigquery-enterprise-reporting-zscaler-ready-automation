# Enterprise Setup Guide (Portable Cloud SDK)

## 1) Install using portable ZIP (no installer)
If you cannot install gcloud CLI via an installer, use the official **versioned archive** ZIP.
Extract it anywhere (example: `C:\tools\google-cloud-sdk`).

Then set these paths in `bq_automation.py`:
- `BQ = r"...\google-cloud-sdk\bin\bq.cmd"`
- `GSUTIL` and `GCLOUD` are derived automatically

## 2) Proxy configuration
Use environment variables first, and fall back to gcloud proxy properties if needed.

## 3) Trusted certificates
If TLS inspection is enabled, configure `core/custom_ca_certs_file`.

## 4) Zscaler
See **zscaler-setup.md** for the recommended default enterprise setup.
