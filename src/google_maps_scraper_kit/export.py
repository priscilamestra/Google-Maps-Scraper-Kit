"""Institutional CSV: one company per row; deprecated personal/scoring columns stay empty."""
from __future__ import annotations
import csv
import json
import os
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from .db import get_campaign, list_campaign_results, list_campaigns
from .contacts import normalize_phone, normalize_url

FIELDS = (
    "campaign_id",
    "campaign",
    "company_id",
    "name",
    "category",
    "city",
    "state",
    "address",
    "phone",
    "whatsapp",
    "website",
    "review_rating",
    "review_count",
    "emails",
    "instagram",
    "facebook",
    "linkedin",
    "source_urls",
    "latitude",
    "longitude",
    "maps_url",
    "place_id",
    "decision_maker_name",
    "decision_maker_title",
    "decision_maker_linkedin_url",
    "decision_maker_verification",
    "decision_maker_confidence",
    "fit_score",
    "confidence",
    "review_status",
    "exclusion_reason",
    "selected",
    "selection_reason",
    "collected_at",
    "phones",
    "tiktok",
    "youtube",
    "twitter",
    "x",
    "evidence_json",
    "schema_version",
    "website_scan_status",
    "website_scan_error",
    "website_pages",
    "website_scanned_at",
    "published_emails", "email_host", "email_checks_json", "maps_details_json",
)

DEPRECATED_FIELDS = ("decision_maker_name", "decision_maker_title", "decision_maker_linkedin_url",
                     "decision_maker_verification", "decision_maker_confidence", "fit_score", "confidence")


def export_rows(db_path, company_ids=None, campaign_id=None):
    allowed = None if company_ids is None else set(company_ids)
    campaigns = [get_campaign(db_path, campaign_id)] if campaign_id else list_campaigns(db_path)
    unique = {}
    for campaign in campaigns:
        for item in list_campaign_results(db_path, campaign["campaign_id"]):
            if item["company_id"] in unique or (allowed is not None and item["company_id"] not in allowed):
                continue
            row = dict.fromkeys(FIELDS, "")
            row.update({key: item.get(key, "") for key in FIELDS if key not in DEPRECATED_FIELDS})
            row.update(campaign_id=campaign["campaign_id"], campaign=campaign["name"],
                       city=campaign["config"].get("location", {}).get("city", ""),
                       state=campaign["config"].get("location", {}).get("state", ""),
                       phone=normalize_phone(item.get("phone")) or item["phones"].split(", ")[0],
                       website=normalize_url(item.get("website")), schema_version="2",
                       source_urls=" | ".join(item["source_urls"]),
                       evidence_json=json.dumps(item["evidence"], ensure_ascii=False),
                       email_checks_json=json.dumps(item.get("email_checks", []), ensure_ascii=False),
                       maps_details_json=json.dumps(item.get("maps_details", {}), ensure_ascii=False))
            unique[item["company_id"]] = row
    return list(unique.values())


def export_companies(db_path, output, company_ids=None, campaign_id=None):
    rows = export_rows(db_path, company_ids, campaign_id)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig", dir=destination.parent,
                                         prefix=destination.name+".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                # Spreadsheet formulas are untrusted source text, never executable CSV content.
                writer.writerow({key: ("'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@")) else value) for key,value in row.items()})
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(rows)


def export_xlsx(db_path, output, company_ids=None, campaign_id=None):
    """Write a dependency-free XLSX with the same columns and rows as the CSV export."""
    rows = export_rows(db_path, company_ids, campaign_id)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    def cell(value):
        text = str(value if value is not None else "")
        if text.lstrip().startswith(("=", "+", "-", "@")):
            text = "'" + text
        return f'<c t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'

    sheet_rows = [FIELDS, *(tuple(row.get(field, "") for field in FIELDS) for row in rows)]
    sheet = "".join(
        f'<row r="{index}">' + "".join(cell(value) for value in row) + "</row>"
        for index, row in enumerate(sheet_rows, 1)
    )
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as book:
            book.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            book.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            book.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Leads" sheetId="1" r:id="rId1"/></sheets></workbook>')
            book.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            book.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>{sheet}</sheetData><autoFilter ref="A1:AW{len(sheet_rows)}"/></worksheet>')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return len(rows)
