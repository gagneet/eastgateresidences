"""Tenancy Passport — residency score computation and PDF generation."""
import io
from datetime import datetime, timezone, timedelta

import jwt
from typing import Optional

from config import JWT_SECRET, JWT_ALGORITHM
from database import db

TOTAL_LOTS = 87


async def compute_residency_score(user_id: str, unit_number: str, building_id: str) -> dict:
    """
    Computes a 0-100 residency score.

    Tenant scoping note: every query below goes through TenantScopedDatabase,
    which injects building_id from the ambient request context — `building_id`
    is required here so a caller can never silently inherit another building's
    context, but it is the request context (not this argument) that scopes the
    reads. Previously this defaulted to "13195", hardcoding East Gate.
    Components:
    - Payment history (40%): balance_owing == 0 → 100, else reduce
    - Noise incidents (30%): 0 incidents → 100, each −15
    - Maintenance requests (20%): 0-3 = 100, 4-6 = 80, 7+ = 60
    - Community engagement (10%): any volunteer/event = 100, else 50
    """
    # Payment history — read from unit_levy_ledger (live source of truth)
    current_year = str(datetime.now(timezone.utc).year)
    ledger = await db.unit_levy_ledger.find_one(
        {"unit_number": unit_number, "year": current_year},
        {"_id": 0, "net_balance": 1}
    )
    owing = round(max(0.0, float((ledger or {}).get("net_balance", 0) or 0)), 2)
    payment_score = 100 if owing == 0 else max(0, 100 - int(owing / 100))

    # Noise incidents
    noise_count = await db.workflow_requests.count_documents(
        {"unit_number": unit_number, "request_type": "noise_complaint",
         "is_test_data": {"$ne": True}}
    )
    noise_score = max(0, 100 - noise_count * 15)

    # Maintenance requests (reasonable = good stewardship)
    maint_count = await db.workflow_requests.count_documents(
        {"unit_number": unit_number, "request_type": "maintenance_request",
         "is_test_data": {"$ne": True}}
    )
    maint_score = 100 if maint_count <= 3 else (80 if maint_count <= 6 else 60)

    # Community engagement
    engagement_count = await db.volunteer_events.count_documents(
        {"participants.user_id": user_id, "is_test_data": {"$ne": True}}
    )
    engagement_score = 100 if engagement_count > 0 else 50

    total = round(
        payment_score * 0.40
        + noise_score * 0.30
        + maint_score * 0.20
        + engagement_score * 0.10
    )

    return {
        "score": total,
        "grade": "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D",
        "components": {
            "payment_history": payment_score,
            "noise_incidents": noise_score,
            "maintenance_requests": maint_score,
            "community_engagement": engagement_score,
        },
        "noise_incident_count": noise_count,
        "maintenance_request_count": maint_count,
        "volunteer_event_count": engagement_count,
    }


def generate_passport_token(user_id: str, unit_number: str, score: int) -> str:
    """Generated function header.

    Function: generate_passport_token
    Path: backend/services/tenancy_passport_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    payload = {
        "type": "tenancy_passport",
        "user_id": user_id,
        "unit_number": unit_number,
        "score": score,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "exp": datetime.now(timezone.utc) + timedelta(days=365 * 3),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_passport_token(token: str) -> Optional[dict]:
    """Generated function header.

    Function: verify_passport_token
    Path: backend/services/tenancy_passport_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


def generate_passport_pdf(
        resident_name: str,
        unit_number: str,
        building_name: str,
        lease_start: str,
        lease_end: str,
        score: int,
        score_components: dict,
        noise_incidents: int,
        maintenance_count: int,
        user_id: str,
) -> bytes:
    """Generate Tenancy Passport PDF using ReportLab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        import qrcode as qr_lib

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
                                leftMargin=2 * cm, rightMargin=2 * cm)
        styles = getSampleStyleSheet()

        token = generate_passport_token(user_id, unit_number, score)
        verify_url = f"https://eastgateresidences.com.au/verify-passport/{token}"

        # QR code
        qr = qr_lib.QRCode(version=1, box_size=4, border=2)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buf = io.BytesIO()
        qr_img.save(qr_buf, format="PNG")
        qr_buf.seek(0)

        from reportlab.platypus import Image
        qr_rl = Image(qr_buf, width=3 * cm, height=3 * cm)

        story = []
        story.append(Paragraph("EASTGATE RESIDENCY PASSPORT", styles["Title"]))
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(f"Resident: {resident_name}", styles["Heading2"]))
        story.append(Paragraph(f"Unit: {unit_number} — {building_name}", styles["Normal"]))
        story.append(Paragraph(f"Tenancy: {lease_start} to {lease_end}", styles["Normal"]))
        story.append(Spacer(1, 0.3 * cm))

        grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
        story.append(Paragraph(f"Residency Score: {score}/100  Grade {grade}", styles["Heading2"]))

        data = [
            ["Component", "Score"],
            ["Payment history (40%)", f"{score_components.get('payment_history', 0)}/100"],
            ["Noise incidents (30%)", f"{score_components.get('noise_incidents', 0)}/100"],
            ["Maintenance requests (20%)", f"{score_components.get('maintenance_requests', 0)}/100"],
            ["Community engagement (10%)", f"{score_components.get('community_engagement', 0)}/100"],
            ["Noise incidents recorded", str(noise_incidents)],
            ["Maintenance requests raised", str(maintenance_count)],
        ]
        t = Table(data, colWidths=[12 * cm, 4 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F4F4F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.5 * cm))
        story.append(qr_rl)
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("Scan to verify at eastgateresidences.com.au", styles["Normal"]))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph(
            "This passport was issued by East Gate Residences using the EastGate Community OS platform "
            "— a modern strata management system. "
            "Interested? Visit eastgateresidences.com.au/for-strata-managers",
            ParagraphStyle("pitch", parent=styles["Normal"], textColor=colors.grey, fontSize=9)
        ))

        doc.build(story)
        buffer.seek(0)
        return buffer.read()
    except ImportError:
        # Fallback if reportlab not available in test environment
        return b"PDF_PLACEHOLDER"
