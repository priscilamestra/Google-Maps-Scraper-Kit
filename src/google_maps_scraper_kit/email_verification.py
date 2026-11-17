"""Optional public DNS checks, scoped to a saved list; never claims mailbox deliverability."""
import json
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode

from .db import connect, list_campaign_results


def check_domain(domain, fetcher=None):
    if fetcher is None:
        from .website import _fetch
        fetcher = _fetch
    def query(kind):
        _url, _mime, body = fetcher("https://dns.google/resolve?"+urlencode({"name": domain.encode("idna").decode(), "type": kind}))
        return json.loads(body)
    try:
        response = query("MX")
        status = response.get("Status")
        if status == 3:
            return {"status": "invalid", "mail_host": "none", "error": "NXDOMAIN"}
        if status != 0:
            raise ValueError(f"DNS status {status}")
        hosts = [answer["data"].split()[-1].rstrip(".").lower() for answer in response.get("Answer", []) if answer.get("type") == 15]
        if hosts and not any(hosts):
            return {"status": "invalid", "mail_host": "none", "error": "null MX"}
        if hosts:
            provider = "google_workspace" if any(h.endswith(".google.com") or h.endswith(".googlemail.com") or h == "smtp.google.com" for h in hosts) else "microsoft_365" if any(h.endswith(".mail.protection.outlook.com") for h in hosts) else "other"
            return {"status": "valid", "mail_host": provider, "error": None}
        # SMTP permits an implicit MX on A/AAAA; no MX alone is not proof of a dead domain.
        for kind, record in (("A", 1), ("AAAA", 28)):
            fallback = query(kind)
            if fallback.get("Status") not in {0, 3}:
                raise ValueError("DNS fallback unavailable")
            if any(answer.get("type") == record for answer in fallback.get("Answer", [])):
                return {"status": "implicit_mx", "mail_host": "other", "error": None}
        return {"status": "invalid", "mail_host": "none", "error": "no MX, A or AAAA"}
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        return {"status": "unknown", "mail_host": None, "error": str(error)[:500]}


def verify_campaign_emails(path, campaign_id, checker=check_domain):
    emails = {email.strip() for row in list_campaign_results(path, campaign_id) for email in row["published_emails"].split(",") if email.strip()}
    with closing(connect(path)) as connection:
        cached = {row[0] for row in connection.execute("SELECT email FROM email_checks WHERE campaign_id=? AND status!='unknown'", (campaign_id,))}
    domains = {}
    unknown = 0
    for email in sorted(emails-cached):
        domain = email.rsplit("@", 1)[-1]
        if domain not in domains:
            domains[domain] = checker(domain)
        check = domains[domain]
        unknown += check["status"] == "unknown"
        with closing(connect(path)) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO email_checks(campaign_id,email,status,mail_host,error,checked_at) VALUES(?,?,?,?,?,?)",
                               (campaign_id, email, check["status"], check.get("mail_host"), check.get("error"), datetime.now(timezone.utc).isoformat()))
    return unknown
