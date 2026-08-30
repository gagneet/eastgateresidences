"""
Diagnostic script: test SMTP connection to Migadu and verify email settings.

This is a manual diagnostic tool, NOT a unit test.  It connects to the live
MongoDB database and sends a real email, so it must NEVER run in the automated
test suite.

Run manually when debugging email delivery issues:
    backend/venv/bin/python3 manual/diagnostics/smtp_connection_diagnostic.py

Override the recipient when needed:
    TEST_SMTP_RECIPIENT=someone@example.com backend/venv/bin/python3 manual/diagnostics/smtp_connection_diagnostic.py
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# bare load_dotenv() only walks up from this script's own directory
# (manual/diagnostics/), never into backend/ where .env actually lives.
load_dotenv(Path(__file__).resolve().parent.parent.parent / "backend" / ".env")

MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME')
TEST_SMTP_RECIPIENT = os.environ.get('TEST_SMTP_RECIPIENT', 'gagneet@gmail.com')


async def test_smtp_connection():
    """Test SMTP connection with current settings"""
    print("=" * 60)
    print("SMTP Connection Diagnostic Test")
    print("=" * 60)

    # Get settings from database
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    settings = await db.email_settings.find_one({"id": "main"}, {"_id": 0})

    if not settings:
        print("❌ No email settings found in database")
        print("Please configure email settings via UI first")
        client.close()
        return

    print("\n📋 Current Settings:")
    print(f"   Provider: {settings.get('provider')}")
    print(f"   SMTP Host: {settings.get('smtp_host')}")
    print(f"   SMTP Port: {settings.get('smtp_port')}")
    print(f"   Security Mode: {settings.get('smtp_security')}")
    print(f"   Username: {settings.get('smtp_user')}")
    print(
        f"   Password: {'*' * len(settings.get('smtp_password', ''))} ({len(settings.get('smtp_password', ''))} chars)")
    print(f"   Sender Email: {settings.get('sender_email')}")
    print(f"   Sender Name: {settings.get('sender_name')}")

    # Test connection
    smtp_host = settings.get('smtp_host')
    smtp_port = settings.get('smtp_port')
    smtp_security = settings.get('smtp_security')
    smtp_user = settings.get('smtp_user')
    smtp_pass = settings.get('smtp_password')

    print("\n🔍 Testing Connection...")

    try:
        # Test based on security mode
        if smtp_port == 465 or smtp_security == "ssl":
            print(f"   Using SMTP_SSL (port {smtp_port})")
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
            print("   ✅ SSL connection established")
        else:
            print(f"   Using SMTP with STARTTLS (port {smtp_port})")
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()
            print("   ✅ SMTP connection established")
            if smtp_security == "tls":
                server.starttls()
                server.ehlo()
                print("   ✅ STARTTLS upgrade successful")

        # Test authentication
        print(f"\n🔐 Testing Authentication...")
        print(f"   Username: {smtp_user}")

        try:
            server.login(smtp_user, smtp_pass)
            print("   ✅ Authentication successful!")

            # Test sending to the configured manual diagnostic recipient.
            print(f"\n📧 Testing Email Send...")
            msg = MIMEMultipart('alternative')
            msg['Subject'] = "SMTP Test - East Gate Residences"
            msg['From'] = f"{settings.get('sender_name')} <{settings.get('sender_email')}>"
            msg['To'] = TEST_SMTP_RECIPIENT

            html = """
            <div style="font-family: sans-serif; padding: 20px;">
                <h2>SMTP Connection Test Successful!</h2>
                <p>This email confirms that your SMTP configuration is working correctly.</p>
                <p><strong>Server:</strong> {host}:{port}</p>
                <p><strong>Security:</strong> {security}</p>
            </div>
            """.format(host=smtp_host, port=smtp_port, security=smtp_security.upper())

            msg.attach(MIMEText(html, 'html'))

            server.send_message(msg)
            print(f"   ✅ Test email sent to {TEST_SMTP_RECIPIENT}")
            print("\n✅ All tests passed! SMTP is configured correctly.")

        except smtplib.SMTPAuthenticationError as e:
            print(f"   ❌ Authentication failed: {e}")
            print("\n🔧 Troubleshooting:")
            print("   1. Verify username is correct email address")
            print("   2. Check password in Migadu admin panel")
            print("   3. Ensure mailbox is active in Migadu")
            print("   4. Check if account is locked due to failed attempts")

        except Exception as e:
            print(f"   ❌ Send failed: {e}")

        server.quit()

    except smtplib.SMTPConnectError as e:
        print(f"   ❌ Connection failed: {e}")
        print("\n🔧 Troubleshooting:")
        print("   1. Check SMTP host is correct (smtp.migadu.com)")
        print("   2. Verify port is correct (465 for SSL, 587 for TLS)")
        print("   3. Check firewall allows outbound connections")

    except Exception as e:
        print(f"   ❌ Error: {e}")
        print(f"   Error type: {type(e).__name__}")

    client.close()

    print("\n" + "=" * 60)


async def check_migadu_mailbox():
    """Check if mailbox exists in database"""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print("\n📬 Checking Mailbox Configuration...")

    settings = await db.email_settings.find_one({"id": "main"}, {"_id": 0})
    if not settings:
        print("❌ No email settings in database")
        client.close()
        return

    smtp_user = settings.get('smtp_user')

    # Check if user exists
    user = await db.users.find_one(
        {"mail_username": smtp_user},
        {"_id": 0, "email": 1, "full_name": 1, "mail_username": 1, "mail_password": 1}
    )

    if user:
        print(f"   ✅ User found in database:")
        print(f"      Login Email: {user.get('email')}")
        print(f"      Full Name: {user.get('full_name')}")
        print(f"      Mail Username: {user.get('mail_username')}")
        print(
            f"      Mail Password: {'*' * len(user.get('mail_password', ''))} ({len(user.get('mail_password', ''))} chars)")
    else:
        print(f"   ⚠️  No user found with mail_username: {smtp_user}")
        print("      This is OK if using a shared 'noreply' account")

    client.close()


if __name__ == "__main__":
    asyncio.run(test_smtp_connection())
    asyncio.run(check_migadu_mailbox())
