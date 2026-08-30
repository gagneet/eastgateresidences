-- GAP-FIN-031: read-only Postgres verification for levy-year bank transactions,
-- receipts, and levy-item payment state.
--
-- Usage:
--   psql "$DATABASE_URL" -f backend/scripts/audits/gap_fin_031_pg_levy_year_reconciliation.sql
--
-- Optional psql overrides:
--   psql "$DATABASE_URL" \
--     -v scheme_number=13195 \
--     -v from_date=2021-01-01 \
--     -v to_date=2027-01-01 \
--     -v from_levy_year=2021 \
--     -v to_levy_year=2026 \
--     -f backend/scripts/audits/gap_fin_031_pg_levy_year_reconciliation.sql
--
-- Notes:
-- - Dates must be the building's levy-year boundary, not a generic financial-year
--   boundary. For East Gate, Mongo settings currently show start month January.
-- - This script is read-only. Do not use SQL to post receipts into the hash-chained
--   ledger; use FinancialCoreService.record_payment()/record_historical_payment().
-- - Current code may store finance.bank_transactions.bank_transaction_id in
--   finance.receipts.external_reference instead of receipts.bank_transaction_id.
--   The join below checks both so historical rows are not falsely reported as
--   missing solely because of that known link-quality issue.
-- - Aggregate totals are triage evidence only. Use the bucket query below to
--   classify rows before replaying approved paths or preparing a reviewed data
--   repair.

\set scheme_number '13195'
\set from_date '2021-01-01'
\set to_date '2027-01-01'
\set from_levy_year '2021'
\set to_levy_year '2026'

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id, s.scheme_number, s.scheme_name
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
)
SELECT 'scheme_context' AS check_name, *
  FROM scheme;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
)
SELECT EXTRACT(YEAR FROM bt.transaction_date)::int AS transaction_year,
       bt.source_type,
       bt.provenance_class,
       bt.confidence,
       bt.requires_review,
       count(*) AS bank_transaction_count,
       sum(bt.amount_cents) AS net_amount_cents,
       count(*) FILTER (WHERE bt.amount_cents > 0) AS positive_transaction_count,
       coalesce(sum(bt.amount_cents) FILTER (WHERE bt.amount_cents > 0), 0) AS positive_amount_cents,
       count(*) FILTER (WHERE bt.reconstruction_batch_id IS NOT NULL) AS with_reconstruction_batch
  FROM finance.bank_transactions bt
  JOIN scheme s ON s.scheme_id = bt.scheme_id
 WHERE bt.transaction_date >= :'from_date'::date
   AND bt.transaction_date < :'to_date'::date
 GROUP BY 1,2,3,4,5
 ORDER BY 1,2,3,4,5;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
)
SELECT EXTRACT(YEAR FROM r.received_on)::int AS received_year,
       r.channel,
       count(*) AS receipt_count,
       sum(r.amount_cents) AS amount_cents,
       count(*) FILTER (WHERE r.bank_transaction_id IS NOT NULL) AS with_bank_transaction_id,
       count(*) FILTER (WHERE r.external_reference IS NOT NULL) AS with_external_reference,
       count(*) FILTER (WHERE r.reconstruction_batch_id IS NOT NULL) AS with_reconstruction_batch
  FROM finance.receipts r
  JOIN scheme s ON s.scheme_id = r.scheme_id
 WHERE r.received_on >= :'from_date'::date
   AND r.received_on < :'to_date'::date
 GROUP BY 1,2
 ORDER BY 1,2;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
),
bank_to_receipt AS (
    SELECT bt.bank_transaction_id,
           bt.transaction_date,
           bt.amount_cents,
           r.receipt_id,
           r.amount_cents AS receipt_amount_cents,
           r.bank_transaction_id AS receipt_bank_transaction_id,
           r.external_reference
      FROM finance.bank_transactions bt
      JOIN scheme s ON s.scheme_id = bt.scheme_id
      LEFT JOIN finance.receipts r
        ON r.scheme_id = bt.scheme_id
       AND (
            r.bank_transaction_id = bt.bank_transaction_id
            OR r.external_reference = bt.bank_transaction_id::text
       )
     WHERE bt.transaction_date >= :'from_date'::date
       AND bt.transaction_date < :'to_date'::date
       AND bt.amount_cents > 0
)
SELECT EXTRACT(YEAR FROM transaction_date)::int AS transaction_year,
       count(*) AS positive_bank_transactions,
       sum(amount_cents) AS positive_bank_amount_cents,
       count(*) FILTER (WHERE receipt_id IS NOT NULL) AS matched_receipt_count,
       coalesce(sum(amount_cents) FILTER (WHERE receipt_id IS NOT NULL), 0) AS matched_receipt_source_cents,
       coalesce(sum(receipt_amount_cents) FILTER (WHERE receipt_id IS NOT NULL), 0) AS matched_receipt_actual_cents,
       count(*) FILTER (WHERE receipt_id IS NULL) AS missing_receipt_count,
       coalesce(sum(amount_cents) FILTER (WHERE receipt_id IS NULL), 0) AS missing_receipt_cents,
       count(*) FILTER (WHERE receipt_id IS NOT NULL AND receipt_bank_transaction_id IS NULL) AS matched_by_external_reference_only
  FROM bank_to_receipt
 GROUP BY 1
 ORDER BY 1;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
),
bank_to_receipt AS (
    SELECT bt.bank_transaction_id,
           bt.transaction_date,
           bt.amount_cents,
           bt.external_transaction_id,
           bt.source_type,
           bt.provenance_class,
           bt.requires_review,
           bt.reconstruction_batch_id,
           r.receipt_id,
           r.bank_transaction_id AS receipt_bank_transaction_id,
           r.external_reference
      FROM finance.bank_transactions bt
      JOIN scheme s ON s.scheme_id = bt.scheme_id
      LEFT JOIN finance.receipts r
        ON r.scheme_id = bt.scheme_id
       AND (
            r.bank_transaction_id = bt.bank_transaction_id
            OR r.external_reference = bt.bank_transaction_id::text
       )
     WHERE bt.transaction_date >= :'from_date'::date
       AND bt.transaction_date < :'to_date'::date
       AND bt.amount_cents > 0
),
bucketed AS (
    SELECT *,
           CASE
             WHEN receipt_id IS NOT NULL AND receipt_bank_transaction_id IS NOT NULL THEN 'linked_by_bank_transaction_id'
             WHEN receipt_id IS NOT NULL THEN 'legacy_external_reference_only'
             WHEN reconstruction_batch_id IS NOT NULL AND requires_review IS FALSE THEN 'missing_receipt_reconstruction_no_review'
             WHEN reconstruction_batch_id IS NOT NULL THEN 'missing_receipt_reconstruction_requires_review'
             ELSE 'missing_receipt_unclassified_positive_bank_tx'
           END AS reconciliation_bucket
      FROM bank_to_receipt
)
SELECT EXTRACT(YEAR FROM transaction_date)::int AS transaction_year,
       reconciliation_bucket,
       count(*) AS row_count,
       coalesce(sum(amount_cents), 0) AS amount_cents,
       min(transaction_date) AS first_transaction_date,
       max(transaction_date) AS last_transaction_date
  FROM bucketed
 GROUP BY 1,2
 ORDER BY 1,2;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
)
SELECT lr.financial_year AS levy_year,
       count(*) AS levy_item_count,
       count(DISTINCT li.lot_id) AS lot_count,
       sum(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents) AS charged_cents,
       sum(li.paid_cents) AS paid_cents,
       sum(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents - li.paid_cents) AS outstanding_cents
  FROM finance.levy_items li
  JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
  JOIN scheme s ON s.scheme_id = li.scheme_id
 WHERE lr.financial_year ~ '^[0-9]{4}'
   AND substring(lr.financial_year from 1 for 4)::int BETWEEN :'from_levy_year'::int AND :'to_levy_year'::int
 GROUP BY 1
 ORDER BY 1;

WITH scheme AS (
    SELECT s.tenant_id, s.scheme_id
      FROM core.schemes s
     WHERE s.scheme_number = :'scheme_number'
),
lot_levies AS (
    SELECT lr.financial_year AS levy_year,
           l.lot_number,
           l.unit_number,
           l.lot_id,
           sum(li.principal_cents + li.gst_cents + li.interest_cents + li.recovery_costs_cents) AS charged_cents,
           sum(li.paid_cents) AS paid_cents
      FROM finance.levy_items li
      JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
      JOIN core.lots l ON l.lot_id = li.lot_id
      JOIN scheme s ON s.scheme_id = li.scheme_id
     WHERE lr.financial_year = :'to_levy_year'
     GROUP BY 1,2,3,4
),
lot_receipts AS (
    SELECT :'to_levy_year' AS levy_year,
           r.lot_id,
           sum(r.amount_cents) AS receipt_cents,
           count(*) AS receipt_count
      FROM finance.receipts r
      JOIN scheme s ON s.scheme_id = r.scheme_id
     WHERE r.received_on >= (:'to_levy_year' || '-01-01')::date
       AND r.received_on < ((:'to_levy_year'::int + 1)::text || '-01-01')::date
       AND r.channel = 'bank_transfer'
     GROUP BY r.lot_id
)
SELECT ll.levy_year,
       ll.lot_number,
       ll.unit_number,
       ll.charged_cents,
       ll.paid_cents,
       coalesce(lr.receipt_cents, 0) AS receipt_cents,
       coalesce(lr.receipt_count, 0) AS receipt_count,
       ll.charged_cents - ll.paid_cents AS outstanding_cents
  FROM lot_levies ll
  LEFT JOIN lot_receipts lr ON lr.lot_id = ll.lot_id
 ORDER BY ll.unit_number NULLS LAST, ll.lot_number;
