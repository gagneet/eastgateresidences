---
# MRI Migration Guide
## For Super Administrators — Importing Historical Financial Data

---

## Overview

The MRI Migration tool imports historical trust accounting data from MRI Software
into your building's trust accounting system. Each migration batch is scoped to
your building — data from one building is never visible to another.

**This process cannot be undone easily. Always complete a dry-run before committing.**

---

## Prerequisites

Before starting a migration:

1. Export your data from MRI Software as a CSV file
2. Ensure the financial year being imported is not already in the system
3. Confirm with your Strata Manager that the import is authorised
4. Have a backup of the current database (handled by system administrator)

---

## Step 1: Upload the File

Go to **Admin > MRI Migration > New Batch**.

Upload your MRI CSV export. The system assigns the batch a status of **Created**.

---

## Step 2: Validate

Click **Validate**.

The system checks every row for:

| Check                  | Severity    | What it means                             |
|------------------------|-------------|-------------------------------------------|
| Missing required field | 🔴 Blocking | Import cannot proceed until fixed         |
| Amount out of range    | 🔴 Blocking | Amount is ≤ 0 or > $999,999               |
| Duplicate CRN          | 🔴 Blocking | Same payment reference appears twice      |
| Invalid date format    | 🔴 Blocking | Date cannot be parsed                     |
| Unknown unit number    | 🟡 Warning  | Unit not found in system (check spelling) |
| Suspicious amount      | 🟡 Warning  | Unusually high or low amount              |
| Missing reference      | ℹ️ Info     | No source reference provided              |

**Blocking errors must be fixed before proceeding.**

The batch will show as **Validated** (green) or **Validation Failed** (red).

---

## Step 3: Dry Run

Click **Dry Run** to preview what will be imported.

The dry run shows:

- Number of rows to be imported
- Total dollar amount
- Sample rows from the import
- Any warnings that exist

**Nothing is written to the database during a dry run.**

When satisfied, click **Approve for Commit** to advance to the next stage.

---

## Step 4: Commit

Review the commit safety message:

> *"This will post X transactions totalling $Y. Reversal is possible within 30 days
> but requires Super Admin approval."*

Click **Commit** to write the data.

The system will:

1. Check the idempotency key (prevents duplicate imports)
2. Write all rows to the staging collection
3. Verify row count and sum totals
4. Mark the batch as **Committed**
5. Make rows available for reconciliation

---

## Step 5: Verify

After committing:

1. Go to **Finance > Trust Accounting** and verify the imported transactions appear
2. Check the total imported matches your MRI export total
3. Begin reconciliation for the imported period

---

## Rollback

If you committed an import in error, a **30-day rollback window** is available.

Go to **Admin > MRI Migration > [Batch Name] > Rollback**.

Only **Super Admin** can initiate a rollback. The rollback:

1. Removes all trust ledger entries created by the batch
2. Marks the batch as **Rolled Back**
3. Creates an audit log entry

After 30 days, rollback is no longer available.

---

## Batch Status Reference

| Status             | Meaning                                              |
|--------------------|------------------------------------------------------|
| Created            | File uploaded, not yet validated                     |
| Validated          | Validation passed, no blocking errors                |
| Validation Failed  | Blocking errors found — fix the file and re-upload   |
| Dry Run Complete   | Preview completed                                    |
| Ready to Commit    | Approved for final import                            |
| Committed          | Import written to database                           |
| Rollback Available | Import committed, rollback window open (30 days)     |
| Rolled Back        | Import reversed                                      |
| Failed             | Import failed partway — contact system administrator |

---

## Frequently Asked Questions

**Q: What happens if the import fails halfway through?**
A: The system uses a safe staging pattern. If the commit fails, the batch is marked
**Failed** and staging rows are preserved for inspection. Contact your system administrator.

**Q: Can I import the same file twice?**
A: No. The system uses a SHA-256 fingerprint of the file. Re-uploading the same file
will return an "idempotent replay" message rather than creating a duplicate.

**Q: What if I need to fix a few rows after committing?**
A: Use the trust accounting transaction editor to make individual corrections.
Do not rollback the entire batch for a small number of errors.
