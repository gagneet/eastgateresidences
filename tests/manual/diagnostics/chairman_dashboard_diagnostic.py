#!/usr/bin/env python3
"""
Diagnostic script: verify chairman account and dashboard data in the live DB.

This is a manual diagnostic tool, NOT a unit test.  It connects to the live
MongoDB database and may log sensitive production data (hashed credentials,
email addresses).  It must NEVER run in the automated test suite.

Run manually when debugging chairman access issues:
    backend/venv/bin/python3 manual/diagnostics/chairman_dashboard_diagnostic.py
"""
import os
import sys
from pathlib import Path

import asyncio
import bcrypt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# bare load_dotenv() only walks up from this script's own directory
# (manual/diagnostics/), never into backend/ where .env actually lives.
load_dotenv(Path(__file__).resolve().parent.parent.parent / "backend" / ".env")


async def test_chairman_dashboard():
    """Test chairman login and dashboard data"""

    # Connect to database
    client = AsyncIOMotorClient(os.getenv('MONGO_URL'))
    db = client[os.getenv('DB_NAME')]

    print("=== Testing Chairman Account ===\n")

    # 1. Verify chairman user exists
    chairman = await db.users.find_one({'email': 'anthony@eastgateresidences.com.au'})

    if not chairman:
        print("❌ Chairman user not found!")
        return False

    print(f"✅ Chairman user found:")
    print(f"   Name: {chairman.get('full_name')}")
    print(f"   Role: {chairman.get('role')}")
    print(f"   Unit: {chairman.get('unit_number')}")
    print(f"   Approved: {chairman.get('is_approved')}")

    # 2. Verify password
    stored_hash = chairman.get('password_hash')
    if stored_hash and bcrypt.checkpw(os.environ.get("E2E_CHAIRMAN_PASSWORD", "").encode('utf-8'), stored_hash.encode('utf-8')):
        print(f"✅ Password verified")
    else:
        print(f"❌ Password verification failed")
        return False

    # 3. Test building KPIs data
    print(f"\n=== Testing Building KPIs Data ===\n")

    # Get budget for 2026-2027
    budget = await db.budgets.find_one({'financial_year': '2026-2027'})
    if not budget:
        print("❌ Budget not found for 2026-2027")
        return False

    print(f"✅ Budget found for 2026-2027:")
    print(f"   Admin Fund Income: ${budget.get('admin_fund_income', 0):,.2f}")
    print(f"   Sinking Fund Income: ${budget.get('sinking_fund_income', 0):,.2f}")
    print(f"   Admin Fund Closing: ${budget.get('admin_fund_closing', 0):,.2f}")
    print(f"   Sinking Fund Closing: ${budget.get('sinking_fund_closing', 0):,.2f}")

    total_levied = budget.get('admin_fund_income', 0) + budget.get('sinking_fund_income', 0)
    print(f"   Total Levied: ${total_levied:,.2f}")

    # 4. Calculate arrears from units
    print(f"\n=== Calculating Arrears ===\n")

    units = await db.units.find({}, {'unit_number': 1, 'balance_owing': 1, 'balance_credit': 1}).to_list(200)
    total_units = len(units)

    units_in_arrears = 0
    total_arrears = 0.0
    arrears_list = []

    for unit in units:
        balance_owing = unit.get('balance_owing', 0)
        if balance_owing > 0:
            units_in_arrears += 1
            total_arrears += balance_owing
            arrears_list.append({
                'unit': unit.get('unit_number'),
                'amount': balance_owing
            })

    # Sort by amount descending
    arrears_list.sort(key=lambda x: x['amount'], reverse=True)

    print(f"✅ Units analyzed:")
    print(f"   Total Units: {total_units}")
    print(f"   Units in Arrears: {units_in_arrears}")
    print(f"   Total Arrears: ${total_arrears:,.2f}")

    # 5. Calculate collection metrics
    total_paid = total_levied - total_arrears
    collection_rate = (total_paid / total_levied * 100) if total_levied > 0 else 0
    arrears_percentage = (units_in_arrears / total_units * 100) if total_units > 0 else 0

    print(f"\n=== Collection Metrics ===\n")
    print(f"   Total Levied: ${total_levied:,.2f}")
    print(f"   Total Paid: ${total_paid:,.2f}")
    print(f"   Collection Rate: {collection_rate:.2f}%")
    print(f"   Arrears Percentage: {arrears_percentage:.2f}%")

    # 6. Show top 5 arrears
    print(f"\n=== Top 5 Units in Arrears ===\n")
    for i, arrear in enumerate(arrears_list[:5], 1):
        print(f"   {i}. {arrear['unit']}: ${arrear['amount']:,.2f}")

    # 7. Test finance data for charts
    print(f"\n=== Testing Finance Chart Data ===\n")

    finance_count = await db.finance.count_documents({})
    print(f"✅ Total finance entries: {finance_count}")

    # Count by entry type
    income_count = await db.finance.count_documents({'entry_type': 'income'})
    expense_count = await db.finance.count_documents({'entry_type': 'expense'})

    print(f"   Income entries: {income_count}")
    print(f"   Expense entries: {expense_count}")

    # Sample entries
    sample_income = await db.finance.find_one({'entry_type': 'income'})
    sample_expense = await db.finance.find_one({'entry_type': 'expense'})

    if sample_income:
        print(f"\n   Sample Income Entry:")
        print(f"     Category: {sample_income.get('category')}")
        print(f"     Amount: ${sample_income.get('amount', 0):,.2f}")
        print(f"     Date: {sample_income.get('date', '')[:10]}")

    if sample_expense:
        print(f"\n   Sample Expense Entry:")
        print(f"     Category: {sample_expense.get('category')}")
        print(f"     Amount: ${sample_expense.get('amount', 0):,.2f}")
        print(f"     Date: {sample_expense.get('date', '')[:10]}")

    # 8. Final summary
    print(f"\n{'=' * 50}")
    print(f"✅ ALL DASHBOARD DATA VERIFIED")
    print(f"{'=' * 50}")
    print(f"\nExpected Dashboard Display:")
    print(f"  • Collection Rate: {collection_rate:.2f}%")
    print(f"  • Units in Arrears: {units_in_arrears} / {total_units}")
    print(f"  • Total Arrears: ${total_arrears:,.2f}")
    print(f"  • Admin Fund: ${budget.get('admin_fund_closing', 0):,.2f}")
    print(f"  • Sinking Fund: ${budget.get('sinking_fund_closing', 0):,.2f}")
    print(f"\nLogin at: https://eastgateresidences.com.au/login")
    print(f"Username: anthony@eastgateresidences.com.au")
    print(f"Password: $E2E_CHAIRMAN_PASSWORD")
    print(f"\n⚠️  Remember to hard-refresh browser (Ctrl+Shift+R)")

    client.close()
    return True


if __name__ == "__main__":
    try:
        result = asyncio.run(test_chairman_dashboard())
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
