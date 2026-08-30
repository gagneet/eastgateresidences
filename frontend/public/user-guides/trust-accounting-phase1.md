# Trust Accounting Phase 1 — User Guide

## For Strata Managers and EC Members

---

## 1. What Are Trust Accounts and Why Does Your Building Need Them?

A trust account is a separate bank account that holds money belonging to the owners
of your strata scheme. Your Owners Corporation is legally required to maintain:

- **Admin Fund Account** — holds money for day-to-day running expenses (cleaning, insurance, repairs)
- **Sinking Fund Account** — holds money for long-term capital works (painting, roof replacement, lifts)

The Strata Manager is a **trustee** — they hold this money on behalf of the owners and must
keep meticulous records of every deposit and withdrawal. This software helps you do that.

---

## 2. Understanding Your Building's Configuration

Every building has its own financial configuration. Your Strata Manager sets this up once:

| Setting             | What It Means                                                                   |
|---------------------|---------------------------------------------------------------------------------|
| Annual Budget       | Total money to collect this financial year (admin + sinking separately)         |
| Quarterly Due Dates | The 4 dates each year when levies are due (e.g. March 1, June 1, Sept 1, Dec 1) |
| Grace Period        | Days after the due date before a levy is considered overdue (typically 14 days) |
| Interest Rate       | Annual interest rate charged on overdue levies (set by your state legislation)  |
| DEFT Biller Code    | Your building's unique code for BPAY payments                                   |

These settings are specific to YOUR building. A different building managed by the same platform
will have completely different settings — there is no cross-over.

### Adding a New Trust Bank Account

Most buildings have their trust bank account(s) set up during onboarding. If your building needs
an additional account (for example, a separate savings account for the sinking fund), a
Super Admin, Strata Admin, or Strata Manager can add one directly:

1. Go to **Trust Bank Accounts** and click **Add Account**.
2. Enter the bank name, account name, BSB, and account number. Only the last 4 digits of the
   account number are stored — the rest is never saved by the platform.
3. Choose which fund the account holds (Admin, Sinking, or Special Purpose) and, if known, the
   opening balance.
4. Click **Create Account**. The account appears immediately in your account list, ready to
   receive interest postings and be selected for reconciliation.

This feature requires Trust Accounting to be enabled for your building — if it isn't, you'll see
a message explaining that and can ask your platform administrator to enable it.

---

## 3. Generating Quarterly Levy Schedules — Step by Step

At the start of each quarter, you generate levy schedules for all units:

1. Go to **Finance → Trust Accounting**
2. Click the **Generate Levies** button (requires Strata Manager access)
3. Select the quarter (e.g. "2026-Q2") and due date
4. Click **Generate**

The system will:

- Look up the annual budget from your building configuration
- Divide by 4 to get the quarterly amount
- Distribute across all units proportionally (by UOE — see section 4)
- Assign a unique DEFT CRN to each unit for BPAY payments

**Important:** The system is idempotent. Running generation twice for the same quarter
will NOT create duplicate levies — it will show "0 created, 87 skipped".

---

## 4. How Unit Levy Amounts Are Calculated (UOE Explained Simply)

**UOE = Unit of Entitlement**

Each unit in your strata scheme has a UOE number registered in the strata plan.
Think of it like a "share" of the building's costs:

- A small apartment might have UOE = 82
- A large apartment might have UOE = 111 or 139
- A penthouse might have UOE = 149
- A townhouse might have UOE = 160 or 161

The system adds up all UOE values (e.g. 10,916 for East Gate) and calculates
each unit's share proportionally:

```
Unit levy = Quarterly Budget × (Unit UOE ÷ Total Building UOE)

Example (East Gate 2-bed apartment, UOE 111):
Admin levy  = $85,217.50 × (111 ÷ 10,916) = $866.53
Sinking levy = $24,876.25 × (111 ÷ 10,916) = $252.95
Total levy  = $1,119.48
```

These numbers come from your building's budget and strata plan — NOT from hardcoded values.
If the budget changes next year, the levy amounts change automatically.

---

## 5. How Owners Pay Via DEFT/BPAY (What a CRN Is)

**CRN = Customer Reference Number**

Each unit receives a unique CRN for the quarter. Owners use this to pay via:

- BPAY (through their bank's internet banking)
- Direct debit (if set up)

The CRN format: `MOCK-EG-452301-0018-2026Q1-7`

- `MOCK-EG-452301` = your building's DEFT biller code
- `0018` = lot number
- `2026Q1` = the quarter
- `7` = security check digit (Luhn algorithm)

When DEFT receives a payment, it sends a notification to the platform. The system
automatically matches the CRN to the correct unit and updates their levy status.

**Your building's CRNs are unique to your building.** Even if two buildings have
the same lot number 18, their CRNs will be different because the biller codes differ.

---

## 6. Recording Manual Payments (EFT/Cheque)

For owners who pay by EFT or cheque (not via BPAY):

1. Go to **Finance → Trust Accounting → Levy Schedule**
2. Find the unit's row
3. Click **Record Payment**
4. Enter the amount, date, and reference number
5. Select payment method (EFT, cheque, etc.)
6. Click **Save**

The system will:

- Create a transaction record in the trust ledger
- Update the levy status (pending → partial → paid)
- Recalculate the displayed fund balance from the opening balance and transaction history

**Note:** All transactions are immutable. If you make an error, you must
create a **reversal** rather than editing or deleting the original.

Balances shown in Trust Accounting are calculated from the account's opening balance plus all
recorded deposits, interest, charges, disbursements, and reversals. The displayed balance is not a
separate editable field.

---

## 7. The Arrears Timeline — What Happens at Day 14, 21, 30, 60

When a levy is not paid by the due date plus grace period, the system
automatically escalates through stages:

| Day        | Stage               | What Happens                                                                        |
|------------|---------------------|-------------------------------------------------------------------------------------|
| 0          | Due Date            | Levy is due                                                                         |
| +14        | Grace Period Ends   | Levy becomes overdue                                                                |
| Grace + 14 | **Reminder**        | Automated reminder email to owner                                                   |
| Grace + 21 | **Formal Notice**   | PDF formal notice generated and emailed, case enters Debt Recovery Board monitoring |
| Grace + 30 | **Interest Charge** | Interest starts accruing at your state's legislated rate                            |
| Grace + 60 | **Legal Flag**      | Levy flagged for potential legal action, Debt Recovery Board marks as DCA eligible  |

**Important:** These thresholds are configured for YOUR building and may differ from
other buildings on the platform (different states have different rates and rules).

Example — East Gate (ACT, 10% pa):
$866.53 overdue for 30 days → $8.22 interest charged

---

## 8. How the Arrears System Connects to the Debt Recovery Board

The Trust Accounting arrears engine feeds directly into the existing
**Debt Recovery Board (DRB)** system:

```
Reminder Stage (Day 14)     → DRB: no change yet
Formal Notice (Day 21)      → DRB: stage set to "Monitor"
Legal Flag (Day 60)         → DRB: stage set to "DCA Eligible"
```

From "DCA Eligible", the DRB workflow handles formal debt collection
including DCA (Debt Collection Agency) referral.

You can view the DRB status in the **Arrears tab** of the Trust Accounting dashboard.

---

## 9. Quarterly Strata Manager Checklist

At the start of each quarter:

- [ ] Generate levy schedules for the new quarter (`POST /levies/generate`)
- [ ] Send levy notices to all owners
- [ ] Verify DEFT biller codes are active

During the quarter:

- [ ] Reconcile trust account weekly against bank statement
- [ ] Record any manual payments (EFT/cheque)
- [ ] Review arrears report — check for unpaid levies past grace period
- [ ] Run arrears escalation check (or confirm automatic job ran)

At quarter end:

- [ ] Complete bank reconciliation
- [ ] Review financial summary report
- [ ] Check collection rate (target: >95%)
- [ ] Escalate any legal cases to DCA if appropriate

---

## 10. Period Management and Audit Trail

### What is a Financial Period?

A **financial period** is a defined window of time (typically one quarter or one month) during which
transactions are recorded in the trust ledger. Think of it like a chapter in a book — once the chapter
is finished and reviewed, it is sealed so that nobody can go back and change the story.

Your building's strata software tracks five stages for each period:

| Stage           | What it means for you                                                        |
|-----------------|------------------------------------------------------------------------------|
| **Open**        | Transactions are being recorded normally                                     |
| **Reconciling** | The manager is matching the bank statement to the ledger — posting is paused |
| **Closed**      | The period is finished and has passed the three-way balance check            |
| **Audited**     | An auditor has reviewed and confirmed the records                            |
| **Locked**      | Permanently sealed — no further changes are possible                         |

### Why Can't Managers Post to a Closed Period?

Once a period is **Closed**, **Audited**, or **Locked**, it cannot accept new transactions.
This is by design — it protects the integrity of your building's financial records and ensures that the
audited statements cannot be altered after the fact.

**What to do instead:** If your manager or strata agent discovers an error in a closed period,
they do **not** delete or edit the original entry. Instead, they post a **reversal** in the
*current open period* — an equal and opposite transaction that cancels the error. Both the original
and the reversal remain in the audit trail permanently.

This is the same method used by all professionally managed trust accounts and is a requirement under
ACT and NSW strata legislation.

### What is the Merkle Seal? (Tamper-Evident Daily Snapshot)

When a period is closed, the system automatically creates a **Merkle seal** — a mathematical
fingerprint of every transaction recorded in that period.

Think of it like a wax seal on an envelope:

- It is calculated from all the transactions in the period.
- Each period's seal is linked to the previous period's seal (forming a chain).
- If **anyone** were to secretly change a transaction after the period was closed, the fingerprint would
  no longer match, and the system would detect the tampering immediately.

You do not need to manage the seal yourself — it is created automatically at period close and stored
securely. It is there to protect you and your fellow owners by providing independent proof that the
financial records have not been altered.

### What Happens if Reconciliation Fails?

Before a period can be closed, the system performs a **three-way reconciliation check**:

1. The adjusted bank balance (bank statement, corrected for timing differences)
2. The ledger balance (transactions recorded in the platform)
3. The subledger balance (unit-level levy and payment totals)

All three must agree to the cent before the period can close.

If reconciliation fails, your strata manager will investigate the discrepancy. Common causes include:

- A bank transaction not yet recorded in the platform
- A direct debit that was returned and not reversed
- A manual payment recorded at the wrong amount

**If you believe there is an error in your levy records**, contact your strata manager or building
administrator directly. Do not attempt to resolve financial discrepancies yourself — all corrections
must go through the proper reversal process to maintain the audit trail.

| Contact                | When                                                                                                |
|------------------------|-----------------------------------------------------------------------------------------------------|
| Your strata manager    | For questions about levy amounts, payment records, or account corrections                           |
| Building administrator | For general queries about your account balance or payment history                                   |
| Platform support       | For technical errors (e.g., the portal shows an incorrect balance that your manager cannot explain) |

---

## 11. Glossary

| Term                         | Meaning                                                                                                      |
|------------------------------|--------------------------------------------------------------------------------------------------------------|
| **Admin Fund**               | Bank account for day-to-day running costs                                                                    |
| **Sinking Fund**             | Bank account for long-term capital works                                                                     |
| **UOE**                      | Unit of Entitlement — each unit's proportional share of costs                                                |
| **Levy**                     | Quarterly payment owed by each unit owner                                                                    |
| **CRN**                      | Customer Reference Number — unique BPAY identifier per unit per quarter                                      |
| **DEFT**                     | Payment platform used for BPAY levy collection                                                               |
| **Biller Code**              | Your building's unique DEFT identifier (stored in trust config, NOT in .env)                                 |
| **Grace Period**             | Days after due date before a levy is considered overdue                                                      |
| **DRB**                      | Debt Recovery Board — the formal debt escalation system                                                      |
| **DCA**                      | Debt Collection Agency — external collector for severe arrears                                               |
| **Arrears**                  | Outstanding (unpaid) levy amounts                                                                            |
| **Reconciliation**           | Matching your trust account bank statement to the system ledger                                              |
| **Reversal**                 | An equal and opposite transaction that cancels a previous transaction                                        |
| **Audit Log**                | Immutable record of every financial action (kept 7 years)                                                    |
| **Trust Config**             | Per-building financial configuration stored in MongoDB                                                       |
| **Financial Period**         | A defined time window (quarter/month) with a lifecycle state: Open → Reconciling → Closed → Audited → Locked |
| **Merkle Seal**              | Tamper-evident mathematical fingerprint of all transactions in a closed period; chained to the prior period  |
| **Three-Way Reconciliation** | Period-close check requiring bank balance, ledger balance, and subledger balance to all agree to the cent    |
| **Period Lock**              | Permanent seal applied at the LOCKED stage — no further changes possible under any circumstances             |
