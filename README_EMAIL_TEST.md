# Email Service Test Suite

This test suite allows you to test the email service functionality without requiring MT5 or live trading data, making it perfect for macOS development.

## Features Tested

✅ **Email Service Initialization**
- SendGrid configuration check
- Environment variable validation
- Service parameters verification

✅ **Value Extraction Logic**
- Heatmap alert value extraction
- RSI alert value extraction (both `rsi` and `rsi_value` keys)
- RSI correlation alert value extraction
- Safe float conversion with error handling

✅ **Cooldown Mechanism**
- Alert hash generation
- Time-based cooldown logic
- Value-based similarity checking
- Smart retry logic for similar vs different values

✅ **Alert Hash Generation**
- Heatmap alert hashing
- RSI alert hashing
- RSI correlation alert hashing
- Unique identifier generation

✅ **Safe Float Conversion**
- Valid value conversion
- Invalid value handling
- Error logging and fallbacks

✅ **RSI Value Extraction**
- Support for both `rsi` and `rsi_value` key formats
- Priority handling (prefers `rsi` over `rsi_value`)
- Missing key handling

## How to Run

```bash
# From the Fxlabs.ai_Back_end directory
python3 test_email_service.py
```

## Test Output

The test suite provides comprehensive output showing:
- Service initialization status
- Value extraction results
- Cooldown logic behavior
- Hash generation examples
- Float conversion results
- RSI value extraction tests

## Mock Data

The test includes realistic mock data for:
- **Heatmap Alerts**: EURUSD, GBPUSD, USDJPY with strength values and indicators
- **RSI Alerts**: Both `rsi` and `rsi_value` key formats
- **RSI Correlation Alerts**: Multiple currency pairs with correlation data

## Environment Setup

To test actual email sending (optional):
1. Set the SendGrid API key environment variable:
   ```bash
   export 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="your_api_key_here"
   ```

2. Change the `test_email` variable in the script to your email address

## Benefits

- **No MT5 Dependency**: Works on macOS without MetaTrader 5
- **Comprehensive Testing**: Covers all major email service functionality
- **Real-world Scenarios**: Tests both old and new API response formats
- **Error Handling**: Validates robust error handling and fallbacks
- **Development Friendly**: Easy to run and understand results

## Test Results

The test suite validates that:
- Email service initializes correctly
- Value extraction works with both key formats (`rsi` and `rsi_value`)
- Cooldown mechanism prevents spam while allowing significant changes
- Hash generation creates unique identifiers
- Safe float conversion handles edge cases
- RSI value extraction supports multiple key formats

This ensures the email service is robust and ready for production use, even without live trading data.




🧪 FX Labs Email Service Test
Testing real email sending to Ashish

🚀 Starting Email Test...
========================================
✅ SendGrid configured successfully
📧 From: civawoc344@camjoint.com
👤 From Name: FX Labs Alerts
📬 Sending test email to: ashish.y8750@gmail.com

✅ Test email sent successfully!
📧 Check your inbox: ashish.y8750@gmail.com
📱 Check spam folder if not in inbox

🎉 Email service is working perfectly!
🎯 Test completed successfully!
📧 You should receive the test email shortly


API Key: SG.vFohAdf...
From Email: civawoc344@camjoint.com
SendGrid Client: True
Response Status: 202
Response Headers: {'Server': 'nginx', 'Date': 'Sat, 20 Sep 2025 05:21:22 GMT', 'Content-Length': '0', 'Connection': 'close', 'X-Message-Id': 'Tv4CZJqxTCqhtu0zPPbwqg', 'Access-Control-Allow-Origin': 'https://sendgrid.api-docs.io', 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Authorization, Content-Type, On-behalf-of, x-sg-elas-acl', 'Access-Control-Max-Age': '600', 'X-No-CORS-Reason': 'https://sendgrid.com/docs/Classroom/Basics/API/cors.html', 'Strict-Transport-Security': 'max-age=31536000; includeSubDomains', 'Content-Security-Policy': "frame-ancestors 'none'", 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'no-sniff', 'Referrer-Policy': 'strict-origin-when-cross-origin'}                                        
Response Body: b''
