# GSTFlow Local MVP

Privacy-first GST invoice processing prototype. The first build uses **local Tesseract OCR** and normal application code; it does not require a paid AI API.

## What works

- Upload invoice images or PDFs
- OCR runs locally
- Extract GSTIN, invoice number/date, taxable value, CGST, SGST, IGST and total
- Match invoices to a client business by GSTIN
- Store each invoice under that business in SQLite
- Flag low-confidence / arithmetic mismatch invoices for review
- Human approval
- Excel export

## Run

```bash
cd gstflow_mvp
python -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000

## Local requirements

- Python 3.11+
- Tesseract OCR available as `tesseract`
- Poppler `pdftoppm` for PDFs

Install Python packages if needed:

```bash
pip install -r requirements.txt
```

## Security note

This is an MVP, not production-ready financial software. Before real client data: add proper authentication, tenant-level authorization, encrypted storage/backups, audit logging, malware scanning, secrets management, retention/deletion policies, and security testing.
