"""
Courier Tracking Service
========================
Provides parcel tracking integration for major carriers used in Australia:
  - Australia Post (AusPost Tracking API v2)
  - StarTrack (AusPost subsidiary)
  - DHL Express
  - FedEx
  - TNT Australia
  - Amazon Logistics
  - Toll Group
  - Manual / Other

In production, real API credentials must be configured via environment variables.
Until then, all carriers fall back to a *mock* implementation that returns
plausible tracking events so the feature is fully exercisable end-to-end.

Environment variables (optional – required only for live tracking):
  AUSPOST_API_KEY       – Australia Post Developer Portal API key
  DHL_API_KEY           – DHL Express API key
  FEDEX_API_KEY         – FedEx Web Services API key
  FEDEX_ACCOUNT_NUMBER  – FedEx account number
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

class TrackingEvent:
    """A single tracking status event."""

    def __init__(
            self,
            timestamp: str,
            status: str,
            location: Optional[str],
            description: str,
    ) -> None:
        """Generated function header.

        Function: TrackingEvent.__init__
        Path: backend/services/courier_tracking_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.timestamp = timestamp
        self.status = status
        self.location = location
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        """Generated function header.

        Function: TrackingEvent.to_dict
        Path: backend/services/courier_tracking_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "timestamp": self.timestamp,
            "status": self.status,
            "location": self.location,
            "description": self.description,
        }


class TrackingResult:
    """Aggregated tracking information for a shipment."""

    def __init__(
            self,
            carrier: str,
            tracking_number: str,
            current_status: str,
            estimated_delivery: Optional[str],
            events: List[TrackingEvent],
            source: str = "mock",
            tracking_url: Optional[str] = None,
    ) -> None:
        """Generated function header.

        Function: TrackingResult.__init__
        Path: backend/services/courier_tracking_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.carrier = carrier
        self.tracking_number = tracking_number
        self.current_status = current_status
        self.estimated_delivery = estimated_delivery
        self.events = events
        self.source = source  # "live" | "mock"
        self.tracking_url = tracking_url

    def to_dict(self) -> Dict[str, Any]:
        """Generated function header.

        Function: TrackingResult.to_dict
        Path: backend/services/courier_tracking_service.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return {
            "carrier": self.carrier,
            "tracking_number": self.tracking_number,
            "current_status": self.current_status,
            "estimated_delivery": self.estimated_delivery,
            "events": [e.to_dict() for e in self.events],
            "source": self.source,
            "tracking_url": self.tracking_url,
        }


# ---------------------------------------------------------------------------
# Carrier tracking-URL templates (public deep-links – no API key required)
# ---------------------------------------------------------------------------

_TRACKING_URLS: Dict[str, str] = {
    "auspost": "https://auspost.com.au/mypost/track/#/details/{tracking_number}",
    "startrack": "https://startrack.com.au/track-and-trace/?q={tracking_number}",
    "dhl": "https://www.dhl.com/au-en/home/tracking.html?tracking-id={tracking_number}",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
    "tnt": "https://www.tnt.com/express/en_au/site/tracking.html?searchType=CON&cons={tracking_number}",
    "amazon": "https://track.amazon.com.au/tracking/{tracking_number}",
    "toll": "https://www.toll.com.au/tools-and-support/track-shipment/?con={tracking_number}",
}


def get_public_tracking_url(carrier: str, tracking_number: str) -> Optional[str]:
    """Return the public carrier-website tracking URL for a given tracking number."""
    template = _TRACKING_URLS.get(carrier.lower())
    if not template or not tracking_number:
        return None
    return template.replace("{tracking_number}", tracking_number)


# ---------------------------------------------------------------------------
# Mock tracking event generators
# ---------------------------------------------------------------------------

def _mock_events(tracking_number: str, carrier: str) -> List[TrackingEvent]:
    """
    Generate a realistic sequence of mock tracking events.
    The events are deterministic based on the tracking number so the same
    parcel always shows the same status across calls.
    """
    now = datetime.now(timezone.utc)

    # Use last char of tracking number to pick a scenario
    scenario = (ord(tracking_number[-1]) % 3) if tracking_number else 0

    if scenario == 0:
        # In transit
        return [
            TrackingEvent(
                timestamp=(now - timedelta(days=2)).isoformat(),
                status="picked_up",
                location="Origin Facility, Melbourne VIC",
                description="Item collected from sender",
            ),
            TrackingEvent(
                timestamp=(now - timedelta(days=1, hours=6)).isoformat(),
                status="in_transit",
                location="Distribution Centre, Sydney NSW",
                description="Parcel arrived at distribution centre",
            ),
            TrackingEvent(
                timestamp=(now - timedelta(hours=4)).isoformat(),
                status="out_for_delivery",
                location="Delivery Depot, Canberra ACT",
                description="Item is out for delivery",
            ),
        ]
    elif scenario == 1:
        # Delivered
        return [
            TrackingEvent(
                timestamp=(now - timedelta(days=3)).isoformat(),
                status="picked_up",
                location="Origin Facility, Brisbane QLD",
                description="Item collected from sender",
            ),
            TrackingEvent(
                timestamp=(now - timedelta(days=2)).isoformat(),
                status="in_transit",
                location="Air Hub, Sydney NSW",
                description="Parcel in transit via air freight",
            ),
            TrackingEvent(
                timestamp=(now - timedelta(days=1)).isoformat(),
                status="delivered",
                location="Canberra ACT 2612",
                description="Parcel delivered to building reception",
            ),
        ]
    else:
        # Awaiting pickup / delayed
        return [
            TrackingEvent(
                timestamp=(now - timedelta(hours=18)).isoformat(),
                status="arrived_at_facility",
                location="Parcel Locker / Post Office, Braddon ACT",
                description="Parcel ready for collection",
            ),
        ]


def _mock_status(events: List[TrackingEvent]) -> str:
    """Generated function header.

    Function: _mock_status
    Path: backend/services/courier_tracking_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return events[-1].status if events else "unknown"


def _mock_estimated_delivery(events: List[TrackingEvent]) -> Optional[str]:
    """Generated function header.

    Function: _mock_estimated_delivery
    Path: backend/services/courier_tracking_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    last_status = _mock_status(events)
    if last_status == "delivered":
        return None
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Carrier-specific live API adapters (stubs – populate with real credentials)
# ---------------------------------------------------------------------------

async def _track_auspost_live(tracking_number: str) -> Optional[TrackingResult]:
    """
    Australia Post Tracking API v2
    Docs: https://developers.auspost.com.au/apis/track-and-trace/reference
    Endpoint: GET https://digitalapi.auspost.com.au/track/v2/shipments?search_reference={id}
    Header: auth-key: <AUSPOST_API_KEY>
    """
    api_key = os.environ.get("AUSPOST_API_KEY")
    if not api_key:
        return None

    try:
        import aiohttp  # type: ignore
        url = "https://digitalapi.auspost.com.au/track/v2/shipments"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    url,
                    params={"search_reference": tracking_number},
                    headers={"auth-key": api_key},
                    timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        shipments = data.get("shipments", [])
        if not shipments:
            return None
        shipment = shipments[0]
        events_raw = shipment.get("trackSummary", {}).get("events", [])
        events = [
            TrackingEvent(
                timestamp=e.get("date", "") + "T" + e.get("time", ""),
                status=e.get("eventDescription", "").lower().replace(" ", "_"),
                location=e.get("location", ""),
                description=e.get("eventDescription", ""),
            )
            for e in events_raw
        ]
        current_status = shipment.get("trackSummary", {}).get("trackingStatus", "unknown").lower()
        return TrackingResult(
            carrier="auspost",
            tracking_number=tracking_number,
            current_status=current_status,
            estimated_delivery=shipment.get("trackSummary", {}).get("estimatedDeliveryDate"),
            events=events,
            source="live",
            tracking_url=get_public_tracking_url("auspost", tracking_number),
        )
    except Exception:
        return None


async def _track_dhl_live(tracking_number: str) -> Optional[TrackingResult]:
    """
    DHL Express Tracking API
    Docs: https://developer.dhl.com/api-reference/shipment-tracking
    """
    api_key = os.environ.get("DHL_API_KEY")
    if not api_key:
        return None

    try:
        import aiohttp  # type: ignore
        url = "https://api-eu.dhl.com/track/shipments"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    url,
                    params={"trackingNumber": tracking_number},
                    headers={"DHL-API-Key": api_key},
                    timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        shipments = data.get("shipments", [])
        if not shipments:
            return None
        shipment = shipments[0]
        events = [
            TrackingEvent(
                timestamp=e.get("timestamp", ""),
                status=e.get("status", {}).get("statusCode", "unknown"),
                location=e.get("location", {}).get("address", {}).get("addressLocality", ""),
                description=e.get("status", {}).get("description", ""),
            )
            for e in shipment.get("events", [])
        ]
        return TrackingResult(
            carrier="dhl",
            tracking_number=tracking_number,
            current_status=shipment.get("status", {}).get("statusCode", "unknown"),
            estimated_delivery=shipment.get("estimatedTimeOfDelivery"),
            events=events,
            source="live",
            tracking_url=get_public_tracking_url("dhl", tracking_number),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def track_parcel(carrier: str, tracking_number: str) -> TrackingResult:
    """
    Retrieve tracking information for a parcel.

    Attempts live API lookup first (if credentials are configured).
    Falls back to a mock/simulated response so the feature is always exercisable.

    Args:
        carrier:         Carrier slug (auspost, dhl, fedex, …).
        tracking_number: Carrier-assigned tracking number.

    Returns:
        TrackingResult with events and current status.
    """
    carrier = carrier.lower()

    # Try live adapters
    live_result: Optional[TrackingResult] = None
    if carrier == "auspost":
        live_result = await _track_auspost_live(tracking_number)
    elif carrier == "dhl":
        live_result = await _track_dhl_live(tracking_number)
    # Other carriers: add live adapters here as API credentials become available

    if live_result:
        return live_result

    # Mock fallback
    events = _mock_events(tracking_number, carrier)
    tracking_url = get_public_tracking_url(carrier, tracking_number)
    carrier_labels = {
        "auspost": "Australia Post",
        "startrack": "StarTrack",
        "dhl": "DHL Express",
        "fedex": "FedEx",
        "tnt": "TNT",
        "amazon": "Amazon Logistics",
        "toll": "Toll Group",
    }
    return TrackingResult(
        carrier=carrier_labels.get(carrier, carrier.title()),
        tracking_number=tracking_number,
        current_status=_mock_status(events),
        estimated_delivery=_mock_estimated_delivery(events),
        events=events,
        source="mock",
        tracking_url=tracking_url,
    )
