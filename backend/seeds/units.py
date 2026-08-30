"""
Unit Entitlement Seed Data - 2026 Levy Data

Creates unit entitlements for East Gate Residences with actual 2026 levy data
from EastGate_Units13195_2025_FULL_UPDATED_WITH_2026_UOE_SPLIT_COMPARISON.xlsx
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from utils.unit_number import format_unit_display

# A SEED DECLARES the building's layout, so it is the one place a prefix rule may be
# written down — but it must still APPLY that rule through the shared formatter, never
# by gluing a prefix onto a number inline. Anything else drifts from
# db.settings type="unit_display", which is what every reader resolves against.
# See utils/unit_number.py and tests/backend/test_unit_identity_single_source.py.
_UNIT_DISPLAY_RULES = [
    {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
    {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
]


# Excel file path
EXCEL_PATH = Path(
    __file__).parent.parent.parent / 'EastGate_Units13195_2025_FULL_UPDATED_WITH_2026_UOE_SPLIT_COMPARISON.xlsx'

# Total entitlements constant (for backward compatibility)
TOTAL_ENTITLEMENTS = 10000.0

# Owner email overrides — maps exact owner_name / owner_name_b strings to their
# @eastgateresidences.com.au email addresses. Maintained by strata management.
OWNER_EMAIL_OVERRIDES = {
    "Anthony McDonald": "anthony@eastgateresidences.com.au",
    "Avneet Rooprai": "avneet@eastgateresidences.com.au",
    "Gagneet Singh": "gagneet@eastgateresidences.com.au",
    "Jane Pearce": "jane.pearce@eastgateresidences.com.au",
    "Yushan Han": "yushan.han@eastgateresidences.com.au",
    "Patina lnthavong": "patina.lnthavong@eastgateresidences.com.au",
    "Giovanna Giaccio": "giovanna.giaccio@eastgateresidences.com.au",
    "Marion Turley": "marion.turley@eastgateresidences.com.au",
    "Kimberley Ruth Swords": "kimberley.swords@eastgateresidences.com.au",
    "Rachel Clarke": "rachel.clarke@eastgateresidences.com.au",
    "Molly Hulands": "molly.hulands@eastgateresidences.com.au",
    "Charmi Dhanesha": "charmi.dhanesha@eastgateresidences.com.au",
    "Olivia Fairweather": "olivia.fairweather@eastgateresidences.com.au",
    "Jason Carter": "jason.carter@eastgateresidences.com.au",
    "Stephanie Traycevska": "stephanie.traycevska@eastgateresidences.com.au",
    "Katharine Mills": "katharine.mills@eastgateresidences.com.au",
    "Santpal Vekariya": "santpal.vekariya@eastgateresidences.com.au",
    "Tess McLaughlin": "tess.mclaughlin@eastgateresidences.com.au",
    "Sok-Joan Yoon": "sok-joan.yoon@eastgateresidences.com.au",
    "Yoonsuh Heo": "yoonsuh.heo@eastgateresidences.com.au",
    "Daniel Smart": "daniel.smart@eastgateresidences.com.au",
    "Nirosha Pragash": "nirosha.pragash@eastgateresidences.com.au",
    "Michael Kerrin Hopkins": "michael.hopkins@eastgateresidences.com.au",
    "Melissa Hopkins": "melissa.hopkins@eastgateresidences.com.au",
    "Niran Poglobe Karaeni": "niran.karaeni@eastgateresidences.com.au",
    "Maka-Moi Jimi Pickette": "maka-moi.pickette@eastgateresidences.com.au",
    "Georgina Kate Mitchell": "georgina.mitchell@eastgateresidences.com.au",
    "Isabelle Baldwin": "isabelle.baldwin@eastgateresidences.com.au",
    "Cassie Chapman": "cassie.chapman@eastgateresidences.com.au",
    "Amishaben Patel": "amishaben.patel@eastgateresidences.com.au",
    "Mark Jewell": "mark.jewell@eastgateresidences.com.au",
    "Peter Hanks": "peter.hanks@eastgateresidences.com.au",
    "Fiona Hanks": "fiona.hanks@eastgateresidences.com.au",
    "Arja Torpstrom": "arja.torpstrom@eastgateresidences.com.au",
    "Paul Stuart M Stuart": "paul.stuart@eastgateresidences.com.au",
    "Emma Watt": "emma.watt@eastgateresidences.com.au",
    "Blake Hayward": "blake.hayward@eastgateresidences.com.au",
    "Will Han": "will.han@eastgateresidences.com.au",
    "Minkyoung Kim": "minkyoung.kim@eastgateresidences.com.au",
    "Susan Roche": "susan.roche@eastgateresidences.com.au",
    "Anna Dziedzic": "anna.dziedzic@eastgateresidences.com.au",
    "Yanyan Pan": "yanyan.pan@eastgateresidences.com.au",
    "Yingyi Zheng": "yingyi.zheng@eastgateresidences.com.au",
    "Dylan Mousset": "dylan.mousset@eastgateresidences.com.au",
    "Shivani Dhanesha": "shivani.dhanesha@eastgateresidences.com.au",
    "Khyle Lambert": "khyle.lambert@eastgateresidences.com.au",
    "Kikham Sikoulabot": "kikham.sikoulabot@eastgateresidences.com.au",
    "Keith Edward Dears": "keith.dears@eastgateresidences.com.au",
    "Natasha Andrus": "natasha.andrus@eastgateresidences.com.au",
    "Mehulkumar Dhanesha": "mehulkumar.dhanesha@eastgateresidences.com.au",
    "Sarah Marrapodi": "sarah.marrapodi@eastgateresidences.com.au",
    "Larissa Bianca Melnyk": "larissa.melnyk@eastgateresidences.com.au",
    "Lamisa Ahmad": "lamisa.ahmad@eastgateresidences.com.au",
    "Joseph Vega": "joseph.vega@eastgateresidences.com.au",
    "Maria Vega": "maria.vega@eastgateresidences.com.au",
    "Marcelo Ramos da Silva": "marcelo.silva@eastgateresidences.com.au",
    "Brenda Thompson": "brenda.thompson@eastgateresidences.com.au",
    "Kwong Hang Wong": "kwong.wong@eastgateresidences.com.au",
    "Hannah Webb": "hannah.webb@eastgateresidences.com.au",
    "Joshua Solano": "joshua.solano@eastgateresidences.com.au",
    "Sarah Solano": "sarah.solano@eastgateresidences.com.au",
    "Joan Daupan": "joan.daupan@eastgateresidences.com.au",
    "Christen Luke Whisson": "christen.whisson@eastgateresidences.com.au",
    "Rachela Micheal Molong Amanamoi": "rachela.amanamoi@eastgateresidences.com.au",
    "Tin Leung": "tin.leung@eastgateresidences.com.au",
    "Jennifer Leung": "jennifer.leung@eastgateresidences.com.au",
    "Joshua Apps": "joshua.apps@eastgateresidences.com.au",
    "Zara Apps": "zara.apps@eastgateresidences.com.au",
    "Isabella Heggie": "isabella.heggie@eastgateresidences.com.au",
    "Dige Wang": "dige.wang@eastgateresidences.com.au",
    "Nicholas John Gandy": "nicholas.gandy@eastgateresidences.com.au",
    "Danny Chau": "danny.chau@eastgateresidences.com.au",
    "G Hourany": "g.hourany@eastgateresidences.com.au",
    "Morag Bullard": "morag.bullard@eastgateresidences.com.au",
    "Megan Joan Reeve": "megan.reeve@eastgateresidences.com.au",
    "Rose Marimon": "rose.marimon@eastgateresidences.com.au",
    "Xinyao Feng": "xinyao.feng@eastgateresidences.com.au",
    "Zhaocong Chen": "zhaocong.chen@eastgateresidences.com.au",
    "Sharyn Leanne Girvan": "sharyn.girvan@eastgateresidences.com.au",
    "Andrew Girvan": "andrew.girvan@eastgateresidences.com.au",
    "Jade Nicole Hall": "jade.hall@eastgateresidences.com.au",
    "Sheridan Carswell": "sheridan.carswell@eastgateresidences.com.au",
    "Jonathan Stanley": "jonathan.stanley@eastgateresidences.com.au",
    "Geoffery Osborne": "geoffery.osborne@eastgateresidences.com.au",
    "Rebecca Osborne": "rebecca.osborne@eastgateresidences.com.au",
    "Lloyd Taylor": "lloyd.taylor@eastgateresidences.com.au",
    "Lorna Stansfield": "lorna.stansfield@eastgateresidences.com.au",
    "Jake Paul Hope": "jake.hope@eastgateresidences.com.au",
    "Tess Tiatia": "tess.tiatia@eastgateresidences.com.au",
    "Kaushal Shah": "kaushal.shah@eastgateresidences.com.au",
    "Radhika Shah": "radhika.shah@eastgateresidences.com.au",
    "Hamish Angus": "hamish.angus@eastgateresidences.com.au",
    "Aditya Reddy Mekala": "aditya.mekala@eastgateresidences.com.au",
    "Saideepika Mekala": "saideepika.mekala@eastgateresidences.com.au",
    "Tara G Munro": "tara.munro@eastgateresidences.com.au",
    "Shane C Smith": "shane.smith@eastgateresidences.com.au",
    "Talor Stewart Lawton": "talor.lawton@eastgateresidences.com.au",
    "Rebecca Lawton": "rebecca.lawton@eastgateresidences.com.au",
    "Olivia Rollings": "olivia.rollings@eastgateresidences.com.au",
    "Mark Raets": "mark.raets@eastgateresidences.com.au",
    "Gunjan Pandey": "gunjan.pandey@eastgateresidences.com.au",
    "Rinku Pandey": "rinku.pandey@eastgateresidences.com.au",
    "Krystal Smith": "krystal.smith@eastgateresidences.com.au",
    "Paul Wywsik": "paul.wywsik@eastgateresidences.com.au",
    "Brian Cadungog": "brian.cadungog@eastgateresidences.com.au",
    "Joveryl Cadungog": "joveryl.cadungog@eastgateresidences.com.au",
    "Zachary Luke James": "zachary.james@eastgateresidences.com.au",
    "Aurora James": "aurora.james@eastgateresidences.com.au",
    "Adam Wayne Howson": "adam.howson@eastgateresidences.com.au",
    "Joshua Howson": "joshua.howson@eastgateresidences.com.au",
    "G Vellore Ranganathan": "g.ranganathan@eastgateresidences.com.au",
    "Kumari Ranganathan": "kumari.ranganathan@eastgateresidences.com.au",
    "Jinal Achal Dave": "jinal.dave@eastgateresidences.com.au",
    "Achal Dave": "achal.dave@eastgateresidences.com.au",
    "Riyu Kurian Abraham": "riyu.abraham@eastgateresidences.com.au",
    "Reshma Abraham": "reshma.abraham@eastgateresidences.com.au",
}


def parse_int_or_range(value):
    """Parse integer or range like '3-4', returning first number."""
    if pd.notna(value):
        value_str = str(value)
        if '-' in value_str:
            return int(value_str.split('-')[0])
        else:
            return int(float(value_str))  # Handle float strings like '2.0'
    return None


def get_units_seed_data():
    """
    Returns list of unit entitlements for the database with 2026 levy data.

    East Gate Residences has 87 lots:
    - Apartments: 70 units (UA001-UA070)
    - Townhouses: 17 units (TH071-TH087)
    Total UOE: 10,000.0

    Data sourced from 2026 budget Excel file with actual owner names,
    property details, and calculated levy amounts.
    """
    if not EXCEL_PATH.exists():
        print(f"⚠️  Excel file not found: {EXCEL_PATH}")
        print("   Using fallback unit data")
        return get_fallback_units_data()

    try:
        # Read Excel file
        df = pd.read_excel(EXCEL_PATH)

        # Track unit numbers by type for sequential numbering
        apartment_counter = 1
        townhouse_counter = 1

        units = []
        for _, row in df.iterrows():
            lot = int(row['Lot'])

            # Property type mapping
            prop_type = row['Property Type'].lower()
            unit_type = 'apartment' if prop_type == 'apartment' else 'townhouse'

            # Generate unit number based on type with UA/TH prefix
            if unit_type == 'apartment':
                unit_number = format_unit_display(apartment_counter, _UNIT_DISPLAY_RULES)
                apartment_counter += 1
            else:
                unit_number = format_unit_display(70 + townhouse_counter, _UNIT_DISPLAY_RULES)
                townhouse_counter += 1

            # Calculate quarterly levies
            admin_annual = float(row['2026 Admin Annual'])
            sinking_annual = float(row['2026 Sinking Annual'])
            admin_quarterly = round(admin_annual / 4, 2)
            sinking_quarterly = round(sinking_annual / 4, 2)

            # Parse bedrooms, bathrooms, car spaces
            bedrooms = parse_int_or_range(row['Bedrooms'])
            bathrooms = parse_int_or_range(row['Bathrooms'])
            car_spaces = parse_int_or_range(row['Garages'])

            owner_name = str(row['Owner Name']) if pd.notna(row['Owner Name']) else None

            # Split "Name A & Name B" into separate fields if present
            owner_name_primary = owner_name
            owner_name_secondary = None
            if owner_name and ' & ' in owner_name:
                parts = owner_name.split(' & ', 1)
                owner_name_primary = parts[0].strip()
                owner_name_secondary = parts[1].strip()

            unit_doc = {
                'id': str(uuid.uuid4()),
                'lot_number': f'LOT{lot}',
                'unit_number': unit_number,  # Already formatted as UA001-UA070 or TH071-TH087
                'unit_type': unit_type,
                'owner_name': owner_name_primary,
                'owner_name_b': owner_name_secondary,
                'owner_email': OWNER_EMAIL_OVERRIDES.get(owner_name_primary),
                'owner_email_b': OWNER_EMAIL_OVERRIDES.get(owner_name_secondary) if owner_name_secondary else None,
                'entitlement': float(row['UOE']),
                'bedrooms': bedrooms,
                'bathrooms': bathrooms,
                'car_spaces': car_spaces,

                # Quarterly breakdown
                'q1_total': float(row['2026 Q1 Total']),
                'q2_total': float(row['2026 Q2 Total']),
                'q3_total': float(row['2026 Q3 Total']),
                'q4_total': float(row['2026 Q4 Total']),

                # 2025 comparison data
                'admin_levy_2025': float(row['2025 Admin Levied']) if pd.notna(row['2025 Admin Levied']) else None,
                'sinking_levy_2025': float(row['2025 Sinking Levied']) if pd.notna(
                    row['2025 Sinking Levied']) else None,
                'levy_increase_2026': float(row['Increase ($)']) if pd.notna(row['Increase ($)']) else None,
                'levy_increase_percent_2026': float(row['Increase (%)']) if pd.notna(row['Increase (%)']) else None,

                # Account balances (default to 0)
                'balance_owing': 0.00,
                'balance_credit': 0.00,

                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }

            units.append(unit_doc)

        # Count unit types
        apartment_count = sum(1 for u in units if u['unit_type'] == 'apartment')
        townhouse_count = sum(1 for u in units if u['unit_type'] == 'townhouse')
        print(f"✅ Loaded {len(units)} units from Excel with 2026 levy data")
        print(
            f"   📊 {apartment_count} apartments "
            f"({format_unit_display(1, _UNIT_DISPLAY_RULES)}-"
            f"{format_unit_display(apartment_count, _UNIT_DISPLAY_RULES)}), "
            f"{townhouse_count} townhouses "
            f"({format_unit_display(71, _UNIT_DISPLAY_RULES)}-"
            f"{format_unit_display(70 + townhouse_count, _UNIT_DISPLAY_RULES)})")
        return units

    except Exception as e:
        print(f"⚠️  Error reading Excel file: {e}")
        print("   Using fallback unit data")
        return get_fallback_units_data()


def get_fallback_units_data():
    """Fallback unit data if Excel file is not available."""
    units = []

    # Create 87 generic units (70 apartments + 17 townhouses)
    apartment_counter = 1
    townhouse_counter = 1

    for i in range(1, 88):
        lot_num = f"LOT{i}"

        # Determine unit type (first 70 are apartments, rest are townhouses)
        if i <= 70:
            unit_type = 'apartment'
            unit_num = format_unit_display(apartment_counter, _UNIT_DISPLAY_RULES)
            apartment_counter += 1
            entitlement = 110.0  # Average entitlement
            admin_quarterly = 945.0
            sinking_quarterly = 275.0
        else:
            unit_type = 'townhouse'
            unit_num = format_unit_display(70 + townhouse_counter, _UNIT_DISPLAY_RULES)
            townhouse_counter += 1
            entitlement = 145.0
            admin_quarterly = 1240.0
            sinking_quarterly = 362.0

        units.append({
            'id': str(uuid.uuid4()),
            'lot_number': lot_num,
            'unit_number': unit_num,
            'unit_type': unit_type,
            'owner_name': None,
            'owner_email': None,
            'entitlement': entitlement,
            'balance_owing': 0.00,
            'balance_credit': 0.00,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        })

    return units
