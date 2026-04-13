import React, { useState } from 'react';
import { X, FileText, Shield, AlertTriangle, Scale } from 'lucide-react';

const ENTITY = 'RISEDUAL CORPORATION';
const STATE = 'Florida';
const SITE = 'risedual.ai';
const EMAIL = 'legal@risedual.ai';
const PRICE = '$45';
const UPDATED = 'April 11, 2026';

const TABS = [
  { id: 'terms', label: 'Terms of Service', icon: FileText },
  { id: 'privacy', label: 'Privacy Policy', icon: Shield },
  { id: 'risk', label: 'Risk Disclosure', icon: AlertTriangle },
  { id: 'disclaimer', label: 'Disclaimer', icon: Scale },
];

/* ─── Section helper ─── */
const S = ({ title, children }) => (
  <div className="mb-6">
    <h3 className="text-white text-sm font-semibold mb-2">{title}</h3>
    <div className="text-slate-300 text-xs leading-relaxed space-y-2">{children}</div>
  </div>
);

/* ═══════════════════════════════════════════════
   TERMS OF SERVICE
   ═══════════════════════════════════════════════ */
const TermsContent = () => (
  <div>
    <p className="text-slate-400 text-[11px] mb-4">Last Updated: {UPDATED}</p>

    <S title="1. Acceptance of Terms">
      <p>By accessing or using the RISEDUAL AI platform ("Service"), operated by {ENTITY}, a {STATE} corporation, you agree to be bound by these Terms of Service ("Terms"). If you do not agree to these Terms, you must not access or use the Service.</p>
    </S>

    <S title="2. Description of Service">
      <p>RISEDUAL AI provides an AI-powered market intelligence platform that includes, but is not limited to: real-time market data visualization, AI-driven market analysis, multi-model consensus predictions, options flow analysis, dark pool data aggregation, sector heatmaps, paper trading simulation, and third-party brokerage account connectivity. The Service is designed for informational and educational purposes only.</p>
    </S>

    <S title="3. Eligibility">
      <p>You must be at least 18 years of age and legally capable of entering into binding contracts to use the Service. By using the Service, you represent and warrant that you meet these requirements. Users under the age of 18 are strictly prohibited from using the Service.</p>
    </S>

    <S title="4. Account Registration">
      <p>To access certain features, you must create an account. You agree to provide accurate, current, and complete information during registration and to keep your account information updated. You are solely responsible for maintaining the confidentiality of your login credentials and for all activities that occur under your account. You must notify us immediately at {EMAIL} of any unauthorized use of your account.</p>
    </S>

    <S title="5. Subscription and Payment">
      <p>Certain features of the Service require a paid subscription ("Pro Plan") at a rate of {PRICE} per month. Subscription fees are billed monthly through our third-party payment processor, Stripe, Inc. All payments are non-refundable except as required by applicable law or at our sole discretion. You authorize us to charge your payment method on a recurring basis until you cancel. Cancellation takes effect at the end of the current billing period.</p>
    </S>

    <S title="6. Success Fee">
      <p>In addition to the monthly subscription, users who connect a third-party brokerage account to the Service are subject to a performance-based success fee. The success fee is calculated at a rate of one and a half percent (1.5%) of net monthly gains exceeding one thousand US dollars ($1,000) in a given billing period. The billing period resets on the first day of each calendar month. No success fee is charged during periods of loss, breakeven, or when gains do not exceed the $1,000 threshold. The success fee is assessed based on portfolio value changes as reported by connected broker accounts. Payment of success fees is manual and must be remitted promptly upon notification. {ENTITY} reserves the right to modify the fee rate or threshold with thirty (30) days prior written notice. By connecting a brokerage account, you acknowledge and agree to the success fee structure described herein.</p>
    </S>

    <S title="7. Third-Party Brokerage Connections">
      <p>The Service may allow you to connect third-party brokerage accounts by providing API keys or authorizing via OAuth. {ENTITY} does not act as a broker-dealer, investment advisor, or fiduciary. We do not have custody of your funds or securities. By connecting a brokerage account, you acknowledge that: (a) you are solely responsible for all trading decisions and activity on your connected accounts; (b) {ENTITY} is not liable for any losses, damages, or unauthorized transactions resulting from your use of connected broker APIs; (c) API keys are encrypted and stored securely, but you assume the risk of providing third-party credentials to our platform.</p>
    </S>

    <S title="8. AI-Generated Content">
      <p>The Service utilizes artificial intelligence and machine learning models to generate market predictions, analysis, and insights. All AI-generated content is for informational purposes only and does not constitute financial advice, investment recommendations, or solicitations to buy or sell any security. AI predictions are probabilistic in nature and may be inaccurate. Past performance of AI models does not guarantee future results.</p>
    </S>

    <S title="8. Acceptable Use">
      <p>You agree not to: (a) use the Service for any unlawful purpose or in violation of any applicable law or regulation; (b) reverse engineer, decompile, or disassemble any part of the Service; (c) attempt to gain unauthorized access to other users' accounts or our systems; (d) use automated means (bots, scrapers) to access the Service except through our provided APIs; (e) redistribute, resell, or commercially exploit the Service without written consent; (f) upload malicious code or interfere with the Service's operation.</p>
    </S>

    <S title="10. Intellectual Property">
      <p>All content, features, functionality, trademarks, and intellectual property of the Service are owned by {ENTITY} and protected by copyright, trademark, and other intellectual property laws. You may not copy, modify, distribute, or create derivative works based on our proprietary content without express written permission.</p>
    </S>

    <S title="11. Limitation of Liability">
      <p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, {ENTITY}, ITS OFFICERS, DIRECTORS, EMPLOYEES, AND AGENTS SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING BUT NOT LIMITED TO LOSS OF PROFITS, LOSS OF DATA, TRADING LOSSES, OR LOSS OF GOODWILL, ARISING OUT OF OR RELATED TO YOUR USE OF THE SERVICE, WHETHER BASED ON WARRANTY, CONTRACT, TORT, OR ANY OTHER LEGAL THEORY, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. OUR TOTAL LIABILITY FOR ALL CLAIMS RELATED TO THE SERVICE SHALL NOT EXCEED THE AMOUNT YOU PAID US IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.</p>
    </S>

    <S title="12. Indemnification">
      <p>You agree to indemnify, defend, and hold harmless {ENTITY} and its affiliates from any claims, liabilities, damages, losses, and expenses (including reasonable attorneys' fees) arising from your use of the Service, violation of these Terms, or infringement of any third-party rights.</p>
    </S>

    <S title="13. Termination">
      <p>{ENTITY} reserves the right to suspend or terminate your account at any time, with or without cause, and with or without notice. Upon termination, your right to use the Service ceases immediately. Sections relating to intellectual property, limitation of liability, indemnification, and governing law shall survive termination.</p>
    </S>

    <S title="14. Governing Law and Dispute Resolution">
      <p>These Terms shall be governed by and construed in accordance with the laws of the State of {STATE}, without regard to its conflict of law provisions. Any disputes arising under these Terms shall be resolved exclusively in the state or federal courts located in {STATE}. You agree to submit to the personal jurisdiction of such courts.</p>
    </S>

    <S title="15. Modifications">
      <p>{ENTITY} reserves the right to modify these Terms at any time. We will notify you of material changes by posting the updated Terms on the Service with a revised "Last Updated" date. Your continued use of the Service after such changes constitutes acceptance of the modified Terms.</p>
    </S>

    <S title="16. Contact Information">
      <p>For questions about these Terms, contact us at: {EMAIL}</p>
      <p>{ENTITY}<br/>State of Incorporation: {STATE}<br/>Website: {SITE}</p>
    </S>
  </div>
);

/* ═══════════════════════════════════════════════
   PRIVACY POLICY
   ═══════════════════════════════════════════════ */
const PrivacyContent = () => (
  <div>
    <p className="text-slate-400 text-[11px] mb-4">Last Updated: {UPDATED}</p>

    <S title="1. Introduction">
      <p>{ENTITY} ("we," "us," "our") respects your privacy and is committed to protecting your personal information. This Privacy Policy describes how we collect, use, disclose, and safeguard your information when you use the RISEDUAL AI platform ("Service").</p>
    </S>

    <S title="2. Information We Collect">
      <p><strong>Account Information:</strong> When you register, we collect your name, email address, and encrypted password hash. We do not store plaintext passwords.</p>
      <p><strong>Payment Information:</strong> Subscription payments are processed by Stripe, Inc. We do not store your credit card numbers, bank account details, or full payment credentials on our servers. Stripe's privacy policy governs the handling of your payment data.</p>
      <p><strong>Brokerage API Keys:</strong> If you connect a third-party brokerage account, your API keys are encrypted using AES-256 encryption before storage. We access your brokerage data solely to provide portfolio analytics and, where authorized, trade execution features.</p>
      <p><strong>Usage Data:</strong> We collect data about how you interact with the Service, including pages visited, features used, timestamps, device type, browser type, IP address, and referring URLs.</p>
      <p><strong>AI Chat Data:</strong> Conversations with our AI assistant are stored to provide session continuity. Chat data may include text messages, uploaded images, and voice transcriptions.</p>
      <p><strong>Push Notification Tokens:</strong> If you opt in to push notifications, we store your browser push subscription endpoint for delivering alerts.</p>
    </S>

    <S title="3. How We Use Your Information">
      <p>We use collected information to: (a) provide, maintain, and improve the Service; (b) process transactions and manage subscriptions; (c) send administrative communications; (d) deliver push notifications you have opted into; (e) generate AI-powered market analysis and personalized insights; (f) detect and prevent fraud, abuse, and security incidents; (g) comply with legal obligations.</p>
    </S>

    <S title="4. Third-Party Services">
      <p>We share data with the following categories of third-party service providers:</p>
      <p><strong>Payment Processing:</strong> Stripe, Inc. processes all payment transactions.</p>
      <p><strong>Market Data Providers:</strong> Alpha Vantage, Finnhub, Binance, and CoinGecko provide real-time and historical market data. Your individual queries may be sent to these providers to retrieve data.</p>
      <p><strong>AI Model Providers:</strong> OpenAI and Google (Gemini) process AI chat messages and market analysis requests. Message content is sent to these providers for inference. Please review their respective privacy policies.</p>
      <p><strong>Analytics:</strong> We may use analytics services to understand usage patterns and improve the Service.</p>
      <p>We do not sell your personal information to third parties.</p>
    </S>

    <S title="5. Data Security">
      <p>We implement industry-standard security measures including: HTTPS encryption for all data in transit, AES-256 encryption for sensitive credentials at rest, httpOnly secure cookies for authentication, bcrypt password hashing with individual salts, and regular security reviews. However, no method of transmission or storage is 100% secure. We cannot guarantee absolute security.</p>
    </S>

    <S title="6. Data Retention">
      <p>We retain your account data for as long as your account is active or as needed to provide the Service. Chat history, trading journal entries, and analytics data are retained unless you request deletion. You may request deletion of your account and associated data by contacting {EMAIL}. Certain data may be retained as required by law.</p>
    </S>

    <S title="7. Cookies and Tracking">
      <p>We use httpOnly cookies for authentication session management. These are essential cookies required for the Service to function. We do not use third-party advertising cookies or cross-site tracking pixels.</p>
    </S>

    <S title="8. Your Rights">
      <p>Depending on your jurisdiction, you may have the right to: (a) access the personal data we hold about you; (b) request correction of inaccurate data; (c) request deletion of your data; (d) object to or restrict certain processing; (e) data portability; (f) withdraw consent where processing is based on consent. To exercise these rights, contact us at {EMAIL}.</p>
    </S>

    <S title="9. Children's Privacy">
      <p>The Service is not intended for individuals under 18 years of age. We do not knowingly collect personal information from children. If we learn we have collected data from a child under 18, we will delete it promptly.</p>
    </S>

    <S title="10. Changes to This Policy">
      <p>We may update this Privacy Policy from time to time. We will notify you of material changes by updating the "Last Updated" date. Your continued use of the Service constitutes acceptance of the updated policy.</p>
    </S>

    <S title="11. Contact">
      <p>For privacy inquiries: {EMAIL}</p>
      <p>{ENTITY} | {STATE}</p>
    </S>
  </div>
);

/* ═══════════════════════════════════════════════
   RISK DISCLOSURE
   ═══════════════════════════════════════════════ */
const RiskContent = () => (
  <div>
    <p className="text-slate-400 text-[11px] mb-4">Last Updated: {UPDATED}</p>

    <div className="bg-amber-900/30 border border-amber-700/40 rounded-xl p-4 mb-6">
      <p className="text-amber-300 text-xs font-semibold mb-2">IMPORTANT: PLEASE READ THIS RISK DISCLOSURE CAREFULLY BEFORE USING THE SERVICE.</p>
      <p className="text-amber-200/80 text-xs">Trading stocks, options, cryptocurrencies, and other financial instruments involves substantial risk of loss and is not suitable for every investor. You should carefully consider whether trading is appropriate for you in light of your financial condition.</p>
    </div>

    <S title="1. General Trading Risks">
      <p>Trading and investing in securities, options, futures, and cryptocurrencies involves significant risk, including the risk of losing your entire investment. Prices can be highly volatile and are influenced by numerous factors beyond anyone's control, including economic conditions, geopolitical events, regulatory changes, and market sentiment. Past performance is not indicative of future results.</p>
    </S>

    <S title="2. AI Predictions and Analysis">
      <p>RISEDUAL AI uses artificial intelligence and machine learning models to generate market predictions, analysis, and signals. These AI-generated outputs are: (a) probabilistic estimates, not guarantees; (b) based on historical data, real-time feeds, and algorithmic models that may contain errors or biases; (c) subject to model limitations, data quality issues, and unforeseen market conditions; (d) not a substitute for independent research, professional financial advice, or sound judgment. AI models can and do produce inaccurate, incomplete, or misleading results. You should never rely solely on AI predictions for making trading or investment decisions.</p>
    </S>

    <S title="3. Options Trading Risks">
      <p>Options trading carries a high level of risk. Options are complex instruments, and you can lose the entire premium paid. Writing (selling) options can result in losses that substantially exceed the premium received. Options strategies involving multiple legs carry additional commissions and complexity. Not all options strategies are suitable for all investors.</p>
    </S>

    <S title="4. Cryptocurrency Risks">
      <p>Cryptocurrency markets operate 24/7 and are subject to extreme price volatility. Cryptocurrencies are largely unregulated and may be subject to future regulatory actions that could adversely affect their value. Cryptocurrency exchanges and wallets may be vulnerable to hacking. The value of any cryptocurrency may decline to zero.</p>
    </S>

    <S title="5. Dark Pool and Institutional Flow Data">
      <p>Dark pool data and institutional order flow information presented on the platform are derived from publicly available sources and third-party data providers. This data may be delayed, incomplete, or inaccurate. Dark pool transactions represent a subset of total market activity and should not be interpreted as definitive indicators of future price movements.</p>
    </S>

    <S title="6. Third-Party Brokerage Risks">
      <p>By connecting a third-party brokerage account to the Service, you acknowledge: (a) {ENTITY} is not responsible for the actions, errors, or omissions of any brokerage firm; (b) API connections may experience delays, disruptions, or failures; (c) orders placed through connected brokers are subject to the broker's own terms, conditions, and execution policies; (d) you bear full responsibility for the security of your API keys and any transactions executed through them.</p>
    </S>

    <S title="7. Paper Trading Limitations">
      <p>Paper trading (simulated trading) results do not represent actual trading and may not reflect the impact of real-world factors such as slippage, liquidity, market impact, and emotional decision-making. Paper trading performance should not be considered indicative of actual trading results.</p>
    </S>

    <S title="8. No Guarantee of Profits">
      <p>{ENTITY} makes no representations, warranties, or guarantees regarding the profitability of any trading strategy, AI prediction, signal, or analysis provided through the Service. Any testimonials, case studies, or performance metrics presented on the platform are for illustrative purposes only and do not guarantee similar results.</p>
    </S>

    <S title="9. Seek Professional Advice">
      <p>Before making any investment or trading decisions, you should consult with a qualified financial advisor, tax professional, and/or attorney who can evaluate your individual circumstances. The information provided by RISEDUAL AI is not tailored to any individual's specific investment needs or objectives.</p>
    </S>
  </div>
);

/* ═══════════════════════════════════════════════
   DISCLAIMER
   ═══════════════════════════════════════════════ */
const DisclaimerContent = () => (
  <div>
    <p className="text-slate-400 text-[11px] mb-4">Last Updated: {UPDATED}</p>

    <div className="bg-red-900/20 border border-red-700/30 rounded-xl p-4 mb-6">
      <p className="text-red-300 text-xs font-semibold">GENERAL DISCLAIMER: THE INFORMATION PROVIDED BY RISEDUAL AI IS FOR GENERAL INFORMATIONAL AND EDUCATIONAL PURPOSES ONLY. NOTHING ON THIS PLATFORM CONSTITUTES FINANCIAL, INVESTMENT, LEGAL, OR TAX ADVICE.</p>
    </div>

    <S title="1. Not a Broker-Dealer or Investment Advisor">
      <p>{ENTITY} is NOT a registered broker-dealer, investment advisor, or financial planner with the U.S. Securities and Exchange Commission (SEC), the Financial Industry Regulatory Authority (FINRA), or any state securities regulatory authority. {ENTITY} does not provide personalized investment advice, and no content on the platform should be construed as such.</p>
    </S>

    <S title="2. No Fiduciary Relationship">
      <p>Use of the Service does not create a fiduciary, advisory, or professional relationship between you and {ENTITY}. We do not owe you a duty of care, loyalty, or best execution with respect to any trading or investment activity.</p>
    </S>

    <S title="3. Informational Purposes Only">
      <p>All market data, AI predictions, analysis, signals, research reports, news aggregations, and other content provided through the Service are for informational and educational purposes only. Such content is not intended as a recommendation or solicitation to buy, sell, or hold any security, cryptocurrency, or financial instrument.</p>
    </S>

    <S title="4. Accuracy of Information">
      <p>While we strive to provide accurate and up-to-date information, {ENTITY} makes no warranties or representations as to the accuracy, completeness, timeliness, reliability, or suitability of any information displayed on the platform. Market data is sourced from third-party providers and may be delayed, inaccurate, or incomplete. We are not responsible for errors or omissions in third-party data.</p>
    </S>

    <S title="5. Third-Party Content and Links">
      <p>The Service may display content sourced from third parties, including news articles, financial data, and market commentary. The inclusion of third-party content does not imply endorsement or verification by {ENTITY}. We are not responsible for the accuracy or reliability of third-party content.</p>
    </S>

    <S title="6. Regulatory Compliance">
      <p>It is your responsibility to ensure that your use of the Service and any trading activity complies with all applicable federal, state, and local laws and regulations in your jurisdiction. Securities and cryptocurrency regulations vary by jurisdiction, and certain features of the Service may not be available or appropriate in all locations.</p>
    </S>

    <S title="7. Forward-Looking Statements">
      <p>The platform may contain forward-looking statements, predictions, and projections about market trends, stock prices, and economic conditions. These statements are inherently uncertain and based on assumptions that may prove incorrect. Actual results may differ materially from any projections or predictions presented.</p>
    </S>

    <S title="8. No Warranty">
      <p>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, AND NON-INFRINGEMENT. {ENTITY} DOES NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR SECURE.</p>
    </S>

    <S title="9. Assumption of Risk">
      <p>By using the Service, you expressly acknowledge and agree that you use the Service at your sole risk. You are solely responsible for any investment or trading decisions you make. {ENTITY} shall not be held liable for any trading losses, financial damages, or other consequences arising from your reliance on the Service.</p>
    </S>

    <S title="10. Contact">
      <p>If you have questions about this Disclaimer, contact us at: {EMAIL}</p>
      <p>{ENTITY} | Incorporated in the State of {STATE}</p>
    </S>
  </div>
);

/* ═══════════════════════════════════════════════
   MAIN COMPONENT
   ═══════════════════════════════════════════════ */
const CONTENT = {
  terms: TermsContent,
  privacy: PrivacyContent,
  risk: RiskContent,
  disclaimer: DisclaimerContent,
};

const LegalPages = ({ onClose, initialTab = 'terms' }) => {
  const [tab, setTab] = useState(initialTab);
  const Content = CONTENT[tab];

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center p-2 sm:p-4" data-testid="legal-modal">
      <div className="bg-[#0D1526] border border-slate-700/50 rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/40">
          <h2 className="text-white text-base font-semibold tracking-wide">Legal</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors" data-testid="legal-close-btn">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/40 overflow-x-auto">
          {TABS.map(t => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                data-testid={`legal-tab-${t.id}`}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium whitespace-nowrap border-b-2 transition-all ${
                  tab === t.id
                    ? 'border-[#3DE8D9] text-[#3DE8D9]'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <Content />
        </div>
      </div>
    </div>
  );
};

export default LegalPages;
