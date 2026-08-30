---
# Trust Reconciliation Guide
## For EC Members and Strata Managers

---

## What is Reconciliation?

Bank reconciliation matches every deposit and withdrawal in your bank statement against
a corresponding entry in your trust ledger. When every transaction is matched, the
bank balance and the ledger balance should agree exactly.

Phase 2 introduces a **Smart Matching Engine** that suggests matches automatically,
so you spend your time reviewing exceptions rather than hunting through long lists.

---

## Step 1: Create a Reconciliation Run

Go to **Finance > Trust Accounting > Reconciliation**.

Click **New Reconciliation Run** and select:

- Fund: Admin Fund or Sinking Fund
- Period: e.g. March 2026
- Financial Year: 2026–2027

---

## Step 2: Import Your Bank Statement

Download a CSV export from your bank (CBA or NAB formats supported).

Click **Import Bank Statement** and upload the CSV file.

The system will parse each line and display the bank statement in a table.

---

## Step 3: Review Match Suggestions

Once the statement is imported, click **Get Suggestions**.

The Smart Matching Engine scores each bank line against every unmatched ledger entry
and shows a ranked list of candidates:

| Colour          | Match Type     | What to Do                                       |
|-----------------|----------------|--------------------------------------------------|
| 🟢 Green (≥90%) | Exact          | Click **Auto-Match All Exact** to bulk-confirm   |
| 🟡 Amber (≥70%) | Likely         | Review the pair and click **Confirm** if correct |
| 🔴 Red (<70%)   | Weak/Ambiguous | Investigate manually before matching             |

### Understanding Match Reasons

Each suggested match shows why it was recommended:

- `exact_amount` — amounts are identical
- `date_within_2_days` — transaction dates are within 2 days
- `reference_match` — bank reference matches ledger reference
- `description_overlap` — similar words in description

---

## Step 4: Handle Exceptions

The **Exceptions** tab shows items that need your attention:

| Exception Type             | Meaning                                  | Action                                 |
|----------------------------|------------------------------------------|----------------------------------------|
| Duplicate bank line        | Two bank lines look identical            | Confirm one is correct, flag the other |
| Stale unmatched (45+ days) | Item has been unmatched for over 45 days | Investigate or write off               |
| Suspicious variance        | Amount differs from expected by >5%      | Check for bank fees or errors          |

---

## Step 5: Close the Period

Once all items are matched (or exceptions are resolved), click **Close Period**.

A closed reconciliation:

- Cannot have new matches added
- Locks the period against future transaction posting
- Records who closed it and when

A padlock icon 🔒 shows on the period once closed.

---

## Period Locks

Period locks prevent anyone from posting transactions into a period that has been reconciled.
This protects the integrity of your historical records.

**To lock a period manually:** Go to **Finance > Period Locks > Lock Period**

**To unlock a period:** Only a Super Admin can unlock a period.
Unlocking is only allowed if there are no unmatched transactions in that period.

---

## Summary Panel

The top of the reconciliation screen shows a live summary:

| Metric                    | Description                                  |
|---------------------------|----------------------------------------------|
| Matched                   | Confirmed pairs                              |
| Unmatched (bank)          | Bank lines with no ledger match yet          |
| Unmatched (internal)      | Ledger entries with no bank line yet         |
| Suggested                 | Auto-scored candidates awaiting confirmation |
| Reconciliation Confidence | % of transactions matched (target: 100%)     |
| Total Unreconciled        | Dollar amount of unmatched items             |

---

## Frequently Asked Questions

**Q: What if a bank line doesn't match any ledger entry?**
A: Check if the payment was recorded in the ledger. If not, create a transaction first.
If it's a bank fee or interest, record it as a ledger adjustment.

**Q: Can I undo a match?**
A: Yes. Click the match and select **Unmatch**. Both lines return to unmatched status.

**Q: Who can close a reconciliation?**
A: Strata Manager or Super Admin only.
