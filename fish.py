#!/usr/bin/env python3
"""Fi$h API CLI Tool - interact with the Fi$h accounting API."""

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.env")
DEFAULT_ORG_ID = "1"
VENDOR_DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vendors.json")

# Canonical vendor names mapped from raw TSV aliases
VENDOR_ALIASES = {
    "Ace Hardware": ["Ace Hardware"],
    "Airbnb": ["AirBnb"],
    "Amazon": ["Amazon"],
    "Anderson Law": ["Anderson"],
    "ARCO": ["Arco?"],
    "Ashland Mobile": ["ashland mobile"],
    "Best Buy": ["Best Buy"],
    "Anthropic (Claude)": ["Claude", "claud"],
    "DigitalOcean": ["Digital Ocean"],
    "Discord / Mitch Ray": ["Discord Mitch Ray", "mitchray TA"],
    "Enterprise Rent-A-Car": ["Enterprise Rentacar"],
    "Eugene Chevron": ["Eugene Chevron"],
    "EventHelper": ["EventHelper"],
    "Meta (Facebook)": ["Facebook Vendor"],
    "Fiverr": ["Fiverr"],
    "GoDaddy": ["Godaddy"],
    "Goodstack": ["goodstack"],
    "Harbor Freight": ["Harbor Freight"],
    "Harland Clarke": ["Harlond Clarke"],
    "ITU Online Training": ["ITU", "ITU Training", "itu online"],
    "Jess Langpap": ["Jess Langpap"],
    "JetBrains": ["JetBrains"],
    "Mail Stop": ["Mail Stop", "mailstop"],
    "Matt Campbell": ["Matt Campbell", "Matt Mileage OR"],
    "Medford Airport": ["Medford Airport"],
    "Mt. Ashland Foundation": ["Mt Ashland Foundation"],
    "Oregon Secretary of State": ["OR Secretary of State", "Oregon SOS"],
    "OpenAI": ["OpenAI", "Chatgpt", "chatgpt"],
    "Pilot Flying J": ["Pilot"],
    "Replit": ["Replit", "replit"],
    "SmallPDF": ["SmalPDF"],
    "Stripe": ["stripe"],
    "TradingView": ["Trading View"],
    "Verizon": ["verizon"],
    "Walmart": ["Walmart"],
    "Wells Fargo": ["Wells Fargo"],
    "Zach Pistole": ["Zach Pistole"],
}

# TSV Import column mapping - adjust when the TSV format is known
# Maps TSV column headers to transaction fields
TSV_MAPPING = {
    # "TSV Column Name": "api_field"
    # Transaction-level fields:
    #   date, description, reference, transactionType, vendorId, customerId,
    #   reimbursementStatus
    # Line-item fields (per line):
    #   accountId, debit, credit, description, functionalClass,
    #   expenseSubcategoryId, vendorId
}

# Account ID shorthand map
ACCT = {
    "checking": 1, "savings": 2, "petty_cash": 3, "schwab": 103,
    "accounts_receivable": 4, "accounts_payable": 12, "reimb_payable": 13,
    "utilities": 44, "office_supplies": 47, "software_tech": 48,
    "postage_shipping": 49, "telephone_internet": 51,
    "travel_airfare": 52, "travel_lodging": 53, "travel_meals": 54,
    "travel_ground": 55, "conference_reg": 56, "legal_fees": 58,
    "program_supplies": 62, "marketing": 69, "bank_fees": 72,
    "misc_expense": 74,
}

# Description keyword -> (account_id, expense_subcategory_id or None, functional_class)
DESCRIPTION_MAP = {
    "phone stipend":        (ACCT["telephone_internet"], 14, "management_general"),
    "home office":          (ACCT["office_supplies"],     10, "management_general"),
    "utilities":            (ACCT["utilities"],           7,  "management_general"),
    "internet":             (ACCT["telephone_internet"], 14, "management_general"),
    "llm coding":           (ACCT["software_tech"],      11, "program"),
    "investment channel":   (ACCT["misc_expense"],       None, "management_general"),
    "llm":                  (ACCT["software_tech"],      11, "program"),
    "monthly subscription": (ACCT["software_tech"],      11, "program"),
    "fundraising":          (ACCT["software_tech"],      11, "program"),
    "training":             (ACCT["conference_reg"],      19, "program"),
    "domain renew":         (ACCT["marketing"],           28, "program"),
    "pobox":                (ACCT["postage_shipping"],   12, "management_general"),
}

# ---------------------------------------------------------------------------
# Local vendor database
# ---------------------------------------------------------------------------

def load_vendor_db():
    """Load the local vendor database (JSON file)."""
    if os.path.exists(VENDOR_DB_FILE):
        with open(VENDOR_DB_FILE) as f:
            return json.load(f)
    return {"vendors": {}, "alias_map": {}}


def save_vendor_db(db):
    """Save the local vendor database."""
    with open(VENDOR_DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


def rebuild_alias_map(db):
    """Rebuild alias_map from VENDOR_ALIASES using current vendor DB entries."""
    db["alias_map"] = {}
    for canonical, aliases in VENDOR_ALIASES.items():
        for alias in aliases:
            db["alias_map"][alias.lower()] = canonical
    return db


def lookup_vendor_id(raw_name, db):
    """Look up a Fi$h vendor ID from a raw TSV vendor name."""
    canonical = db["alias_map"].get(raw_name.lower())
    if canonical and canonical in db["vendors"]:
        return db["vendors"][canonical]["id"]
    return None


# ---------------------------------------------------------------------------
# Credentials & HTTP helpers
# ---------------------------------------------------------------------------

def load_credentials():
    creds = {}
    try:
        with open(CREDENTIALS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip()
    except FileNotFoundError:
        print(f"Error: {CREDENTIALS_FILE} not found", file=sys.stderr)
        sys.exit(1)
    for key in ("CLIENT_ID", "API_TOKEN", "BASE_URL"):
        if key not in creds:
            print(f"Error: {key} missing from {CREDENTIALS_FILE}", file=sys.stderr)
            sys.exit(1)
    return creds


def api_request(method, path, creds, org_id=None, body=None, params=None):
    """Make an API request and return parsed JSON."""
    base = creds["BASE_URL"].rstrip("/")
    url = f"{base}/api/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Authorization": f"Bearer {creds['API_TOKEN']}",
        "X-Client-Id": creds["CLIENT_ID"],
        "Content-Type": "application/json",
    }
    if org_id:
        headers["X-Org-Id"] = str(org_id)

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            err_json = json.loads(err_body)
            print(f"API Error {e.code}: {err_json.get('message', err_body)}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"API Error {e.code}: {err_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_businesses(args, creds):
    resp = api_request("GET", "/businesses", creds)
    businesses = resp.get("data", [])
    print(f"{'ID':<6} {'Name':<30} {'Type':<15} {'FY Start'}")
    print("-" * 70)
    for b in businesses:
        print(f"{b['id']:<6} {b['name']:<30} {b.get('entityType',''):<15} {b.get('fiscalYearStart','')}")


def cmd_accounts(args, creds):
    resp = api_request("GET", "/accounts", creds, org_id=args.org)
    accounts = resp.get("data", [])
    print(f"{'ID':<6} {'Number':<10} {'Name':<35} {'Type':<15} {'Balance':>12}")
    print("-" * 80)
    for a in accounts:
        print(f"{a['id']:<6} {a.get('accountNumber',''):<10} {a['name']:<35} {a.get('accountType',''):<15} {a.get('balance','0.00'):>12}")


def cmd_vendors(args, creds):
    resp = api_request("GET", "/vendors", creds, org_id=args.org)
    vendors = resp.get("data", [])
    if not vendors:
        print("No vendors found.")
        return
    print(f"{'ID':<6} {'Name':<40} {'Contact'}")
    print("-" * 70)
    for v in vendors:
        contact = v.get("email", "") or v.get("phone", "") or ""
        print(f"{v['id']:<6} {v['name']:<40} {contact}")


def cmd_customers(args, creds):
    resp = api_request("GET", "/customers", creds, org_id=args.org)
    customers = resp.get("data", [])
    if not customers:
        print("No customers found.")
        return
    print(f"{'ID':<6} {'Name':<40} {'Contact'}")
    print("-" * 70)
    for c in customers:
        contact = c.get("email", "") or c.get("phone", "") or ""
        print(f"{c['id']:<6} {c['name']:<40} {contact}")


def cmd_transactions(args, creds):
    params = {}
    if args.fy:
        params["fiscalYear"] = args.fy
    resp = api_request("GET", "/transactions", creds, org_id=args.org, params=params or None)
    txns = resp.get("data", [])
    count = resp.get("count", len(txns))
    print(f"Transactions: {count}")
    print(f"{'ID':<6} {'Date':<12} {'Type':<18} {'Description':<35} {'Ref':<15} {'Posted'}")
    print("-" * 100)
    for t in txns:
        posted = "Y" if t.get("isPosted") else "N"
        print(f"{t['id']:<6} {t['date']:<12} {t.get('transactionType',''):<18} {t.get('description',''):<35} {t.get('reference',''):<15} {posted}")
        for li in t.get("lineItems", []):
            acct = f"  acct:{li['accountId']}"
            dr = li.get("debit", "0.00")
            cr = li.get("credit", "0.00")
            desc = li.get("description", "")
            print(f"       {acct:<20} DR {dr:>10}  CR {cr:>10}  {desc}")


def cmd_post_txn(args, creds):
    try:
        lines = json.loads(args.lines)
    except json.JSONDecodeError as e:
        print(f"Error parsing --lines JSON: {e}", file=sys.stderr)
        sys.exit(1)

    body = {
        "transaction": {
            "transactionType": args.type,
            "date": args.date,
            "description": args.desc,
            "isPosted": True,
        },
        "lineItems": lines,
    }
    if args.ref:
        body["transaction"]["reference"] = args.ref
    # v1.2: vendorId, customerId, functionalClass are now line-item level
    # kept here for backward compat with simple single-line posts
    if args.vendor:
        body["transaction"]["vendorId"] = int(args.vendor)
    if args.customer:
        body["transaction"]["customerId"] = int(args.customer)

    resp = api_request("POST", "/transactions", creds, org_id=args.org, body=body)
    txn = resp.get("data", {})
    print(f"Created transaction ID {txn.get('id')} - {txn.get('description')}")


def cmd_import_tsv(args, creds):
    filepath = args.file
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    headers = reader.fieldnames
    rows = list(reader)

    print(f"File: {filepath}")
    print(f"Columns: {headers}")
    print(f"Rows: {len(rows)}")
    print()

    if args.show_headers:
        print("Column headers:")
        for i, h in enumerate(headers):
            print(f"  [{i}] {h}")
        print("\nSample row (first):")
        if rows:
            for h in headers:
                print(f"  {h}: {rows[0][h]}")
        return

    if not TSV_MAPPING:
        print("TSV_MAPPING is empty. Run with --show-headers to see columns,")
        print("then configure TSV_MAPPING in fish.py before importing.")
        print("\nShowing first 5 rows raw:")
        for i, row in enumerate(rows[:5]):
            print(f"\n--- Row {i+1} ---")
            for h in headers:
                print(f"  {h}: {row[h]}")
        return

    # Build transactions from rows
    transactions = []
    errors = []
    for i, row in enumerate(rows):
        try:
            txn = build_transaction_from_row(row, i + 1)
            transactions.append(txn)
        except ValueError as e:
            errors.append(f"Row {i+1}: {e}")

    if errors:
        print(f"Validation errors ({len(errors)}):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        if not args.dry_run:
            print("\nFix errors before importing.", file=sys.stderr)
            sys.exit(1)

    print(f"Parsed {len(transactions)} transactions ({len(errors)} errors)")

    if args.dry_run:
        print("\n[DRY RUN] Would post the following:")
        for i, txn in enumerate(transactions):
            t = txn["transaction"]
            print(f"  {i+1}. {t['date']} | {t['transactionType']} | {t['description']}")
            for li in txn["lineItems"]:
                print(f"       acct:{li['accountId']}  DR {li.get('debit','0.00'):>10}  CR {li.get('credit','0.00'):>10}")
        return

    # Post transactions
    success = 0
    for i, txn in enumerate(transactions):
        try:
            resp = api_request("POST", "/transactions", creds, org_id=args.org, body=txn)
            tid = resp.get("data", {}).get("id", "?")
            print(f"  [{i+1}/{len(transactions)}] Posted txn {tid}: {txn['transaction']['description']}")
            success += 1
        except SystemExit:
            print(f"  [{i+1}/{len(transactions)}] FAILED: {txn['transaction']['description']}", file=sys.stderr)
            if not args.continue_on_error:
                print("Stopping. Use --continue-on-error to skip failures.", file=sys.stderr)
                sys.exit(1)

    print(f"\nDone: {success}/{len(transactions)} transactions posted.")


def build_transaction_from_row(row, row_num):
    """Convert a TSV row to a transaction payload using TSV_MAPPING.

    Override this function for custom TSV formats.
    """
    # This is a template - will be customized when the TSV format is known
    txn = {
        "transaction": {
            "transactionType": "journal_entry",
            "date": "",
            "description": "",
            "isPosted": True,
        },
        "lineItems": [],
    }

    for tsv_col, api_field in TSV_MAPPING.items():
        val = row.get(tsv_col, "").strip()
        if api_field in ("date", "description", "reference", "transactionType",
                         "functionalClass", "reimbursementStatus"):
            txn["transaction"][api_field] = val
        elif api_field in ("vendorId", "customerId"):
            if val:
                txn["transaction"][api_field] = int(val)

    if not txn["transaction"]["date"]:
        raise ValueError("Missing date")
    if not txn["transaction"]["description"]:
        raise ValueError("Missing description")

    return txn


def map_description(desc):
    """Map expense description to (account_id, subcategory_id, functional_class).

    Matches against DESCRIPTION_MAP keys using case-insensitive prefix/substring.
    """
    lower = desc.lower().strip()
    # Try exact match first
    if lower in DESCRIPTION_MAP:
        return DESCRIPTION_MAP[lower]
    # Try startswith match (handles "pobox 3 month" matching "pobox")
    for key, val in DESCRIPTION_MAP.items():
        if lower.startswith(key):
            return val
    return None


def parse_date_mdy(s):
    """Parse M/D/YY date string to YYYY-MM-DD."""
    parts = s.strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"Bad date format: {s}")
    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
    if y < 100:
        y += 2000
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_money(s):
    """Parse dollar string like '$30.00' to Decimal-safe string '30.00'."""
    s = s.strip().replace("$", "").replace(",", "")
    if not s:
        return "0.00"
    return f"{float(s):.2f}"


def cmd_import_report(args, creds):
    """Import an expense report TSV as a single transaction with N line items."""
    filepath = args.file
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    # Skip leading blank lines
    lines = content.splitlines()
    while lines and not lines[0].replace("\t", "").strip():
        lines.pop(0)
    content = "\n".join(lines)

    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    rows = [r for r in reader if any(v and v.strip() for v in r.values())]

    if not rows:
        print("No data rows found.", file=sys.stderr)
        sys.exit(1)

    db = load_vendor_db()
    db = rebuild_alias_map(db)

    debit_lines = []
    errors = []
    latest_date = ""

    for i, row in enumerate(rows, 1):
        date_raw = row.get("Date", "").strip()
        vendor_raw = row.get("Vendor", "").strip()
        desc_raw = row.get("Description", "").strip()
        amount_raw = row.get("Expense Total", "").strip()

        if not date_raw or not desc_raw or not amount_raw:
            errors.append(f"Row {i}: missing date/description/amount")
            continue

        try:
            date_str = parse_date_mdy(date_raw)
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
            continue

        if date_str > latest_date:
            latest_date = date_str

        amount = parse_money(amount_raw)

        mapping = map_description(desc_raw)
        if not mapping:
            errors.append(f"Row {i}: no mapping for description '{desc_raw}'")
            continue

        acct_id, subcat_id, func_class = mapping

        vendor_id = lookup_vendor_id(vendor_raw, db)
        canonical = db["alias_map"].get(vendor_raw.lower(), vendor_raw)
        if not vendor_id:
            errors.append(f"Row {i}: vendor '{vendor_raw}' not found in DB")
            continue

        line = {
            "accountId": acct_id,
            "debit": amount,
            "credit": "0.00",
            "description": f"{canonical} — {desc_raw}",
            "functionalClass": func_class,
            "vendorId": vendor_id,
        }
        if subcat_id is not None:
            line["expenseSubcategoryId"] = subcat_id

        debit_lines.append(line)

    if errors:
        print(f"Errors ({len(errors)}):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        if not args.dry_run:
            print("\nFix errors before importing.", file=sys.stderr)
            sys.exit(1)

    # Calculate total
    total = sum(float(li["debit"]) for li in debit_lines)

    # Credit line: Reimbursements Payable
    credit_line = {
        "accountId": ACCT["reimb_payable"],
        "debit": "0.00",
        "credit": f"{total:.2f}",
        "description": "Reimbursement payable",
        "functionalClass": "none",
    }

    all_lines = debit_lines + [credit_line]

    body = {
        "transaction": {
            "transactionType": "expense",
            "date": latest_date,
            "description": args.desc,
            "reimbursementStatus": "pending",
            "isPosted": True,
        },
        "lineItems": all_lines,
    }

    print(f"Expense report: {args.desc}")
    print(f"Date: {latest_date} | Lines: {len(all_lines)} ({len(debit_lines)} debits + 1 credit)")
    print(f"Total: ${total:.2f}")
    print()

    for li in debit_lines:
        sub = f" sub:{li['expenseSubcategoryId']}" if "expenseSubcategoryId" in li else ""
        print(f"  DR {li['debit']:>10}  acct:{li['accountId']:<4}{sub}  v:{li['vendorId']:<4} [{li['functionalClass']}]  {li['description']}")
    print(f"  CR {credit_line['credit']:>10}  acct:{credit_line['accountId']:<4}  Reimbursement payable")
    print()

    if args.dry_run:
        print("[DRY RUN] Would post the above transaction.")
        print(json.dumps(body, indent=2))
        return

    resp = api_request("POST", "/transactions", creds, org_id=args.org, body=body)
    txn = resp.get("data", {})
    print(f"Created transaction ID {txn.get('id')}: {txn.get('description')}")


def cmd_create_vendor(args, creds):
    body = {"name": args.name}
    if args.contact:
        body["contactName"] = args.contact
    if args.email:
        body["email"] = args.email
    if args.phone:
        body["phone"] = args.phone
    if args.address:
        body["address"] = args.address

    resp = api_request("POST", "/vendors", creds, org_id=args.org, body=body)
    vendor = resp.get("data", {})
    vid = vendor.get("id")
    print(f"Created vendor ID {vid}: {vendor.get('name')}")

    # Save to local DB
    db = load_vendor_db()
    db = rebuild_alias_map(db)
    db["vendors"][args.name] = {"id": vid, "name": vendor.get("name")}
    save_vendor_db(db)
    print(f"  -> Saved to local vendor DB")


def cmd_import_vendors(args, creds):
    """Bulk-create all canonical vendors from VENDOR_ALIASES that don't already exist."""
    # First, fetch existing vendors from the API
    resp = api_request("GET", "/vendors", creds, org_id=args.org)
    existing = {v["name"].lower(): v for v in resp.get("data", [])}

    db = load_vendor_db()
    db = rebuild_alias_map(db)

    canonical_names = sorted(VENDOR_ALIASES.keys())
    created = 0
    skipped = 0

    for name in canonical_names:
        # Skip if already exists on server
        if name.lower() in existing:
            vid = existing[name.lower()]["id"]
            db["vendors"][name] = {"id": vid, "name": name}
            print(f"  SKIP (exists on server) ID {vid}: {name}")
            skipped += 1
            continue

        # Skip if already in local DB (previously created)
        if name in db["vendors"]:
            print(f"  SKIP (in local DB) ID {db['vendors'][name]['id']}: {name}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  WOULD CREATE: {name}")
            continue

        body = {"name": name}
        resp = api_request("POST", "/vendors", creds, org_id=args.org, body=body)
        vendor = resp.get("data", {})
        vid = vendor.get("id")
        db["vendors"][name] = {"id": vid, "name": vendor.get("name")}
        print(f"  CREATED ID {vid}: {name}")
        created += 1

    save_vendor_db(db)
    print(f"\nDone: {created} created, {skipped} skipped. Local DB saved to {VENDOR_DB_FILE}")


def cmd_vendor_lookup(args, creds):
    """Look up a vendor ID from a raw name using the alias map."""
    db = load_vendor_db()
    db = rebuild_alias_map(db)
    raw = args.name
    canonical = db["alias_map"].get(raw.lower())
    if not canonical:
        print(f"No alias mapping for: {raw}")
        sys.exit(1)
    entry = db["vendors"].get(canonical)
    if not entry:
        print(f"Canonical vendor '{canonical}' not yet created (no ID in local DB)")
        sys.exit(1)
    print(f"Raw: {raw} -> Canonical: {canonical} -> ID: {entry['id']}")


def cmd_reports(args, creds):
    report_type = args.report_type
    valid = ("activities", "balance-sheet", "trial-balance", "ledger")
    if report_type not in valid:
        print(f"Unknown report type: {report_type}. Valid: {', '.join(valid)}", file=sys.stderr)
        sys.exit(1)

    params = {}
    if args.fy:
        params["fiscalYear"] = args.fy
    if args.as_of:
        params["asOf"] = args.as_of
    if args.account_id:
        params["accountId"] = args.account_id

    if report_type == "ledger" and "accountId" not in params:
        print("Error: --account-id is required for ledger report", file=sys.stderr)
        sys.exit(1)

    resp = api_request("GET", f"/reports/{report_type}", creds, org_id=args.org, params=params or None)
    print(json.dumps(resp, indent=2))


def cmd_dashboard(args, creds):
    resp = api_request("GET", "/dashboard", creds, org_id=args.org)
    print(json.dumps(resp, indent=2))


def cmd_fiscal_years(args, creds):
    resp = api_request("GET", "/fiscal-years", creds, org_id=args.org)
    years = resp.get("data", [])
    print(f"{'ID':<6} {'Label':<20} {'Start':<12} {'End':<12} {'Closed'}")
    print("-" * 60)
    for y in years:
        closed = "Y" if y.get("isClosed") else "N"
        print(f"{y['id']:<6} {y.get('label',''):<20} {y.get('startDate',''):<12} {y.get('endDate',''):<12} {closed}")


def cmd_apply_payment(args, creds):
    """Create a payment application linking a payment txn to a bill/invoice."""
    body = {
        "paymentTransactionId": int(args.payment_id),
        "appliedToTransactionId": int(args.bill_id),
        "amount": args.amount,
    }
    if args.date:
        body["appliedDate"] = args.date

    resp = api_request("POST", "/payment-applications", creds, org_id=args.org, body=body)
    pa = resp.get("data", {})
    print(f"Created payment application ID {pa.get('id')}")
    print(f"  Payment txn {pa.get('paymentTransactionId')} -> Bill/Invoice txn {pa.get('appliedToTransactionId')}")
    print(f"  Amount: ${pa.get('amount')}  Date: {pa.get('appliedDate')}")


def cmd_payment_status(args, creds):
    """Check payment status for one or more transactions."""
    params = {"transactionIds": args.ids}
    resp = api_request("GET", "/payment-status", creds, org_id=args.org, params=params)
    statuses = resp.get("data", {})
    print(f"{'Txn ID':<10} {'Applied':>12} {'Total':>12} {'Status':<10}")
    print("-" * 50)
    for tid, info in statuses.items():
        print(f"{tid:<10} {info['applied']:>12} {info['total']:>12} {info['status']:<10}")


def cmd_payment_applications(args, creds):
    """List payment applications."""
    params = {}
    if args.txn_id:
        params["transactionId"] = args.txn_id
    resp = api_request("GET", "/payment-applications", creds, org_id=args.org, params=params or None)
    apps = resp.get("data", [])
    if not apps:
        print("No payment applications found.")
        return
    print(f"{'ID':<6} {'Payment Txn':<14} {'Applied To':<14} {'Amount':>12} {'Date':<12}")
    print("-" * 60)
    for a in apps:
        print(f"{a['id']:<6} {a['paymentTransactionId']:<14} {a['appliedToTransactionId']:<14} {a['amount']:>12} {a.get('appliedDate',''):<12}")


def cmd_pay_bill(args, creds):
    """3-step: create bill + payment + payment application in one go.

    For direct expenses paid from a cash account.
    """
    try:
        expense_lines = json.loads(args.lines)
    except json.JSONDecodeError as e:
        print(f"Error parsing --lines JSON: {e}", file=sys.stderr)
        sys.exit(1)

    total = sum(float(li.get("debit", "0")) for li in expense_lines)
    vendor_id = int(args.vendor) if args.vendor else None
    cash_acct = int(args.cash_account) if args.cash_account else ACCT["checking"]

    # Step 1: Create bill (DR expense, CR AP)
    bill_lines = expense_lines + [{
        "accountId": ACCT["accounts_payable"],
        "debit": "0.00",
        "credit": f"{total:.2f}",
        "description": "Accounts payable",
        "functionalClass": "none",
    }]

    bill_body = {
        "transaction": {
            "transactionType": "bill",
            "date": args.date,
            "description": args.desc,
            "isPosted": True,
        },
        "lineItems": bill_lines,
    }
    if vendor_id:
        bill_body["transaction"]["vendorId"] = vendor_id
    if args.ref:
        bill_body["transaction"]["reference"] = args.ref

    payment_date = args.payment_date or args.date

    if args.dry_run:
        print("[DRY RUN] Step 1 — Bill:")
        print(json.dumps(bill_body, indent=2))
        print(f"\nStep 2 — Payment ({payment_date}): DR AP ${total:.2f}, CR acct:{cash_acct} ${total:.2f}")
        print(f"Step 3 — Payment application: ${total:.2f}")
        return

    resp = api_request("POST", "/transactions", creds, org_id=args.org, body=bill_body)
    bill_txn = resp.get("data", {})
    bill_id = bill_txn.get("id")
    print(f"Step 1: Created bill ID {bill_id} — {args.desc}")

    # Step 2: Create payment (DR AP, CR cash)
    payment_body = {
        "transaction": {
            "transactionType": "payment",
            "date": payment_date,
            "description": f"Payment — {args.desc}",
            "isPosted": True,
        },
        "lineItems": [
            {
                "accountId": ACCT["accounts_payable"],
                "debit": f"{total:.2f}",
                "credit": "0.00",
                "description": "Clear accounts payable",
                "functionalClass": "none",
            },
            {
                "accountId": cash_acct,
                "debit": "0.00",
                "credit": f"{total:.2f}",
                "description": "Payment from cash account",
                "functionalClass": "none",
            },
        ],
    }
    if vendor_id:
        payment_body["transaction"]["vendorId"] = vendor_id

    resp = api_request("POST", "/transactions", creds, org_id=args.org, body=payment_body)
    pmt_txn = resp.get("data", {})
    pmt_id = pmt_txn.get("id")
    print(f"Step 2: Created payment ID {pmt_id} — Payment — {args.desc}")

    # Step 3: Create payment application
    pa_body = {
        "paymentTransactionId": pmt_id,
        "appliedToTransactionId": bill_id,
        "amount": f"{total:.2f}",
        "appliedDate": payment_date,
    }
    resp = api_request("POST", "/payment-applications", creds, org_id=args.org, body=pa_body)
    pa = resp.get("data", {})
    print(f"Step 3: Created payment application ID {pa.get('id')} — ${total:.2f}")
    print(f"\nDone: bill={bill_id}, payment={pmt_id}, application={pa.get('id')}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fi$h API CLI Tool")
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # businesses
    sub.add_parser("businesses", help="List all businesses")

    # accounts
    p = sub.add_parser("accounts", help="List chart of accounts")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")

    # vendors
    p = sub.add_parser("vendors", help="List vendors")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")

    # create-vendor
    p = sub.add_parser("create-vendor", help="Create a single vendor")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--name", required=True, help="Vendor name")
    p.add_argument("--contact", help="Contact name")
    p.add_argument("--email", help="Contact email")
    p.add_argument("--phone", help="Contact phone")
    p.add_argument("--address", help="Address")

    # import-vendors
    p = sub.add_parser("import-vendors", help="Bulk-create all canonical vendors")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--dry-run", action="store_true", help="Show what would be created without creating")

    # vendor-lookup
    p = sub.add_parser("vendor-lookup", help="Look up vendor ID from raw TSV name")
    p.add_argument("name", help="Raw vendor name from TSV")

    # customers
    p = sub.add_parser("customers", help="List customers")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")

    # transactions
    p = sub.add_parser("transactions", help="List transactions")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--fy", help="Fiscal year (e.g. 2025)")

    # post-txn
    p = sub.add_parser("post-txn", help="Post a single transaction")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--type", required=True, help="Transaction type (journal_entry, expense, etc.)")
    p.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p.add_argument("--desc", required=True, help="Description")
    p.add_argument("--lines", required=True, help='Line items as JSON array')
    p.add_argument("--ref", help="Reference number")
    p.add_argument("--vendor", help="Vendor ID")
    p.add_argument("--customer", help="Customer ID")

    # import-report
    p = sub.add_parser("import-report", help="Import expense report TSV as single transaction")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--desc", required=True, help="Transaction description (e.g. 'Matt Expense Report Jan 2026')")
    p.add_argument("--dry-run", action="store_true", help="Validate and show payload without posting")
    p.add_argument("file", help="Path to expense report TSV file")

    # import-tsv
    p = sub.add_parser("import-tsv", help="Import transactions from TSV")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--dry-run", action="store_true", help="Validate without posting")
    p.add_argument("--show-headers", action="store_true", help="Show column headers and sample row")
    p.add_argument("--continue-on-error", action="store_true", help="Continue posting on error")
    p.add_argument("file", help="Path to TSV file")

    # reports
    p = sub.add_parser("reports", help="Pull financial reports")
    p.add_argument("report_type", help="Report type: activities, balance-sheet, trial-balance, ledger")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--fy", help="Fiscal year")
    p.add_argument("--as-of", help="As-of date (YYYY-MM-DD)")
    p.add_argument("--account-id", help="Account ID (required for ledger)")

    # dashboard
    p = sub.add_parser("dashboard", help="Dashboard metrics")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")

    # fiscal-years
    p = sub.add_parser("fiscal-years", help="List fiscal years")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")

    # apply-payment
    p = sub.add_parser("apply-payment", help="Create payment application (link payment to bill/invoice)")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--payment-id", required=True, help="Payment transaction ID")
    p.add_argument("--bill-id", required=True, help="Bill/invoice transaction ID")
    p.add_argument("--amount", required=True, help="Amount to apply (e.g. '500.00')")
    p.add_argument("--date", help="Application date (YYYY-MM-DD, defaults to today)")

    # payment-status
    p = sub.add_parser("payment-status", help="Check payment status for transactions")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("ids", help="Comma-separated transaction IDs (e.g. '5,6,7')")

    # payment-applications
    p = sub.add_parser("payment-applications", help="List payment applications")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--txn-id", help="Filter by transaction ID")

    # pay-bill (3-in-1: bill + payment + application)
    p = sub.add_parser("pay-bill", help="Create bill + payment + payment application in one step")
    p.add_argument("--org", default=DEFAULT_ORG_ID, help="Org/business ID")
    p.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p.add_argument("--desc", required=True, help="Description")
    p.add_argument("--lines", required=True, help="Expense line items as JSON (debit lines only)")
    p.add_argument("--vendor", help="Vendor ID")
    p.add_argument("--ref", help="Reference number")
    p.add_argument("--payment-date", help="Payment date if different from bill date (YYYY-MM-DD)")
    p.add_argument("--cash-account", help="Cash account ID (default: checking/1)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be created")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    creds = load_credentials()

    commands = {
        "businesses": cmd_businesses,
        "accounts": cmd_accounts,
        "vendors": cmd_vendors,
        "create-vendor": cmd_create_vendor,
        "import-vendors": cmd_import_vendors,
        "vendor-lookup": cmd_vendor_lookup,
        "customers": cmd_customers,
        "transactions": cmd_transactions,
        "post-txn": cmd_post_txn,
        "import-report": cmd_import_report,
        "import-tsv": cmd_import_tsv,
        "reports": cmd_reports,
        "dashboard": cmd_dashboard,
        "fiscal-years": cmd_fiscal_years,
        "apply-payment": cmd_apply_payment,
        "payment-status": cmd_payment_status,
        "payment-applications": cmd_payment_applications,
        "pay-bill": cmd_pay_bill,
    }

    commands[args.command](args, creds)


if __name__ == "__main__":
    main()
