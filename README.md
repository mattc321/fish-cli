# Fi$h CLI

Command-line tool for the [Fi$h accounting API](https://fish.darkfishdigital.com). Pure Python (stdlib only, no pip installs needed).

## Setup

```bash
cp credentials.env.example credentials.env
# Edit credentials.env with your CLIENT_ID and API_TOKEN
```

## Usage

```bash
python3 fish.py <command> [options]
```

Most commands accept `--org <id>` (defaults to 1).

### Listing data

| Command | Description |
|---------|-------------|
| `businesses` | List all businesses |
| `accounts` | Chart of accounts |
| `vendors` | List vendors |
| `customers` | List customers |
| `transactions` | List transactions with line items (`--fy` to filter by fiscal year) |
| `fiscal-years` | List fiscal years |
| `payment-applications` | List payment applications (`--txn-id` to filter) |
| `dashboard` | Display dashboard metrics |
| `reports <type>` | Financial reports: `activities`, `balance-sheet`, `trial-balance`, `ledger` |

### Creating transactions

| Command | Description |
|---------|-------------|
| `post-txn` | Post a single manual transaction |
| `import-report` | Import an expense report from a TSV file |
| `import-tsv` | Import transactions from a TSV file |
| `pay-bill` | Create bill + payment + payment application in one step |

### Vendor management

| Command | Description |
|---------|-------------|
| `create-vendor` | Create a vendor |
| `import-vendors` | Bulk-create vendors from the built-in alias map |
| `vendor-lookup` | Resolve a raw name to a vendor ID via aliases |

### Payments

| Command | Description |
|---------|-------------|
| `apply-payment` | Link a payment transaction to a bill/invoice |
| `payment-status` | Check paid/partial/unpaid status for transaction IDs |

## Examples

```bash
# List accounts for HTST (org 1)
python3 fish.py accounts --org 1

# List transactions for fiscal year 1
python3 fish.py transactions --org 1 --fy 1

# Pay a bill (creates bill + payment + payment application)
python3 fish.py pay-bill --org 1 --date 2026-02-15 --desc "Office supplies" \
  --vendor 5 --lines '[{"accountId": 50, "amount": 42.99, "description": "Pens and paper"}]'

# Import an expense report
python3 fish.py import-report --org 1 --desc "Jan 2026 expenses" report.tsv

# Check payment status
python3 fish.py payment-status --org 1 100,101,102

# Pull the activities report
python3 fish.py reports activities --org 1 --fy 1
```
