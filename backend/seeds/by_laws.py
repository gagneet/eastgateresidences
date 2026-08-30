"""
By-Laws Seed Data for East Gate Residences

Creates the current by-laws document for residents to acknowledge.
"""
import uuid
from datetime import datetime, timezone


def get_by_laws_seed_data():
    """
    Returns the current by-laws document for East Gate Residences.

    This document must be acknowledged by tenants and guests during registration.
    Owners should acknowledge as part of settlement process.
    """
    return [{
        'id': str(uuid.uuid4()),
        'version': '2026-v1',
        'effective_date': '2026-01-01',
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'is_current': True,
        'content': """# East Gate Residences - Strata By-Laws

## Effective Date: January 1, 2026

### 1. General Conduct

**1.1 Noise and Disturbance**
- Residents must not create noise that disturbs other residents, particularly between 10:00 PM and 7:00 AM.
- Owners and occupiers must ensure that guests also comply with these by-laws.

**1.2 Common Property Use**
- Common property must not be used in a way that interferes with other residents' use and enjoyment.
- No storage of personal items on common property without Executive Committee approval.
- No alterations to common property without prior written approval.

**1.3 Safety and Security**
- Keep all entry doors, gates, and security systems properly secured.
- Do not prop open security doors or disable security systems.
- Report any security concerns or safety hazards to the strata manager immediately.

### 2. Parking and Vehicles

**2.1 Allocated Parking**
- Vehicles must be parked only in designated spaces allocated to your unit.
- Visitor parking is for temporary guest use only (maximum 24 hours).
- No commercial vehicles, caravans, trailers, or boats may be parked on the property without approval.

**2.2 Vehicle Maintenance**
- No vehicle repairs, servicing, or washing is permitted on common property.
- Vehicles must be maintained in a safe and roadworthy condition.
- Oil leaks and vehicle damage to common property are the owner's responsibility.

### 3. Waste Management and Recycling

**3.1 Disposal**
- Use designated waste and recycling bins correctly.
- Do not place bulk items or hazardous waste in common bins.
- Arrange council collection for large items as required.

**3.2 Bin Storage**
- Return bins to designated storage areas promptly after collection.
- Keep bin storage areas clean and tidy.

### 4. Pets

**4.1 Pet Keeping**
- Pets require written approval from the Executive Committee before being brought onto the property.
- Dogs and cats must be registered with local council.
- Pet owners are responsible for cleaning up after their pets immediately.

**4.2 Pet Conduct**
- Pets must not create noise nuisance or disturb other residents.
- Dogs must be on a leash at all times when on common property.
- Aggressive or dangerous animals are prohibited.

### 5. Appearance and Maintenance

**5.1 Unit Maintenance**
- Owners must maintain their units and ensure they do not detract from the appearance of the building.
- Balconies and courtyards must be kept neat and tidy.
- No items may be hung from balconies or windows.

**5.2 Renovations and Alterations**
- All renovations require approval from the Executive Committee before commencement.
- Noisy work may only occur Monday to Friday 7:00 AM - 6:00 PM, Saturday 8:00 AM - 5:00 PM.
- No work on Sundays or public holidays without specific approval.

**5.3 Air Conditioning and Fixtures**
- Installation of air conditioning units requires Executive Committee approval.
- External fixtures must be consistent with building appearance.
- Satellite dishes and antennas require approval before installation.

### 6. Amenities and Facilities

**6.1 Booking System**
- Common facilities (if applicable) must be booked through the strata management system.
- Users are responsible for cleaning and returning facilities to original condition.
- Cancellations must be made at least 24 hours in advance.

**6.2 Gym and Recreation Areas** (If Applicable)
- Users must be residents or approved guests.
- Clean equipment after use.
- Report any equipment damage immediately.

### 7. Smoking

**7.1 Smoking Restrictions**
- Smoking is prohibited in all internal common areas.
- Smoking on balconies must not cause smoke drift to neighboring units.
- Cigarette butts must be disposed of responsibly, not on common property.

### 8. Short-Term Letting

**8.1 Airbnb and Short-Term Rentals**
- Short-term letting (less than 90 days) requires Executive Committee approval.
- Owners must ensure guests comply with all by-laws.
- Owners are responsible for any damage or disturbances caused by short-term guests.

**8.2 Guest Registration**
- All short-term guests must be registered with the strata manager.
- Emergency contact details for the owner must be provided.

### 9. Damage and Liability

**9.1 Responsibility**
- Owners and occupiers are responsible for any damage to common property caused by themselves, family members, guests, or tradespeople.
- Repairs must be carried out promptly at the owner's expense.

**9.2 Insurance**
- Owners must maintain adequate contents and public liability insurance.
- The strata plan's insurance does not cover personal belongings or internal unit damage.

### 10. Compliance and Enforcement

**10.1 Breach of By-Laws**
- Failure to comply with by-laws may result in:
  - Formal warning letters
  - Fines as permitted by legislation
  - Legal action
  - Recovery of costs incurred by the Owners Corporation

**10.2 Dispute Resolution**
- Disputes should first be raised with the strata manager.
- Mediation services are available through the ACT Civil and Administrative Tribunal (ACAT).

### 11. Communications

**11.1 Notices and Communication**
- Official notices will be provided via email, resident portal, or mail as appropriate.
- Residents should keep contact details current with the strata manager.

**11.2 Meetings**
- Annual General Meetings (AGM) and Executive Committee meetings will be held as required by legislation.
- Meeting notices will be provided with required notice periods.

### 12. Amendments

These by-laws may be amended by special resolution at a general meeting of the Owners Corporation in accordance with the Unit Titles (Management) Act 2011 (ACT).

---

## Acknowledgment

By acknowledging these by-laws, I confirm that I have read, understood, and agree to comply with all provisions outlined in this document. I understand that failure to comply may result in penalties as permitted by legislation.

---

**Contact Information:**
- **Strata Manager:** East Gate Strata Management
- **Emergency Contact:** (see emergency services in resident portal)
- **Email:** admin@eastgateresidences.com.au
- **Phone:** (02) 6123 4567

**Last Updated:** January 1, 2026
**Version:** 2026-v1
""",
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }]


if __name__ == '__main__':
    """
    Standalone script to seed by-laws data.
    Run with: cd backend && python3 seeds/by_laws.py
    """
    import asyncio
    import sys
    from pathlib import Path

    # Add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from pymongo import AsyncMongoClient
    from dotenv import load_dotenv
    import os

    load_dotenv()


    async def seed():
        # Connect to database
        """Generated function header.

        Function: seed
        Path: backend/seeds/by_laws.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        mongo_url = os.getenv('MONGO_URL', 'mongodb://127.0.0.1:27018/')
        db_name = os.getenv('DB_NAME', 'strata_production')

        client = AsyncMongoClient(mongo_url)
        db = client[db_name]

        print("📋 Seeding by-laws...")

        # Get seed data
        by_laws_data = get_by_laws_seed_data()

        # Clear existing current by-laws
        await db.by_laws.update_many({'is_current': True}, {'$set': {'is_current': False}})

        # Insert new by-laws
        for by_law in by_laws_data:
            existing = await db.by_laws.find_one({'version': by_law['version']})
            if existing:
                print(f"  ⊘ By-laws version {by_law['version']} already exists")
            else:
                await db.by_laws.insert_one(by_law)
                print(f"  ✓ Created by-laws version {by_law['version']}")

        print("✅ By-laws seeding complete!")

        # Close connection
        client.close()


    # Run async function
    asyncio.run(seed())
