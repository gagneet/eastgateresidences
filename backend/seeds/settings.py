"""
Settings and Configuration Seed Data

Creates initial site settings, emergency services, and EC member data.
"""
import uuid
from datetime import datetime, timezone


def get_site_settings():
    """Default site settings"""
    return {
        'id': 'main',
        'building_name': 'East Gate Residences',
        'building_address': '14 Hoolihan Street, Denman Prospect, ACT 2611',
        'building_phone': '+61 2 6123 4567',
        'building_email': 'admin@eastgate.gagneet.com',
        'manager_name': 'East Gate Strata Management',
        'manager_email': 'manager@eastgate.gagneet.com',
        'manager_phone': '+61 2 6123 4568',
        'hero_image': '/images/east_gate_residences.jpg',
        'logo_url': None,
        'total_units': 87,
        'total_entitlements': 140.0,
        'timezone': 'Australia/Canberra',
        'financial_year_start': '07-01',
        'levy_payment_methods': ['DEFT', 'BPAY', 'Direct Deposit', 'Credit Card'],
        'directory_visible_to_residents': True,
        'marketplace_enabled': True,
        'rate_limit_register': 5,
        'rate_limit_login': 10,
        'rate_limit_forgot_password': 5,
        'rate_limit_reset_password': 5,
        'rate_limit_change_password': 10,
        'rate_limit_registration_decision': 10,
        'rate_limit_registration_invite_lookup': 20,
        'rate_limit_multiplier': 1.0,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }


def get_emergency_services():
    """Emergency contact information — East Gate Residences, Denman Prospect ACT"""
    now = datetime.now(timezone.utc).isoformat()
    services = [
        # ── EMERGENCY SERVICES ──────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Fire / Police / Ambulance',
            'phone': '000',
            'category': 'fire',
            'is_24_7': True,
            'is_private': False,
            'description': 'Always dial Triple Zero in a life-threatening emergency.',
            'order': 1,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Non-Emergency Police',
            'phone': '131 444',
            'category': 'police',
            'is_24_7': True,
            'is_private': False,
            'description': 'Police assistance for non-life-threatening situations.',
            'order': 2,
            'created_at': now
        },
        # ── STRATA MANAGEMENT (residents only) ──────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Civium Strata Manager – Jessica Minichiello',
            'phone': '1300 724 256',
            'email': 'UP13195@civium.com.au',
            'category': 'management',
            'is_24_7': False,
            'is_private': True,
            'description': 'Strata plan UP13195. Email: UP13195@civium.com.au',
            'order': 10,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Civium After Hours Emergency',
            'phone': '1300 724 256',
            'category': 'management',
            'is_24_7': True,
            'is_private': True,
            'description': 'After-hours building emergencies. Additional charges may apply.',
            'order': 11,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Civium Maintenance (Business Hours)',
            'phone': '1300 724 256',
            'email': 'UP13195@civium.com.au',
            'category': 'management',
            'is_24_7': False,
            'is_private': True,
            'description': 'Non-urgent maintenance requests during business hours.',
            'order': 12,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'East Gate Owners Corporation',
            'phone': '0412 017 126',
            'email': 'eastgatedenman13195@gmail.com',
            'category': 'management',
            'is_24_7': False,
            'is_private': True,
            'description': 'Chair: Anthony McDonald. Email: eastgatedenman13195@gmail.com',
            'order': 20,
            'created_at': now
        },
        # ── FIRE SYSTEM ──────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Fire System Maintenance – 360 Degree Fire',
            'phone': '02 6299 0006',
            'category': 'fire',
            'is_24_7': False,
            'is_private': False,
            'description': 'Fire alarm testing: every 2nd Thursday of the month, 9:00 AM.',
            'address': '90 High Street, Queanbeyan NSW 2620',
            'order': 30,
            'created_at': now
        },
        # ── LIFT ─────────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Lift Emergency – Electra Lift Co',
            'phone': '1300 725 290',
            'category': 'building',
            'is_24_7': True,
            'is_private': False,
            'description': 'Call if trapped in lift. Manufacturer: Brilliant Lifts. Fire Brigade: 000.',
            'order': 40,
            'created_at': now
        },
        # ── SECURITY ─────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Security & CCTV – CXI',
            'phone': '1300 798 325',
            'category': 'police',
            'is_24_7': True,
            'is_private': False,
            'description': 'Alarm & CCTV monitoring and maintenance.',
            'order': 50,
            'created_at': now
        },
        # ── ELECTRICAL ───────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Electrician – GMH Electrical',
            'phone': '0418 623 046',
            'category': 'utility',
            'is_24_7': False,
            'is_private': False,
            'order': 60,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Electrician – Lee Madden',
            'phone': '0400 913 440',
            'category': 'utility',
            'is_24_7': False,
            'is_private': False,
            'order': 61,
            'created_at': now
        },
        # ── PLUMBING ─────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': "Plumbing – Level Plumbing",
            'phone': '6185 0341',
            'category': 'utility',
            'is_24_7': False,
            'is_private': False,
            'order': 62,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': "Plumbing – Jim's Plumbing",
            'phone': '13 15 46',
            'category': 'utility',
            'is_24_7': True,
            'is_private': False,
            'order': 63,
            'created_at': now
        },
        # ── LOCKSMITH ────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Locksmith 24/7 – Canberra Locksmiths',
            'phone': '6285 3544',
            'category': 'utility',
            'is_24_7': True,
            'is_private': False,
            'order': 70,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Locksmith 24/7 – ACT Mobile Locksmith',
            'phone': '1800 167 420',
            'category': 'utility',
            'is_24_7': True,
            'is_private': False,
            'order': 71,
            'created_at': now
        },
        # ── PAINTING ─────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Painting – Lee Madden',
            'phone': '0400 913 440',
            'category': 'contractor',
            'is_24_7': False,
            'is_private': False,
            'order': 80,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Painting – Joseph Corradi',
            'phone': '0451 991 986',
            'category': 'contractor',
            'is_24_7': False,
            'is_private': False,
            'order': 81,
            'created_at': now
        },
        # ── HOT WATER ────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Hot Water System – Stiebel Eltron',
            'phone': '1800 153 351',
            'category': 'utility',
            'is_24_7': False,
            'is_private': False,
            'description': 'Heat pump hot water system support.',
            'order': 90,
            'created_at': now
        },
        # ── GLASS ─────────────────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'Glass Repair – Capital Glass',
            'phone': '0409 070 224',
            'category': 'building',
            'is_24_7': False,
            'is_private': False,
            'order': 100,
            'created_at': now
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Glass Repair – EW Glass',
            'phone': '6280 5091',
            'category': 'building',
            'is_24_7': False,
            'is_private': False,
            'order': 101,
            'created_at': now
        },
        # ── GENERAL SERVICES ──────────────────────────────────────────────────
        {
            'id': str(uuid.uuid4()),
            'name': 'General Services – Lee Madden',
            'phone': '0400 913 440',
            'category': 'contractor',
            'is_24_7': False,
            'is_private': False,
            'description': 'Painting, electrical and general building maintenance.',
            'order': 110,
            'created_at': now
        },
    ]

    return services


def get_ec_members():
    """Executive Committee members"""
    members = [
        {
            'id': str(uuid.uuid4()),
            'name': 'Anthony McDonald',
            'position': 'Chairman',
            'email': 'anthony@eastgateresidences.com.au',
            'phone': '+61 412 345 678',
            'bio': 'Chairman of the Executive Committee.',
            'image': None,
            'order': 1,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Brenda Thompson',
            'position': 'Treasurer',
            'email': 'brenda.thompson@eastgateresidences.com.au',
            'phone': '+61 434 567 890',
            'bio': 'Treasurer of the Executive Committee.',
            'image': None,
            'order': 2,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Kimberley Ruth Swords',
            'position': 'Secretary',
            'email': 'kimberly.swords@eastgateresidences.com.au',
            'phone': '+61 423 456 789',
            'bio': 'Secretary of the Executive Committee.',
            'image': None,
            'order': 3,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Marcelo Ramos da Silva',
            'position': 'Committee Member',
            'email': 'marcelo.dasilva@eastgateresidences.com.au',
            'phone': '+61 445 678 901',
            'bio': 'Executive Committee Member.',
            'image': None,
            'order': 4,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Daniel Smart',
            'position': 'Committee Member',
            'email': 'daniel.smart@eastgateresidences.com.au',
            'phone': '+61 456 789 012',
            'bio': 'Executive Committee Member.',
            'image': None,
            'order': 5,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        {
            'id': str(uuid.uuid4()),
            'name': 'Jessica Minichiello',
            'position': 'Strata Manager - Civium',
            'email': 'jessica@civium.com.au',
            'phone': '+61 2 6123 4568',
            'bio': 'Civium Property Group representative and strata manager for East Gate Residences.',
            'image': None,
            'order': 6,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
    ]

    return members


def get_email_settings():
    """Default email configuration"""
    return {
        'id': 'main',
        'provider': 'resend',
        'sender_email': 'noreply@eastgate.gagneet.com',
        'sender_name': 'East Gate Residences',
        'resend_api_key': '',  # Set from environment
        'sendgrid_api_key': '',
        'smtp_host': '',
        'smtp_port': 587,
        'smtp_user': '',
        'smtp_password': '',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
