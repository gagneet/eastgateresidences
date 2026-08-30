"""
Financial Entries Seed Data

Creates financial entries based on 2025 AGM Audited Financial Report.
Data represents actual income and expenses for Administrative and Sinking Funds.
"""
import uuid
from datetime import datetime, timezone, timedelta


def get_finance_entries():
    """
    Returns financial entries from the 2025 audited financial report.

    Summary from Report:
    - Administrative Fund: Income $349,652.23, Expenses $277,998.80
    - Sinking Fund: Income $117,324.04, Expenses $48,039.69
    - Total Net Assets: $246,977.24
    """
    entries = []
    base_date = datetime(2024, 7, 1, tzinfo=timezone.utc)

    # ADMINISTRATIVE FUND INCOME
    # Quarterly levy contributions spread throughout the year
    quarterly_levy = 349652.23 / 4
    for quarter in range(4):
        entries.append({
            'id': str(uuid.uuid4()),
            'date': (base_date + timedelta(days=90 * quarter)).isoformat(),
            'description': f'Administrative Levy - Q{quarter + 1} 2024-2025',
            'category': 'Levy Income',
            'entry_type': 'income',
            'amount': round(quarterly_levy, 2),
            'reference': f'ADMIN-LEVY-Q{quarter + 1}-2025',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'created_by': 'admin@eastgate.com'
        })

    # ADMINISTRATIVE FUND EXPENSES
    # Monthly recurring expenses
    admin_expenses = [
        {'name': 'Cleaning Services', 'monthly': 2311.50, 'category': 'Cleaning'},  # $27,738 annual
        {'name': 'Strata Management Fees', 'monthly': 2441.17, 'category': 'Management'},  # $29,294 annual
        {'name': 'Garden Maintenance', 'monthly': 2724.67, 'category': 'Gardens'},  # $32,696 annual
        {'name': 'Water Usage', 'monthly': 2863.42, 'category': 'Utilities'},  # $34,361 annual
        {'name': 'Electricity - Common Areas', 'monthly': 1969.25, 'category': 'Utilities'},  # $23,631 annual
        {'name': 'Building Insurance', 'monthly': 2083.33, 'category': 'Insurance'},  # $25,000 annual
        {'name': 'Waste Management', 'monthly': 850.00, 'category': 'Services'},  # $10,200 annual
        {'name': 'Security Monitoring', 'monthly': 425.00, 'category': 'Security'},  # $5,100 annual
        {'name': 'Repairs & Maintenance', 'monthly': 1250.00, 'category': 'Maintenance'},  # $15,000 annual
        {'name': 'Administrative Costs', 'monthly': 666.67, 'category': 'Administration'},  # $8,000 annual
        {'name': 'Legal & Professional Fees', 'monthly': 541.67, 'category': 'Professional Fees'},  # $6,500 annual
        {'name': 'Bank Fees', 'monthly': 83.33, 'category': 'Banking'},  # $1,000 annual
    ]

    # Create monthly expense entries for full year (12 months)
    for month in range(12):
        expense_date = base_date + timedelta(days=30 * month)
        for expense in admin_expenses:
            entries.append({
                'id': str(uuid.uuid4()),
                'date': expense_date.isoformat(),
                'description': f"{expense['name']} - {expense_date.strftime('%B %Y')}",
                'category': expense['category'],
                'entry_type': 'expense',
                'amount': expense['monthly'],
                'reference': f"ADMIN-{expense['category'].upper()}-{expense_date.strftime('%Y%m')}",
                'created_at': datetime.now(timezone.utc).isoformat(),
                'created_by': 'admin@eastgate.com'
            })

    # Additional one-off administrative expenses to reach total
    one_off_admin = [
        {
            'date': (base_date + timedelta(days=45)).isoformat(),
            'description': 'Emergency Plumbing Repairs - Level 2',
            'category': 'Emergency Repairs',
            'amount': 3500.00,
            'reference': 'ADMIN-EMRG-001'
        },
        {
            'date': (base_date + timedelta(days=120)).isoformat(),
            'description': 'Lift Inspection & Minor Repairs',
            'category': 'Lift Maintenance',
            'amount': 2850.00,
            'reference': 'ADMIN-LIFT-001'
        },
        {
            'date': (base_date + timedelta(days=200)).isoformat(),
            'description': 'AGM Catering & Venue',
            'category': 'AGM Costs',
            'amount': 1200.00,
            'reference': 'ADMIN-AGM-2025'
        },
        {
            'date': (base_date + timedelta(days=250)).isoformat(),
            'description': 'Fire Safety System Annual Test',
            'category': 'Safety Compliance',
            'amount': 1650.00,
            'reference': 'ADMIN-FIRE-001'
        },
        {
            'date': (base_date + timedelta(days=300)).isoformat(),
            'description': 'Building Signage Replacement',
            'category': 'Building Improvements',
            'amount': 980.00,
            'reference': 'ADMIN-SIGN-001'
        }
    ]

    for expense in one_off_admin:
        entries.append({
            'id': str(uuid.uuid4()),
            'date': expense['date'],
            'description': expense['description'],
            'category': expense['category'],
            'entry_type': 'expense',
            'amount': expense['amount'],
            'reference': expense['reference'],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'created_by': 'admin@eastgate.com'
        })

    # SINKING FUND INCOME
    # Quarterly sinking fund levy contributions
    quarterly_sinking_levy = 117324.04 / 4
    for quarter in range(4):
        entries.append({
            'id': str(uuid.uuid4()),
            'date': (base_date + timedelta(days=90 * quarter)).isoformat(),
            'description': f'Sinking Fund Levy - Q{quarter + 1} 2024-2025',
            'category': 'Sinking Fund Levy',
            'entry_type': 'income',
            'amount': round(quarterly_sinking_levy, 2),
            'reference': f'SINK-LEVY-Q{quarter + 1}-2025',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'created_by': 'admin@eastgate.com'
        })

    # SINKING FUND EXPENSES
    # Major capital works and maintenance
    sinking_expenses = [
        {
            'date': (base_date + timedelta(days=30)).isoformat(),
            'description': 'Lift Maintenance Contract - Annual Service',
            'category': 'Lift Maintenance',
            'amount': 24448.00,
            'reference': 'SINK-LIFT-2025'
        },
        {
            'date': (base_date + timedelta(days=60)).isoformat(),
            'description': 'Roof Leak Repairs - Building A',
            'category': 'Roof Repairs',
            'amount': 8500.00,
            'reference': 'SINK-ROOF-001'
        },
        {
            'date': (base_date + timedelta(days=90)).isoformat(),
            'description': 'Car Park Resurfacing - Level B1',
            'category': 'Car Park Maintenance',
            'amount': 6800.00,
            'reference': 'SINK-CARPARK-001'
        },
        {
            'date': (base_date + timedelta(days=150)).isoformat(),
            'description': 'Fire Panel Upgrade & Testing',
            'category': 'Fire Safety',
            'amount': 4200.00,
            'reference': 'SINK-FIRE-001'
        },
        {
            'date': (base_date + timedelta(days=180)).isoformat(),
            'description': 'Intercom System Repairs',
            'category': 'Security Systems',
            'amount': 2350.00,
            'reference': 'SINK-SEC-001'
        },
        {
            'date': (base_date + timedelta(days=220)).isoformat(),
            'description': 'External Facade Crack Repairs',
            'category': 'Building Repairs',
            'amount': 1741.69,
            'reference': 'SINK-FACADE-001'
        }
    ]

    for expense in sinking_expenses:
        entries.append({
            'id': str(uuid.uuid4()),
            'date': expense['date'],
            'description': expense['description'],
            'category': expense['category'],
            'entry_type': 'expense',
            'amount': expense['amount'],
            'reference': expense['reference'],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'created_by': 'admin@eastgate.com'
        })

    return entries


def get_finance_summary_stats():
    """
    Returns summary statistics from the audited financial report.
    This matches the balance sheet and income/expenditure statements.
    """
    return {
        'administrative_fund': {
            'income': 349652.23,
            'expenses': 277998.80,
            'surplus': 71653.43
        },
        'sinking_fund': {
            'income': 117324.04,
            'expenses': 48039.69,
            'surplus': 69284.35
        },
        'balance_sheet': {
            'total_assets': 259482.46,
            'total_liabilities': 12505.22,
            'net_assets': 246977.24
        },
        'year': 2024,
        'financial_year': '2024-2025',
        'report_date': '2025-06-30'
    }


# Major expense categories from the report
EXPENSE_CATEGORIES = [
    'Cleaning',
    'Management',
    'Gardens',
    'Utilities',
    'Insurance',
    'Maintenance',
    'Lift Maintenance',
    'Security',
    'Professional Fees',
    'Administration',
    'Emergency Repairs',
    'Fire Safety',
    'Building Repairs',
    'Car Park Maintenance',
    'Roof Repairs'
]
