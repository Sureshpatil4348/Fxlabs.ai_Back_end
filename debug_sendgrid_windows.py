#!/usr/bin/env python3
"""
Debug SendGrid on Windows
Simple test to debug SendGrid issues on Windows
"""

import sys
import os

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

def test_sendgrid_direct():
    """Test SendGrid directly"""
    print("🧪 Testing SendGrid Directly")
    print("=" * 40)
    
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
        print("✅ SendGrid imports successful")
        
        # Use the API key from email_service.py
        api_key = "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"
        test_email = "theashish.y@gmail.com"
        
        print(f"📧 Test Email: {test_email}")
        print(f"🔑 API Key: {api_key[:10]}...{api_key[-10:]}")
        
        # Initialize SendGrid
        sg = SendGridAPIClient(api_key=api_key)
        print("✅ SendGrid client created")
        
        # Create simple email
        from_email = Email("alerts@fxlabs.ai", "FX Labs")
        to_email = To(test_email)
        subject = "Windows SendGrid Debug Test"
        content = Content("text/plain", "This is a debug test from Windows to verify SendGrid is working.")
        
        mail = Mail(from_email, to_email, subject, content)
        print("✅ Email object created")
        
        # Send email
        print("📤 Sending test email...")
        response = sg.send(mail)
        
        print(f"📊 Status Code: {response.status_code}")
        print(f"📊 Response Headers: {response.headers}")
        
        if response.status_code in [200, 201, 202]:
            print("✅ SendGrid direct test PASSED!")
            return True
        else:
            print("❌ SendGrid direct test FAILED!")
            print(f"❌ Status: {response.status_code}")
            print(f"❌ Body: {response.body}")
            return False
            
    except Exception as e:
        print(f"❌ SendGrid direct test FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_email_service():
    """Test email service"""
    print("\n🧪 Testing Email Service")
    print("=" * 40)
    
    try:
        from app.email_service import EmailService
        print("✅ EmailService imported successfully")
        
        email_service = EmailService()
        print("✅ EmailService initialized")
        
        if not email_service.sg:
            print("❌ SendGrid not configured in EmailService")
            return False
        
        print("✅ SendGrid configured in EmailService")
        
        # Test simple email
        test_data = [{
            "symbol": "MBTUSDm",
            "rsi_value": 55.51,
            "trigger_condition": "neutral",
            "current_price": 0.11588107
        }]
        
        test_config = {
            "rsi_period": 14,
            "overbought_threshold": 70,
            "oversold_threshold": 30,
            "alert_conditions": ["overbought", "oversold"]
        }
        
        print("📤 Testing email service send_rsi_alert...")
        
        import asyncio
        success = asyncio.run(email_service.send_rsi_alert(
            user_email="theashish.y@gmail.com",
            alert_name="Debug Test Alert",
            triggered_pairs=test_data,
            alert_config=test_config
        ))
        
        if success:
            print("✅ Email service test PASSED!")
            return True
        else:
            print("❌ Email service test FAILED!")
            return False
            
    except Exception as e:
        print(f"❌ Email service test FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main debug function"""
    print("🔍 SendGrid Windows Debug Test")
    print("=" * 50)
    print()
    
    # Test 1: Direct SendGrid
    direct_success = test_sendgrid_direct()
    
    # Test 2: Email Service
    service_success = test_email_service()
    
    print("\n📊 Debug Results:")
    print("=" * 20)
    print(f"Direct SendGrid: {'✅ PASSED' if direct_success else '❌ FAILED'}")
    print(f"Email Service: {'✅ PASSED' if service_success else '❌ FAILED'}")
    
    if direct_success and service_success:
        print("\n🎉 All tests passed! SendGrid is working correctly.")
    elif direct_success and not service_success:
        print("\n⚠️ SendGrid works directly but EmailService has issues.")
    else:
        print("\n💥 SendGrid is not working on Windows.")

if __name__ == "__main__":
    main()
