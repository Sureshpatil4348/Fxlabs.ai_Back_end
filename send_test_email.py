#!/usr/bin/env python3
"""
Send Test Email Script
Sends a test email to verify SendGrid is working properly
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.email_service import EmailService

async def send_test_email():
    """Send a test email to Ashish"""
    
    print("🚀 Starting Email Test...")
    print("=" * 40)
    
    # Initialize email service
    email_service = EmailService()
    
    # Check if SendGrid is configured
    if email_service.sg is None:
        print("❌ SendGrid not configured!")
        print("💡 Please replace the hardcoded API key in app/email_service.py with your actual SendGrid API key")
        return False
    
    print("✅ SendGrid configured successfully")
    print(f"📧 From: {email_service.from_email}")
    print(f"👤 From Name: {email_service.from_name}")
    
    # Test email address
    test_email = "ashish.y8750@gmail.com"
    print(f"📬 Sending test email to: {test_email}")
    print()
    
    try:
        # Send test email
        print("🔄 Sending test email...")
        success = await email_service.send_test_email(test_email)
        print(f"📊 Email send result: {success}")
        
        if success:
            print("✅ Test email sent successfully!")
            print(f"📧 Check your inbox: {test_email}")
            print("📱 Check spam folder if not in inbox")
            print("⏰ Email delivery may take 1-5 minutes")
            print()
            print("🎉 Email service is working perfectly!")
            return True
        else:
            print("❌ Failed to send test email")
            print("🔍 Check the logs above for error details")
            return False
            
    except Exception as e:
        print(f"❌ Error sending test email: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Main function"""
    print("🧪 FX Labs Email Service Test")
    print("Testing real email sending to Ashish")
    print()
    
    # Check if running from correct directory
    if not os.path.exists("app/email_service.py"):
        print("❌ Error: Cannot find app/email_service.py")
        print("💡 Make sure to run this script from the Fxlabs.ai_Back_end directory")
        sys.exit(1)
    
    success = await send_test_email()
    
    if success:
        print("🎯 Test completed successfully!")
        print("📧 You should receive the test email shortly")
    else:
        print("💥 Test failed - check configuration and try again")

if __name__ == "__main__":
    asyncio.run(main())
