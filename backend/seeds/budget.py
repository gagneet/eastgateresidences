"""
Budget and Financial Seed Data

Creates annual budget data based on the East Gate Residences financial structure.
Budget categories are embedded within each budget document (budgets.categories[]).

NOTE: The separate budget_categories collection has been DELETED (Phase 2 cleanup,
2026-02-18). All category data is stored in budgets.categories[] array only.
"""
import uuid
from datetime import datetime, timezone


def get_budget_categories():
    """
    DEPRECATED — The budget_categories collection has been deleted.
    Budget category data is embedded in budgets.categories[] instead.
    This function is kept for reference only and should not be called.
    Returns empty list to prevent accidental seeding.
    """
    return []


def get_annual_budget_data():
    """
    Returns annual budget data for 2024-2025 financial year.
    Based on 2025 Audited Financial Report.
    """
    budget_id = str(uuid.uuid4())

    budget = {
        'id': budget_id,
        'financial_year': '2024-2025',
        'admin_fund_opening': 140937.78,  # From balance sheet
        'sinking_fund_opening': 106039.46,  # From balance sheet
        'admin_fund_income': 349652.23,
        'admin_fund_expenses': 277998.80,
        'admin_fund_closing': 212591.21,  # Opening + surplus
        'sinking_fund_income': 117324.04,
        'sinking_fund_expenses': 48039.69,
        'sinking_fund_closing': 175323.81,  # Opening + surplus
        'approved_at': datetime.now(timezone.utc).isoformat(),
        'approved_by': 'admin@eastgate.com',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'categories': []
    }

    # Administrative Fund Categories (from actual report)
    admin_categories = [
        {'name': 'Gardens', 'budgeted_amount': 32696.00, 'fund_type': 'administrative', 'actual_amount': 32696.00},
        {'name': 'Water', 'budgeted_amount': 34361.00, 'fund_type': 'administrative', 'actual_amount': 34361.00},
        {'name': 'Strata Management', 'budgeted_amount': 29294.00, 'fund_type': 'administrative',
         'actual_amount': 29294.00},
        {'name': 'Cleaning Services', 'budgeted_amount': 27738.00, 'fund_type': 'administrative',
         'actual_amount': 27738.00},
        {'name': 'Building Insurance', 'budgeted_amount': 25000.00, 'fund_type': 'administrative',
         'actual_amount': 25000.00},
        {'name': 'Electricity', 'budgeted_amount': 23631.00, 'fund_type': 'administrative', 'actual_amount': 23631.00},
        {'name': 'Repairs & Maintenance', 'budgeted_amount': 15000.00, 'fund_type': 'administrative',
         'actual_amount': 15000.00},
        {'name': 'Waste Management', 'budgeted_amount': 10200.00, 'fund_type': 'administrative',
         'actual_amount': 10200.00},
        {'name': 'Administrative Costs', 'budgeted_amount': 8000.00, 'fund_type': 'administrative',
         'actual_amount': 8000.00},
        {'name': 'Legal & Professional Fees', 'budgeted_amount': 6500.00, 'fund_type': 'administrative',
         'actual_amount': 6500.00},
        {'name': 'Security Monitoring', 'budgeted_amount': 5100.00, 'fund_type': 'administrative',
         'actual_amount': 5100.00},
        {'name': 'Emergency Repairs', 'budgeted_amount': 12180.00, 'fund_type': 'administrative',
         'actual_amount': 12180.00},
        {'name': 'Banking & Finance', 'budgeted_amount': 1000.00, 'fund_type': 'administrative',
         'actual_amount': 1000.00},
        {'name': 'AGM & Meeting Costs', 'budgeted_amount': 3200.00, 'fund_type': 'administrative',
         'actual_amount': 3200.00},
        {'name': 'Fire Safety Testing', 'budgeted_amount': 1650.00, 'fund_type': 'administrative',
         'actual_amount': 1650.00},
        {'name': 'Lift Inspection', 'budgeted_amount': 2850.00, 'fund_type': 'administrative',
         'actual_amount': 2850.00},
        {'name': 'Building Signage', 'budgeted_amount': 980.00, 'fund_type': 'administrative', 'actual_amount': 980.00},
        {'name': 'Other Expenses', 'budgeted_amount': 38618.80, 'fund_type': 'administrative',
         'actual_amount': 38618.80}
    ]

    # Sinking Fund Categories (from actual report)
    sinking_categories = [
        {'name': 'Lift Maintenance', 'budgeted_amount': 24448.00, 'fund_type': 'sinking', 'actual_amount': 24448.00},
        {'name': 'Roof Repairs', 'budgeted_amount': 8500.00, 'fund_type': 'sinking', 'actual_amount': 8500.00},
        {'name': 'Car Park Maintenance', 'budgeted_amount': 6800.00, 'fund_type': 'sinking', 'actual_amount': 6800.00},
        {'name': 'Fire Safety Systems', 'budgeted_amount': 4200.00, 'fund_type': 'sinking', 'actual_amount': 4200.00},
        {'name': 'Security Systems', 'budgeted_amount': 2350.00, 'fund_type': 'sinking', 'actual_amount': 2350.00},
        {'name': 'Building Repairs', 'budgeted_amount': 1741.69, 'fund_type': 'sinking', 'actual_amount': 1741.69}
    ]

    all_categories = admin_categories + sinking_categories
    budget['categories'] = all_categories

    return budget


def get_levy_reminder_settings():
    """Default settings for automated levy reminders"""
    return {
        'id': 'main',
        'enabled': True,
        'reminder_days_before': [30, 14, 7, 1],  # Send reminders 30, 14, 7, and 1 day before due
        'overdue_reminder_days': [1, 7, 14, 30],  # Send overdue reminders
        'email_enabled': True,
        'sms_enabled': False,
        'whatsapp_enabled': False,
        'levy_due_date': 15,  # 15th of each quarter
        'levy_quarters': ['01-15', '04-15', '07-15', '10-15'],  # Quarterly levy dates
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
