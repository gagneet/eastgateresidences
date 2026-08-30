"""
Shared East Gate owner-balance portal snapshot helpers.

The Strata Web portal presents outstanding amounts as positive dollar values, with
"(CR)" suffixes marking credits. These helpers normalize that raw display into
the signed balance convention used across the codebase:

    positive -> arrears / amount owing
    negative -> credit
    zero     -> clear

Portal table columns (6):
  Lot | Unit | Owner | UOE | Committee | Outstanding
The Committee column is a checkbox flag and is excluded from this snapshot.
"""

from __future__ import annotations

from typing import Iterable
from utils.unit_number import format_unit_display

# Snapshot date: 2026-04-16
PORTAL_ARREARS_TOTAL_2026 = 22_234.80
PORTAL_CREDIT_TOTAL_2026 = 7_124.59

# Raw portal snapshot supplied by the user on 2026-04-16.
# Tuple format: (Lot, Unit, Owner, UOE, Outstanding)
# Committee column excluded — it is a checkbox flag and not used in calculations.
# Outstanding sign convention: positive = arrears, "(CR)" suffix = credit.
PORTAL_OWNER_BALANCE_ROWS_2026 = [
    (1, 1, "Ms Jane Pearce & Mr Yushan Han", 82, "$1,056.77"),
    (2, 2, "Patina lnthavong", 139, "$0.00"),
    (3, 3, "Giovanna Giaccio", 139, "$1,020.26 (CR)"),
    (4, 4, "Marion Turley", 82, "$0.00"),
    (5, 5, "Kimberley Ruth Swords", 111, "$289.55 (CR)"),
    (6, 6, "Ms Rachel Clarke", 82, "$0.00"),
    (7, 7, "Ms Molly Hulands", 82, "$0.00"),
    (8, 8, "Ms Charmi Dhanesha", 111, "$1,222.04"),
    (9, 9, "Ms Olivia Fairweather", 82, "$662.77"),
    (10, 10, "Mr Jason Carter", 139, "$0.00"),
    (11, 11, "Ms Stephanie Traycevska", 139, "$0.00"),
    (12, 12, "Mary Rose Young", 82, "$0.00"),
    (13, 13, "Mr Santpal Vekariya & Ms Kinjalben Vekariya", 111, "$1,222.04"),
    (14, 14, "Tess McLaughlin", 82, "$0.00"),
    (15, 15, "Sok-Joan Yoon & Yoonsuh Heo", 82, "$0.00"),
    (16, 16, "Mr Daniel Smart", 111, "$1,222.04"),
    (17, 17, "Ryan Matthew Cuzner", 82, "$0.00"),
    (18, 18, "Michael Kerrin Hopkins & Melissa Lorraine Hopkins", 139, "$0.00"),
    (19, 19, "Mr Niran Poglobe Karaeni", 139, "$0.00"),
    (20, 20, "Maka-Moi Jimi Pickette", 82, "$1,054.77"),
    (21, 21, "Georgina Kate Mitchell", 111, "$1,102.99 (CR)"),
    (22, 22, "Ms Isabelle Baldwin", 82, "$0.00"),
    (23, 23, "Lachlan Graem Collison", 82, "$0.00"),
    (24, 24, "Amishaben Patel", 111, "$0.00"),
    (25, 25, "Mr Mark Jewell", 82, "$677.77"),
    (26, 26, "Mr Peter Hanks & Ms Fiona Hanks", 139, "$1,528.41"),
    (27, 27, "Ms Arja Torpstrom", 139, "$0.00"),
    (28, 28, "Mr Paul Stuart M Stuart", 82, "$908.21"),
    (29, 29, "Ms Emma Watt", 111, "$0.00"),
    (30, 30, "Blake Hayward", 82, "$915.59"),
    (31, 31, "Dylan Martin Ashfield & Brooke Louise Green", 149, "$1,640.40"),
    (32, 32, "Ms Susan Roche", 82, "$63.35 (CR)"),
    (33, 33, "Anna Dziedzic", 111, "$0.00"),
    (34, 34, "Yanyan Pan & Yingyi Zheng", 82, "$915.59"),
    (35, 35, "Dylan Mousset", 82, "$0.00"),
    (36, 36, "Ms Shivani Dhanesha", 111, "$0.00"),
    (37, 37, "Mr Khyle Lambert", 82, "$0.00"),
    (38, 38, "Mr Kikham Sikoulabot", 139, "$0.66 (CR)"),
    (39, 39, "Keith Edward Dears", 139, "$0.00"),
    (40, 40, "Ms Natasha Andrus", 82, "$0.00"),
    (41, 41, "Mr Mehulkumar Dhanesha", 111, "$0.00"),
    (42, 42, "Ms Sarah Marrapodi", 82, "$949.37"),
    (43, 43, "Larissa Bianca Melnyk", 82, "$0.00"),
    (44, 44, "Lamisa Ahmad", 111, "$0.00"),
    (45, 45, "Mr Joseph Vega & Ms Maria Vega Guillen", 82, "$0.00"),
    (46, 46, "Marcelo Ramos da Silva & Graciela Pezaroylo Topal", 139, "$0.00"),
    (47, 47, "Brenda Thompson", 139, "$0.00"),
    (48, 48, "Kwong Hang Wong", 82, "$0.00"),
    (49, 49, "Ms Hannah Webb", 111, "$0.00"),
    (50, 50, "Mr Joshua Solano & Ms Sarah Hatherly", 82, "$0.00"),
    (51, 51, "Ms Joan Daupan", 82, "$0.00"),
    (52, 52, "Christen Luke Whisson", 111, "$0.00"),
    (53, 53, "Rachela Micheal Molong Amanamoi", 82, "$902.77"),
    (54, 54, "Mr Tin Leung & Ms Jennifer Leung", 139, "$0.00"),
    (55, 55, "Joshua & Zara Apps", 139, "$0.00"),
    (56, 56, "Isabella Heggie", 82, "$0.00"),
    (57, 57, "Dige Wang", 111, "$0.04 (CR)"),
    (58, 58, "Nicholas John Gandy", 82, "$902.78"),
    (59, 59, "Mr Danny Chau", 82, "$0.00"),
    (60, 60, "Ghassan Hourany", 111, "$0.00"),
    (61, 61, "Ms Morag Bullard", 82, "$0.00"),
    (62, 62, "Megan Joan Reeve", 139, "$0.00"),
    (63, 63, "Anthony McDonald & Rose Marimon", 139, "$3,060.60 (CR)"),
    (64, 64, "Xinyao Feng & Zhaocong Chen", 82, "$0.00"),
    (65, 65, "Sharyn Leanne Girvan & Andrew David Brooks", 111, "$0.00"),
    (66, 66, "Jade Nicole Hall", 82, "$0.00"),
    (67, 67, "Ms Sheridan Carswell", 149, "$1,651.35"),
    (68, 68, "Jonathan Stanley", 82, "$0.00"),
    (69, 69, "Geoffery & Rebecca Osborne", 111, "$0.00"),
    (70, 70, "Mr Lloyd Taylor", 82, "$1,128.92"),
    (71, 71, "Lorna Stansfield", 160, "$1.21 (CR)"),
    (72, 72, "Jake Paul Hope & Tess Tiatia", 160, "$0.00"),
    (73, 73, "Kaushal Shah & Radhika Shah", 160, "$0.00"),
    (74, 74, "Hamish Angus", 160, "$1,814.27"),
    (75, 75, "Aditya Reddy Mekala & Saideepika Mekala", 160, "$0.00"),
    (76, 76, "Tara G Munro & Shane C Smith", 160, "$1,558.44 (CR)"),
    (77, 77, "Talor Stewart Lawton & Rebecca Megan Lawton", 160, "$1,858.90"),
    (78, 78, "Olivia Rollings & Mark Raets", 160, "$0.00"),
    (79, 79, "Dr Gunjan Pandey & Dr Rinku Pandey", 160, "$0.00"),
    (80, 80, "Krystal Smith & Paul Wywsik", 160, "$0.04"),
    (81, 81, "Brian Cadungog & Joveryl Cadungog", 160, "$0.00"),
    (82, 82, "Zachary Luke James & Aurora Frances Nissen", 160, "$0.00"),
    (83, 83, "Adam Wayne Howson & Joshua Alfred Lawrence Sutton", 160, "$0.00"),
    (84, 84, "Shouvik Aditya & Cassandra Jane McGufficke", 160, "$0.00"),
    (85, 85, "Jinal Achal & Dave Achal Dave", 160, "$0.00"),
    (86, 86, "Riyu Kurian Abraham & Reshma Shaji", 160, "$0.00"),
    (87, 87, "Avneet Rooprai & Gagneet Singh", 161, "$27.49 (CR)"),
]


def parse_portal_outstanding(raw_value: str) -> float:
    """Generated function header.

    Function: parse_portal_outstanding
    Path: backend/utils/portal_owner_balances.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = str(raw_value).strip()
    is_credit = raw.upper().endswith("(CR)")
    normalized = raw.replace("(CR)", "").replace("(cr)", "").strip()
    amount = float(normalized.replace("$", "").replace(",", ""))
    return -amount if is_credit else amount


def classify_portal_balance(balance: float) -> str:
    """Generated function header.

    Function: classify_portal_balance
    Path: backend/utils/portal_owner_balances.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if balance > 0:
        return "ARREARS"
    if balance < 0:
        return "CREDIT"
    return "CLEAR"


def build_owner_balance_records(
        rows: Iterable[tuple[int, int, str, int, str]] = PORTAL_OWNER_BALANCE_ROWS_2026,
        unit_display_rules: list[dict] | None = None,
) -> list[dict]:
    """Generated function header.

    Function: build_owner_balance_records
    Path: backend/utils/portal_owner_balances.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    records = []
    for lot, unit, owner, uoe, outstanding in rows:
        balance = round(parse_portal_outstanding(outstanding), 2)
        # Per-building rules, never a hardcoded prefix — see utils/unit_number.py.
        # With no rules supplied this yields the bare lot number, which is honest
        # ("no prefix configured") rather than silently applying another building's.
        unit_number = format_unit_display(unit, unit_display_rules)
        records.append({
            "lot": lot,
            "unit": unit,
            "unit_number": unit_number,
            "owner": owner,
            "uoe": uoe,
            "balance": balance,
            "status": classify_portal_balance(balance),
            "outstanding_raw": outstanding,
        })
    return records


def summarize_owner_balances(records: Iterable[dict]) -> dict:
    """Generated function header.

    Function: summarize_owner_balances
    Path: backend/utils/portal_owner_balances.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    arrears_total = 0.0
    credit_total = 0.0
    arrears_count = 0
    credit_count = 0
    clear_count = 0

    for record in records:
        balance = round(float(record["balance"]), 2)
        if balance > 0:
            arrears_total += balance
            arrears_count += 1
        elif balance < 0:
            credit_total += abs(balance)
            credit_count += 1
        else:
            clear_count += 1

    return {
        "arrears_total": round(arrears_total, 2),
        "credit_total": round(credit_total, 2),
        "net_total": round(arrears_total - credit_total, 2),
        "arrears_count": arrears_count,
        "credit_count": credit_count,
        "clear_count": clear_count,
    }
