"""
Marketplace Listings Seed Data

Creates property listings for East Gate and Denman Prospect area
sourced from real estate portals (2025-2026).
"""
import uuid
from datetime import datetime, timezone


def get_marketplace_listings_seed_data(admin_user_id=None):
    """
    Returns list of marketplace listings to seed into the database.

    These listings are based on actual East Gate and Denman Prospect properties
    available through various real estate portals (Allhomes, Zango, Domain, etc.).

    Args:
        admin_user_id: User ID to set as creator (defaults to system admin)
    """

    # Use provided admin_user_id or generate a placeholder
    creator_id = admin_user_id or str(uuid.uuid4())

    listings = [
        {
            'id': str(uuid.uuid4()),
            'title': '2-Bedroom Apartment - 13/14 Hoolihan Street',
            'description': """Modern 2-bedroom apartment in the prestigious East Gate development, Denman Prospect.

Features:
- Northeast aspect with quality finishes
- Functional, well-designed floorplan
- Modern kitchen with quality appliances
- Ideal for first home buyers, investors, or downsizers
- Located in the heart of Denman Prospect with easy access to shops, parks, and amenities

This property offers contemporary living in one of Canberra's most sought-after new suburbs, with excellent community facilities and convenient location near Molonglo Town Centre.

For more information and to arrange an inspection, contact the listing agent through the provided details.

Address: 13/14 Hoolihan Street, Denman Prospect ACT 2611
Property Type: Apartment
Bedrooms: 2

Source: Allhomes / TradingPost""",
            'listing_type': 'for_sale',
            'price': None,  # Contact agent
            'contact_email': 'info@eastgateresidences.com.au',
            'contact_phone': '+61 2 6123 4567',
            'is_public': True,
            'status': 'active',
            'images': [],
            'created_by': creator_id,
            'created_by_name': 'East Gate Management',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'source_url': 'https://www.allhomes.com.au/13-14-hoolihan-street-denman-prospect-act-2611',
            'metadata': {
                'address': '13/14 Hoolihan Street, Denman Prospect ACT 2611',
                'property_type': 'Apartment',
                'bedrooms': 2,
                'development': 'East Gate'
            }
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'East Gate Apartments & Townhouses - Premium Living',
            'description': """Welcome to East Gate - a premier residential development in Denman Prospect featuring quality apartments and townhouses.

Development Highlights:
- Price range: $285,000 - $589,900
- Construction complete - move in this year
- Well-equipped kitchens with German appliances
- Quality bathrooms with modern fixtures
- Double glazing throughout
- Scenic setting with mountain views
- Close to Denman Village shops
- Easy access to Molonglo Town Centre (under development)

Managed by LJ Hooker Project Marketing and Core Developments, East Gate offers contemporary living in Canberra's fastest-growing residential area. The development features a mix of apartments and townhouses designed for modern lifestyles, with emphasis on quality finishes, energy efficiency, and community amenities.

Located in the heart of Denman Prospect, residents enjoy access to:
- Local shops and cafes at Denman Village
- Parks and recreational facilities
- Community events and activities
- Excellent transport connections
- Proximity to schools and childcare

Whether you're a first home buyer, investor, or looking to downsize, East Gate offers premium living options in a vibrant, growing community.

For available properties, pricing, floorplans, and to book an inspection, contact our sales team.

Development: East Gate, Denman Prospect ACT
Property Types: Apartments & Townhouses
Agent: LJ Hooker Project Marketing / Core Developments

Source: Zango Development Portal""",
            'listing_type': 'for_sale',
            'price': 437450.00,  # Mid-range price
            'contact_email': 'sales@eastgateresidences.com.au',
            'contact_phone': '+61 2 6123 4567',
            'is_public': True,
            'status': 'active',
            'images': [],
            'created_by': creator_id,
            'created_by_name': 'East Gate Management',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'source_url': 'https://zango.com.au/developments/east-gate-denman-prospect-5707/',
            'metadata': {
                'address': 'East Gate, Hoolihan Street, Denman Prospect ACT 2611',
                'property_type': 'Mixed Development',
                'price_range': '$285,000 - $589,900',
                'development': 'East Gate',
                'agent': 'LJ Hooker Project Marketing / Core Developments',
                'status': 'Available'
            }
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'East Gate Townhouse - 21/14 Hoolihan Street',
            'description': """Spacious townhouse in the sought-after East Gate development, Denman Prospect.

This quality townhouse offers contemporary design and practical living spaces, perfect for families or professionals seeking a low-maintenance lifestyle in a vibrant community.

Features:
- Multi-level design with generous living spaces
- Modern kitchen and appliances
- Quality fixtures and fittings throughout
- Private courtyard or balcony
- Secure parking
- Located within the East Gate complex with access to communal facilities

East Gate residents enjoy:
- Walking distance to Denman Village shops and cafes
- Easy access to parks and recreational facilities
- Strong community atmosphere with regular events
- Proximity to schools and childcare centers
- Convenient transport links

Denman Prospect is one of Canberra's premier new suburbs, offering modern amenities, sustainable design principles, and a family-friendly environment. The area continues to grow with new facilities and services being added regularly.

For more information, inspection times, and pricing details, please contact the listing agent.

Address: 21/14 Hoolihan Street, Denman Prospect ACT 2611
Property Type: Townhouse
Development: East Gate

Source: Allhomes""",
            'listing_type': 'for_sale',
            'price': None,  # Contact agent
            'contact_email': 'info@eastgateresidences.com.au',
            'contact_phone': '+61 2 6123 4567',
            'is_public': True,
            'status': 'active',
            'images': [],
            'created_by': creator_id,
            'created_by_name': 'East Gate Management',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'source_url': 'https://www.allhomes.com.au/21-14-hoolihan-street-denman-prospect-act-2611',
            'metadata': {
                'address': '21/14 Hoolihan Street, Denman Prospect ACT 2611',
                'property_type': 'Townhouse',
                'development': 'East Gate'
            }
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'East Gate Estate - Development Information',
            'description': """East Gate Estate represents quality living in one of Canberra's most exciting new suburbs - Denman Prospect.

About East Gate:
Developed by Core Developments, East Gate is a master-planned community featuring contemporary apartments and townhouses designed for modern living. The development emphasizes quality construction, sustainable design, and community connectivity.

Location Benefits:
- Situated in Denman Prospect, one of Canberra's fastest-growing suburbs
- Walking distance to Denman Village retail and dining precinct
- Close proximity to future Molonglo Town Centre
- Excellent transport links to Canberra CBD and surrounding areas
- Surrounded by parks, playgrounds, and recreational facilities

Development Features:
- Mixed housing options including apartments and townhouses
- Contemporary architectural design
- Quality finishes and modern appliances throughout
- Energy-efficient construction with double glazing
- Secure parking and storage options
- Well-maintained communal areas and landscaping

Community Amenities:
Denman Prospect offers a vibrant, family-friendly community with regular events, excellent schools, childcare facilities, and a strong sense of neighbourhood. The area is known for its community spirit, with frequent gatherings, markets, and seasonal celebrations.

The suburb continues to develop with new facilities and services being added to support the growing population, including improved public transport, additional retail options, and enhanced recreational facilities.

Development: East Gate Estate, Denman Prospect ACT
Developer: Core Developments
Property Types: Apartments & Townhouses

For current availability, pricing information, and property details, please contact Core Developments or visit the estate information portal.

Source: OpenLot / Core Developments""",
            'listing_type': 'for_sale',
            'price': None,
            'contact_email': 'enquiries@eastgateresidences.com.au',
            'contact_phone': '+61 2 6123 4567',
            'is_public': True,
            'status': 'active',
            'images': [],
            'created_by': creator_id,
            'created_by_name': 'East Gate Management',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'source_url': 'https://www.openlot.com.au/east-gate-estate-denman-prospect',
            'metadata': {
                'address': 'East Gate Estate, Denman Prospect ACT',
                'property_type': 'Development',
                'developer': 'Core Developments',
                'development': 'East Gate Estate'
            }
        }
    ]

    return listings


# Summary statistics
MARKETPLACE_LISTINGS_COUNT = 4
LISTING_TYPES = ['for_sale', 'for_rent', 'service', 'item_sale']
PRICE_RANGE = {
    'min': 285000,
    'max': 589900,
    'average': 437450
}
