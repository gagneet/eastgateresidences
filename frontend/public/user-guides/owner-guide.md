# Owner Guide — EastGate Residences Portal

**For:** Property owners at 14 Hoolihan Street, Denman Prospect ACT  
**URL:** https://eastgateresidences.com.au/dashboard

---

## 1. Dashboard Overview

When you log in you land on the **Owner Dashboard**. The key widgets are:

| Widget                    | What it shows                                                                                                                                                                                   |
|---------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Building Health Score** | A 0–100 composite score across governance, finances, maintenance, community participation, and compliance. Green (75–100) is healthy; amber (50–74) needs attention; red (<50) requires action. |
| **Savings YTD**           | Total verified savings recorded by your Strata Manager this financial year (e.g., contractor rebates, bulk-buy discounts). This offsets future levy increases.                                  |
| **Active Proposals**      | Number of open OC motions you can currently vote on. A red badge means your vote is outstanding.                                                                                                |
| **Levy Status**           | Your next levy due date, amount, and current balance.                                                                                                                                           |
| **Maintenance**           | Open maintenance requests for your unit or common property.                                                                                                                                     |

### Notice Bar (Property Pulse Banner)

The dark banner at the top of the dashboard shows four at-a-glance metrics:

| Metric                      | What it shows                                                                                        | Scope             |
|-----------------------------|------------------------------------------------------------------------------------------------------|-------------------|
| **Fund Health**             | Building-wide full-year levy coverage — payments received against the building's full-year levy (including instalments not yet due). This is distinct from the due-date Collection Rate, and the two are never labelled interchangeably. | **Building-wide** |
| **Levies Paid**             | Percentage of total levies collected from all units so far this financial year                       | **Building-wide** |
| **Maintenance**             | Number of open maintenance requests across the building                                              | Building-wide     |
| **Next Due / Next Meeting** | Your next levy payment due date or the next scheduled meeting                                        | Per-unit          |

> **Note:** Fund Health and Levies Paid reflect the entire building's collection performance, not your individual
> payment status. To see your personal levy payment progress, click the **Fund Health** or **Levies Paid** stat — the
> popup that opens shows both building-wide rates (in the top section) and your unit's personal payment breakdown (in
> the
> lower section).

---

## 2. How to Vote on a Proposal

1. From the dashboard, click the **Active Proposals** card, or navigate to **Governance → Proposals**.
2. You will see a list of open motions. Each card shows the proposal title, category, closing date, and current vote
   tally.
3. Click a proposal to open the detail view. Read the description and any attached documents.
4. Scroll to the **Cast Your Vote** section.
5. Select **For**, **Against**, or **Abstain**.
6. Click **Submit Vote**. Your vote is recorded immediately and cannot be changed.
7. You will receive a confirmation email and an in-app notification.

> **Note:** Voting closes at the date/time shown on the proposal. Votes submitted after closing are not counted.

---

## 3. How to Volunteer for Community Events

1. Navigate to **Community → Volunteer Events**.
2. Browse upcoming events. Each listing shows the event name, date, time, location, and the levy credit offered (in
   AUD).
3. Click **Register** on the event you want to join.
4. Confirm your registration in the dialog.
5. Attend the event on the day. The Strata Manager will mark you as attended.
6. Once marked complete, the levy credit is automatically applied to your lot account within 24 hours. You will receive
   an email confirmation showing the updated balance.

> **Credits** appear on your next levy statement as a line-item deduction.

---

## 4. How to Submit a Smart Request

The **Smart Request** form routes your enquiry to the right team automatically.

1. Navigate to **Requests → Smart Request** (or use the floating **+** button on the dashboard).
2. **Step 1 — Describe your issue:** Type a plain-English description (e.g., "The intercom on my front door stopped
   working").
3. **Step 2 — Category & priority:** The system suggests a category (e.g., "Access Control"). Adjust if needed, then
   select your perceived urgency.
4. **Step 3 — Confirm & submit:** Review the summary and click **Submit Request**.
5. You will receive a reference number and email confirmation. Track progress under **Requests → My Requests**.

---

## 5. How to Track Your Levy Payments

1. Navigate to **Finance → Levy Payments**.
2. The page shows your current balance, the next due date, and a full payment history.
3. To pay online, click **Pay Now** and enter your card details (Visa/Mastercard accepted; secured by Stripe).
4. Receipts are emailed automatically after each payment.
5. Carry-forward credits (e.g., from volunteer events) appear as deductions in your statement.

> Levy quarters are due in **March, June, September, and December**. Late payments may attract interest as per the OC
> rules.

---

## 6. Parcel Notifications

When a parcel is delivered and logged at the front desk for your unit, you will automatically receive a **bell
notification** (🔔) in the portal header. The notification includes:

- The carrier name (e.g., Australia Post, DHL)
- A short description (if logged by admin staff)
- The tracking number (if provided)
- A reminder to collect from the front desk

You can see all parcels for your unit at **Dashboard → Parcels**. From there you can also view live or simulated
tracking for any parcel that has a tracking number recorded.

---

## 7. Community OS Features

Community OS is a suite of tools that gives owners visibility into the health of their building, a voice in community
decisions, and the ability to reduce levies through participation and savings.

### Building Health Score (`/dashboard/health`)

The Building Health Score is a 0–100 composite that summarises how well the building is being managed across five
dimensions. It is shown as a letter grade on your dashboard.

| Grade | Score range | What it means                                                      |
|-------|-------------|--------------------------------------------------------------------|
| **A** | 80–100      | Building is well-governed, financially healthy, and highly engaged |
| **B** | 65–79       | Generally well-managed; one or two dimensions need attention       |
| **C** | 50–64       | Noticeable gaps — the EC or Strata Manager should address these    |
| **D** | Below 50    | Action required; significant risk to property values or safety     |

The five dimensions and their weights are:

| Dimension               | Weight | What drives the score                      |
|-------------------------|--------|--------------------------------------------|
| Financial Compliance    | 25%    | Arrears rate, on-time levy payment ratio   |
| Governance Activity     | 20%    | Proposal frequency, voter turnout          |
| Maintenance Response    | 20%    | Average days to close maintenance requests |
| Community Participation | 20%    | Volunteer registrations, event attendance  |
| Savings Performance     | 15%    | Verified savings YTD vs. budget target     |

Navigate to **Dashboard → Building Health** to see the full dimension breakdown and historical trend.

### Proposals and Voting (`/dashboard/proposals`)

The Proposals page lists all OC motions. Owners can vote on any open proposal.

**Proposal categories:**

| Category        | Typical use                                                 |
|-----------------|-------------------------------------------------------------|
| `capital_works` | Major spending decisions (e.g., lift replacement, repaving) |
| `by_law_change` | Amending the building's by-laws                             |
| `budget`        | Approval of annual budgets and levy rates                   |
| `general`       | Any other matter requiring an owner vote                    |

**Resolution types:**

| Type        | Threshold required to pass                                         |
|-------------|--------------------------------------------------------------------|
| `simple`    | Majority by unit-of-entitlement (UOE) — most common                |
| `special`   | 75% of UOE — required for major expenditure or by-law changes      |
| `unanimous` | 100% agreement — rare; required for certain constitutional changes |

**How to vote:**

1. Go to **Governance → Proposals** and open an active proposal.
2. Read the full motion text and any attached documents.
3. Select **For**, **Against**, or **Abstain** and click **Submit Vote**.
4. You will receive email confirmation. Votes are final and cannot be changed after submission.
5. Voting closes at the deadline shown on the proposal card. The outcome (Passed / Failed / Deferred) is published
   immediately after closing.

### Savings Ledger (`/dashboard/savings`)

The Savings Ledger shows verified savings that your Strata Manager or EC has recorded on behalf of the OC — for example,
a better electricity tariff, a bulk-purchase discount on common-area supplies, or a successfully negotiated contractor
rebate.

- **Savings YTD** is the total verified savings recorded in the current financial year.
- **All-time total** shows cumulative savings since the portal launched.
- Savings are broken down by category: Maintenance, Insurance, Utilities, Admin, Capital.
- Each entry includes a title, description, amount, date, and a link to supporting evidence.

Savings offset future levy increases. A consistently high Savings YTD contributes to your building's **Savings
Performance** dimension in the Health Score.

### Smart Requests (`/dashboard/smart-request`)

Smart Request is an intelligent triage system. Instead of having to know which department to contact, you describe your
issue in plain language and the system routes it automatically.

**Supported request types:**

- Maintenance issues (common property, your unit, intercom, lifts, garage doors)
- Levy and financial queries
- By-law questions and complaints
- Noise and neighbour disputes
- General strata questions

**How to submit a request:**

1. Navigate to **Requests → Smart Request** or tap the floating **+** button on the dashboard.
2. Type a plain-language description of your issue.
3. Review the suggested category and priority — adjust if the system misclassified.
4. Click **Submit Request**. You receive a reference number and email confirmation.
5. Track your request under **Requests → My Requests**. You are notified by email and in-app when the status changes.

**Priority SLAs:**

| Priority | Target first response | Target resolution |
|----------|-----------------------|-------------------|
| Critical | 2 hours               | 24 hours          |
| High     | 4 hours               | 72 hours          |
| Medium   | 24 hours              | 7 days            |
| Low      | 72 hours              | 30 days           |

### Volunteer (`/dashboard/volunteer`)

The Volunteer programme lets owners contribute to the community and earn levy credits in return.

**How it works:**

1. Navigate to **Community → Volunteer Events** to see upcoming events.
2. Each event listing shows the date, location, a description of the work, and the **levy credit amount (AUD)** you will
   earn for attending.
3. Click **Register** and confirm. You can withdraw your registration before the event date.
4. Attend the event. The Strata Manager marks attendance after the event.
5. Within 24 hours of completion, the levy credit is applied to your lot account and deducted from your next levy
   instalment.
6. You receive an email confirmation with your updated account balance.

> **Credits appear as a line-item deduction** on your next levy notice. They cannot be converted to cash.

---

## 8. Preventive Maintenance Plan (PPM)

The PPM Schedule shows the building's planned maintenance programme — 79 recurring tasks across 15 areas of the
property. As an owner you can view the full schedule but cannot modify it.

### Accessing the PPM Schedule

1. Go to **Maintenance** → **PPM Schedule** tab.
2. Browse tasks by **Section** (e.g., Fire Protection, Lifts, Landscaping).
3. Filter by **Status**: Overdue, Due Soon, or Scheduled.
4. Toggle **Compliance Only** to see the 8 legally required fire safety inspections.

### What You Can See

| Column      | Description                                                    |
|-------------|----------------------------------------------------------------|
| Section     | Building area (Fire Protection, Lifts, Landscaping, etc.)      |
| Description | Specific maintenance task                                      |
| Frequency   | How often the task recurs (monthly, quarterly, annually, etc.) |
| Next Due    | When the task is next scheduled                                |
| Status      | Scheduled / Due Soon / Overdue                                 |

> **Note:** Contractor contact details and completion history are visible to building managers only.

### Compliance Health Score

The **Compliance Health** percentage (top-right of the PPM tab) shows what proportion of legally required fire safety
inspections are current. A score of 100% means the building is fully compliant.

### Notifications

You will receive an email notification when a fire safety (compliance) inspection is scheduled within the next 7 days. *
*No action is required from residents** — contractors access common areas only.

---

## 9. Contact Information

| Contact                     | Details                                                                                     |
|-----------------------------|---------------------------------------------------------------------------------------------|
| **Strata Manager**          | [strata.manager@eastgateresidences.com.au](mailto:strata.manager@eastgateresidences.com.au) |
| **Executive Committee**     | [ec@eastgateresidences.com.au](mailto:ec@eastgateresidences.com.au)                         |
| **Emergency (after hours)** | See `/emergency-services` on the portal                                                     |
| **Technical support**       | [support@silverfoxtechnologies.com.au](mailto:support@silverfoxtechnologies.com.au)         |
