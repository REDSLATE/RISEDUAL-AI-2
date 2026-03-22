# RISEDUALAI Deployment Guide for www.risedual.com

## ✅ Pre-Deployment Checklist Complete
- [x] All deployment blockers fixed
- [x] Environment variables configured
- [x] Services running properly
- [x] Database connected
- [x] APIs integrated

## 🚀 Deployment Steps

### Step 1: Get Payment Provider API Keys

Before deploying, you need to set up your payment providers:

#### **Stripe Setup:**
1. Go to https://stripe.com and create an account
2. Navigate to Developers → API keys
3. Copy your:
   - Secret Key (sk_live_...)
   - Publishable Key (pk_live_...)
4. Create a yearly subscription product:
   - Go to Products → Create Product
   - Set price: $50/year
   - Copy the Price ID (price_...)

#### **PayPal Setup:**
1. Go to https://developer.paypal.com
2. Create an app in Dashboard
3. Copy your:
   - Client ID
   - Client Secret
4. Create a subscription plan for $50/year
5. Copy the Plan ID

### Step 2: Update Environment Variables

In Emergent, before deploying, update these environment variables:

```bash
# Replace with your actual Stripe keys
STRIPE_SECRET_KEY=sk_live_your_actual_key
STRIPE_PUBLISHABLE_KEY=pk_live_your_actual_key  
STRIPE_PRICE_ID=price_your_actual_price_id

# Replace with your actual PayPal keys
PAYPAL_CLIENT_ID=your_actual_client_id
PAYPAL_CLIENT_SECRET=your_actual_secret
PAYPAL_MODE=live
PAYPAL_PLAN_ID=P-your_actual_plan_id

# URLs are already set for www.risedual.com
STRIPE_SUCCESS_URL=https://www.risedual.com/subscription/success
STRIPE_CANCEL_URL=https://www.risedual.com/subscription/cancel
PAYPAL_RETURN_URL=https://www.risedual.com/subscription/success
PAYPAL_CANCEL_URL=https://www.risedual.com/subscription/cancel
```

### Step 3: Deploy on Emergent

1. **In Emergent Dashboard:**
   - Click "Deploy to Production"
   - Wait for deployment to complete
   - Note your production URL (e.g., risedualai.emergent.host)

2. **Test the deployment:**
   - Visit the production URL
   - Verify all features work
   - Test payment flows (use Stripe/PayPal test mode first)

### Step 4: Connect Custom Domain (www.risedual.com)

#### **In Emergent:**
1. Go to your app settings
2. Find "Custom Domain" section
3. Enter: www.risedual.com
4. Emergent will provide DNS records

#### **In GoDaddy:**
1. Log into your GoDaddy account
2. Go to: My Products → Domains → risedual.com → DNS
3. **IMPORTANT:** Delete all existing A records
4. Add the DNS records provided by Emergent:
   - Usually CNAME record pointing to Emergent
   - May include A records with IP addresses
5. Save changes

#### **Wait for Propagation:**
- DNS changes take 5-15 minutes (usually)
- Can take up to 24 hours maximum
- Check status: https://www.whatsmydns.net/#A/www.risedual.com

### Step 5: Verify Everything Works

Once DNS propagates, test:
- ✅ Visit https://www.risedual.com
- ✅ SSL certificate is active (🔒 padlock in browser)
- ✅ All pages load correctly
- ✅ Real-time data displays
- ✅ Trading functionality works
- ✅ Subscription page opens
- ✅ Payment flows work (test with small amount first)

## 📋 Post-Deployment Tasks

### 1. Set Stripe to Live Mode
- In Stripe Dashboard, toggle from Test to Live mode
- Update your env vars with live keys

### 2. Set PayPal to Live Mode
- Change PAYPAL_MODE=live in environment variables

### 3. Monitor Your App
- Check error logs regularly
- Monitor subscription payments
- Test trading integrations

### 4. Security Checklist
- ✅ HTTPS enabled (automatic with Emergent)
- ✅ Environment variables secured
- ✅ No API keys in code
- ✅ CORS properly configured

## 🆘 Troubleshooting

### Domain not working?
- Check DNS propagation: https://www.whatsmydns.net
- Verify A/CNAME records in GoDaddy
- Clear browser cache
- Try incognito/private mode

### Payments not working?
- Verify API keys are correct
- Check if in test/live mode
- Check webhook configuration in Stripe/PayPal
- Review backend logs for errors

### Features not working?
- Check environment variables are set
- Verify API keys (Alpha Vantage, Emergent LLM)
- Check backend logs: `sudo supervisorctl tail -f backend`

## 💰 Costs Overview

### Emergent Hosting
- **50 credits/month** for production deployment
- Includes: SSL, CDN, auto-scaling, monitoring

### Payment Provider Fees
- **Stripe:** 2.9% + $0.30 per transaction
- **PayPal:** ~3.5% per transaction
- Your net per subscription: ~$47-48/year

### API Costs (Already integrated)
- Alpha Vantage: Free tier (25 calls/day)
- Emergent LLM: Usage-based

## 📞 Support

Need help?
- Emergent Support: support@emergent.sh
- Community: Discord/Slack (if available)
- Documentation: https://docs.emergent.sh

---

## 🎉 You're Ready to Launch!

Your RISEDUALAI platform is production-ready with:
- ✅ Real-time trading data
- ✅ AI-powered insights
- ✅ Broker integration
- ✅ Subscription payments ($50/year)
- ✅ Professional design
- ✅ Full functionality

**Next Step:** Click "Deploy" in Emergent and follow Steps 3-4 above!
