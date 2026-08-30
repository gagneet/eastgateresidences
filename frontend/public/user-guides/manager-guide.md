# Manager Guide — EastGate Residences Portal

**For:** Strata Managers at 14 Hoolihan Street, Denman Prospect ACT  
**Role:** `strata_manager` (also applies to `chairman` and `ec_member` where noted)  
**URL:** https://eastgateresidences.com.au/dashboard

---

## 1. Managing Incoming Requests (Smart Request Triage)

Smart Requests submitted by residents land in your queue with an auto-suggested category and priority.

1. Navigate to **Requests → Smart Requests Queue**.
2. Filter by **status: Pending** to see new submissions.
3. For each request:
    - Review the auto-suggested **category** and **priority**. Override if the AI misclassified (see GAP-015 in
      `GAPS_AND_FUTURE.md`).
    - Assign to yourself or a team member using the **Assign** dropdown.
    - Update status to **In Progress**.
4. To resolve: update status to **Resolved** and enter a resolution note. The resident is notified automatically.
5. For requests requiring a work order, click **Create Work Order** directly from the request detail.

**SLA targets:**
| Priority | First response | Resolution |
|----------|---------------|------------|
| Critical | 2 hours | 24 hours |
| High | 4 hours | 72 hours |
| Medium | 24 hours | 7 days |
| Low | 72 hours | 30 days |

Red badges on queue items indicate an SLA breach is imminent or has occurred.

---

## 2. Creating and Managing Work Orders

1. Navigate to **Maintenance → Work Orders → New**.
2. Fill in: description, assigned contractor, estimated cost, and target completion date.
3. Link the work order to a source request (optional but recommended for traceability).
4. Save. The contractor (if they have a portal account) receives an email notification.
5. When the job is done, update status to **Completed** and attach the invoice PDF.
6. The system automatically creates a draft **purchase order** entry in the financial module.

---

## 3. Recording Savings Events

When you negotiate a saving on behalf of the OC (e.g., a contractor discount, a bulk-purchase rebate, an insurance
saving), record it so it appears in the **Savings YTD** widget on owner dashboards.

1. Navigate to **Finance → Savings Ledger → Record Saving**.
2. Enter:
    - **Title** (e.g., "Electricity tariff rebate — Q2 2026")
    - **Category** (Maintenance, Insurance, Utilities, Admin, Capital)
    - **Amount saved** (AUD)
    - **Date** and a brief description
    - Attach evidence (invoice comparison, email from supplier)
3. Click **Save**. The entry is immediately visible on the Savings YTD card.

Savings records are permanent — they cannot be deleted, only annotated.

---

## 4. Managing Volunteer Events and Applying Credits

### Create an event

1. Navigate to **Community → Volunteer Events → New Event**.
2. Fill in: title, description, date/time, location, maximum volunteers, and the **levy credit amount (AUD)** awarded
   per attendee.
3. Publish. Residents can register immediately.

### Mark attendance and apply credits

1. After the event, navigate to the event detail page.
2. Click **Mark Complete**.
3. In the attendees list, tick each resident who attended.
4. Click **Apply Credits**. The system runs a MongoDB transaction that:
    - Debits the OC's volunteer credit budget
    - Credits each attendee's `lot_account.ledger_balance`
    - Creates a `journal_entries` record for audit
5. Attendees receive an email confirmation with their new balance.

> **Important:** Credit application requires MongoDB replica set. If you see a transaction error, contact the system
> administrator (GAP-013).

---

## 5. Opening and Closing Proposal Votes

### Open voting on a proposal

1. Navigate to **Governance → Proposals**.
2. Find a proposal in **Draft** status (created by you or an EC member).
3. Review the proposal details. Ensure the description, category, and supporting documents are complete.
4. Click **Open Voting** and set the **closing date/time**.
5. All eligible voters (owners) receive an email and in-app notification immediately.

### Close voting

1. Navigate to the open proposal.
2. Click **Close Voting**. You can close early or wait for the automated close at the deadline.
3. The system calculates the outcome (For/Against/Abstain counts + UOE-weighted result).
4. Set the **outcome** (Passed / Failed / Deferred) and add a resolution note.
5. A closing notification is sent to all voters.

> Proposals require a **simple majority by UOE** to pass by default. Special resolutions require 75% — set this in the
> proposal's `resolution_type` field.

---

## 6. Understanding the Building Health Score

The Building Health Score (0–100) is displayed on the management dashboard and owner dashboards. It is recomputed on
demand or when you trigger a recompute.

| Dimension               | Weight | Source data                                |
|-------------------------|--------|--------------------------------------------|
| Financial Compliance    | 25%    | Arrears rate, on-time levy payment ratio   |
| Governance Activity     | 20%    | Proposal frequency, voter turnout          |
| Maintenance Response    | 20%    | Average days to close maintenance requests |
| Community Participation | 20%    | Volunteer registrations, event attendance  |
| Savings Performance     | 15%    | Savings YTD vs. budget target              |

**To force a recompute:** Navigate to **Admin → Community Dashboard → Recompute**. This calls
`POST /community-dashboard/recompute`.

> Note: Summaries may be stale if no recompute has been triggered recently (GAP-012).

---

## 7. Compliance Calendar and SLA Breach Alerts

1. Navigate to **Compliance → Calendar**.
2. The calendar shows all upcoming compliance deadlines (fire safety, insurance renewal, AGM, by-law review).
3. Items turning **amber** are within 30 days of deadline; **red** items are overdue.
4. Click any item to update its status, attach evidence, or assign an owner.
5. SLA breach alerts for smart requests appear as a badge on the Requests menu item. Click to see the full list sorted
   by most overdue first.

---

## 8. Community OS — Role Access and Quick Reference

This section summarises the Community OS feature set, who can do what, and where to find each function in the portal.

### Role access matrix

| Feature                            | Super Admin | Strata Manager | Chairman | EC Member | Owner | Tenant | Guest |
|------------------------------------|:-----------:|:--------------:|:--------:|:---------:|:-----:|:------:|:-----:|
| View Building Health Score         |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ❌    |   ❌   |
| Force health score recompute       |      ✅      |       ✅        |    ✅     |     ❌     |   ❌   |   ❌    |   ❌   |
| View proposals                     |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ❌    |   ❌   |
| Create proposals                   |      ✅      |       ✅        |    ✅     |     ✅     |   ❌   |   ❌    |   ❌   |
| Open / close voting                |      ✅      |       ✅        |    ✅     |     ❌     |   ❌   |   ❌    |   ❌   |
| Cast vote                          |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ❌    |   ❌   |
| View savings ledger                |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ❌    |   ❌   |
| Record savings event               |      ✅      |       ✅        |    ✅     |     ✅     |   ❌   |   ❌    |   ❌   |
| Submit Smart Request               |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ✅    |   ✅   |
| Assign / resolve requests          |      ✅      |       ✅        |    ✅     |     ✅     |   ❌   |   ❌    |   ❌   |
| View volunteer events              |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ✅    |   ❌   |
| Register for volunteer event       |      ✅      |       ✅        |    ✅     |     ✅     |   ✅   |   ✅    |   ❌   |
| Create / complete volunteer events |      ✅      |       ✅        |    ✅     |     ❌     |   ❌   |   ❌    |   ❌   |

### Quick navigation reference

| Task                     | Portal path                              | API endpoint                                |
|--------------------------|------------------------------------------|---------------------------------------------|
| Building Health Score    | Dashboard → Building Health              | `GET /community-dashboard/health-score`     |
| Building summary KPIs    | Dashboard → Community Dashboard          | `GET /community-dashboard/building-summary` |
| Force recompute          | Admin → Community Dashboard → Recompute  | `POST /community-dashboard/recompute`       |
| View proposals           | Governance → Proposals                   | `GET /proposals`                            |
| Create proposal          | Governance → Proposals → New             | `POST /proposals`                           |
| Open voting              | Proposal detail → Open Voting            | `POST /proposals/{id}/open`                 |
| Close voting             | Proposal detail → Close Voting           | `POST /proposals/{id}/close`                |
| View savings ledger      | Finance → Savings Ledger                 | `GET /savings`                              |
| Record savings           | Finance → Savings Ledger → Record Saving | `POST /savings`                             |
| Savings summary          | Finance → Savings Ledger → Summary       | `GET /savings/summary`                      |
| Smart request queue      | Requests → Smart Requests Queue          | `GET /workflow-requests`                    |
| Assign / resolve request | Request detail → Assign / Resolve        | `PUT /workflow-requests/{id}/status`        |
| Volunteer events         | Community → Volunteer Events             | `GET /volunteer`                            |
| Create volunteer event   | Community → Volunteer Events → New       | `POST /volunteer`                           |
| Mark event complete      | Event detail → Mark Complete             | `PUT /volunteer/{id}/complete`              |

### Workflow: Managing a proposal end-to-end

```
Draft (created by EC/manager)
  → [Open Voting] → Open (owners notified by email)
    → [Votes cast by owners] → …
      → [Close Voting] → Closed (outcome: Passed / Failed / Deferred)
        → Resolution note published → all voters notified
```

### Workflow: Processing a Smart Request

```
Submitted (pending)
  → [Manager triages] → Assigned to staff member → In Progress
    → [Work done / query answered] → Resolved (resolution note added)
      → [Resident confirms or auto-closes after 7 days] → Closed
```

### Workflow: Running a volunteer event

```
Event created (upcoming) → Residents register
  → [Event day] → Manager marks complete + selects attendees
    → [Credits applied] → Lot accounts updated → Residents notified
```

---

## 9. Preventive Maintenance Plan (PPM)

The PPM Schedule is the building's 79-item recurring maintenance calendar, sourced directly from the approved PPM
document (EastGate-UP13195-PPM-January2026.xlsx). It covers 15 sections and includes 8 legally mandated compliance items
under ACT legislation.

### Accessing the PPM Schedule

1. Navigate to **Maintenance** → **PPM Schedule** tab.
2. Use the **Section** dropdown (15 options) to filter by area.
3. Use the **Status** filter to view: Overdue, Due Soon, or Scheduled items.
4. Toggle **Compliance Only** to isolate the 8 fire safety compliance items.
5. Expand any row to see the full completion history.

### Status Indicators

| Badge        | Meaning                        | Required Action              |
|--------------|--------------------------------|------------------------------|
| 🔴 Overdue   | `next_due_date` is in the past | Book contractor immediately  |
| 🟡 Due Soon  | Due within 30 days             | Schedule contractor booking  |
| 🟢 Scheduled | More than 30 days away         | No immediate action required |

### Compliance Items (8 — Fire Safety)

Items marked with a red shield icon are legally mandated under the ACT Strata Act. The **Compliance Health Score** (
shown top-right of the PPM tab) measures what percentage of compliance items are current. Target: **100%**.

You receive automated email notifications at **30, 14, 7, and 0 days** before each compliance item is due, and daily if
any item is overdue.

### Logging Completion

1. Find the completed item and click **Log** in the Actions column.
2. Fill in:
    - **Completed By** — contractor name or company
    - **Completion Date** — actual date of completion
    - **Certificate Reference** — e.g., `AS1851-2026-001` (required for compliance items)
    - **Notes** — optional observations or defects noted
3. Click **Log Completion**. The system automatically:
    - Records the completion entry with your user credentials
    - Recomputes `next_due_date` = completion date + item frequency
    - Creates the next calendar event (visible in the Calendar tab)
    - Recalculates the compliance health score
    - Appends an audit log entry

### Manual Date Override

To reschedule a future occurrence without logging a completion, use the **Edit** action on the item. Updating
`Next Due Date` will also update the corresponding calendar event automatically.

### Vendor Assignment

You can assign a preferred contractor to each PPM item by updating the **Vendor Name** and **Contact** fields via the
edit action. This information is visible only to managers — owners see a restricted view.

### Daily Automated Notifications

The system sends daily email notifications (07:00 AEST) to all managers (super_admin, chairman, ec_member,
strata_manager) when items reach the 30/14/7/0-day thresholds, and once per day for any overdue items. Residents are
notified 7 days before compliance (fire safety) items are due — no resident action is required.

---

## 10. Contact Information

| Contact                     | Details                                                                             |
|-----------------------------|-------------------------------------------------------------------------------------|
| **Platform support**        | [support@silverfoxtechnologies.com.au](mailto:support@silverfoxtechnologies.com.au) |
| **Chairman**                | [chairman@eastgateresidences.com.au](mailto:chairman@eastgateresidences.com.au)     |
| **Emergency (after hours)** | See `/emergency-services` on the portal                                             |
