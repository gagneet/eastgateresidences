# Community OS Permission Matrix

**Backend source:** `backend/routers/` — permission checks use the `PERMISSIONS` dict and FastAPI
`Depends(require_permission(...))`.  
**Role hierarchy** (highest to lowest): `super_admin` → `strata_admin` → `strata_manager` → `ec_member` → `owner` →
`tenant` → `service_provider` → `guest`

> `chairman` is not a standalone role in the current system. Governance chair semantics are represented as
> `role=ec_member` with `ec_position=CHAIRMAN`.

---

## Role Abbreviations

| Abbr | Role             |
|------|------------------|
| SA   | super_admin      |
| CH   | ec_member + CHAIRMAN |
| SM   | strata_manager   |
| EC   | ec_member        |
| OW   | owner            |
| TN   | tenant           |
| SP   | service_provider |
| GU   | guest            |

---

## Proposals

| Permission                 | SA | CH | SM | EC | OW | TN | SP | GU |
|----------------------------|----|----|----|----|----|----|----|----|
| List proposals             | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| View proposal detail       | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| Create proposal            | ✅  | ✅  | ✅  | ✅  | —  | —  | —  | —  |
| Edit proposal (draft only) | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Open voting                | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Close voting               | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Cast vote                  | ✅  | ✅  | —  | ✅  | ✅  | —  | —  | —  |
| View voter list            | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Set outcome                | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

---

## Savings Ledger

| Permission                 | SA | CH | SM | EC | OW | TN | SP | GU |
|----------------------------|----|----|----|----|----|----|----|----|
| List savings events        | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| View savings summary (YTD) | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| Record saving              | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Edit saving                | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

> Savings records cannot be deleted by any role — only annotated.

---

## Volunteer Events

| Permission              | SA | CH | SM | EC | OW | TN | SP | GU |
|-------------------------|----|----|----|----|----|----|----|----|
| List events             | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  |
| View event detail       | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  |
| Create event            | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Edit event              | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Cancel event            | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Register for event      | ✅  | ✅  | —  | ✅  | ✅  | ✅  | —  | —  |
| Cancel own registration | ✅  | ✅  | —  | ✅  | ✅  | ✅  | —  | —  |
| Mark event complete     | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Apply levy credits      | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| View all registrations  | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

---

## Smart Requests (Workflow Requests)

| Permission        | SA | CH | SM | EC | OW | TN | SP | GU |
|-------------------|----|----|----|----|----|----|----|----|
| Submit request    | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| View own request  | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| View all requests | ✅  | ✅  | ✅  | ✅  | —  | —  | —  | —  |
| Assign request    | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Update status     | ✅  | ✅  | ✅  | ✅  | —  | —  | —  | —  |
| Resolve request   | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Close request     | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

---

## Community Dashboard

| Permission           | SA | CH | SM | EC | OW | TN | SP | GU |
|----------------------|----|----|----|----|----|----|----|----|
| Get health score     | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| Get building summary | ✅  | ✅  | ✅  | ✅  | ✅  | ✅  | —  | —  |
| Force recompute      | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

---

## Lot Accounts & Journal Entries

| Permission                 | SA | CH | SM | EC | OW | TN | SP | GU |
|----------------------------|----|----|----|----|----|----|----|----|
| View own lot account       | ✅  | ✅  | —  | —  | ✅  | —  | —  | —  |
| View any lot account       | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| View journal entries (own) | ✅  | ✅  | —  | —  | ✅  | —  | —  | —  |
| View all journal entries   | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |
| Create journal entry       | ✅  | ✅  | ✅  | —  | —  | —  | —  | —  |

> Journal entries are immutable once created. No role can edit or delete them.

---

## Feature Toggle Slugs (Community OS)

These toggles gate entire feature areas. If a toggle is disabled, all associated API routes return `403`.

| Toggle slug             | Controls                              |
|-------------------------|---------------------------------------|
| `proposals`             | All proposal endpoints and UI         |
| `savings_ledger`        | All savings endpoints and UI          |
| `volunteer`             | All volunteer event endpoints and UI  |
| `building_health_score` | Community dashboard health score card |
| `smart_requests`        | Smart Request intake form and queue   |

Toggle values are managed by `super_admin` via **Admin → Feature Toggles**.

---

## Notes

1. `ec_member` can create proposals (to prepare draft motions) but cannot open/close voting — this action is reserved
   for `strata_admin`, `strata_manager`, and governance-chair flows (`ec_position=CHAIRMAN`) to ensure a formal approval step.
2. `tenant` can submit smart requests and volunteer; they cannot vote on proposals or access financial data.
3. `service_provider` has no Community OS permissions — their dashboard is scoped to assigned maintenance work orders.
4. `guest` has no Community OS permissions. Guest role is read-only on announcements and directory only.
