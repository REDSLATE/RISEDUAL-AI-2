#!/usr/bin/env python3
"""
RISEDUAL AI - Complete Codebase PDF Generator
Generates a comprehensive PDF with all source code, architecture docs,
setup instructions, API docs, database schemas, and deployment guide.
"""

import os
import sys
import json
from datetime import datetime

from fpdf import FPDF

# ─── Configuration ───────────────────────────────────────────────────────────

OUTPUT_PATH = "/app/RISEDUAL_AI_Complete_Codebase.pdf"

# Deep navy theme colors (RGB)
NAVY = (15, 23, 42)        # #0F172A
BLUE_ACCENT = (0, 82, 255) # #0052FF
WHITE = (255, 255, 255)
LIGHT_GRAY = (200, 210, 225)
CODE_BG = (30, 41, 59)     # #1E293B
SECTION_BG = (20, 30, 50)

# ─── PDF Class ───────────────────────────────────────────────────────────────

class RiseDualPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.set_auto_page_break(auto=True, margin=20)
        self.alias_nb_pages()
        # Track sections for TOC
        self.toc_entries = []
        self.current_section = ""

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*LIGHT_GRAY)
        self.cell(0, 6, f"RISEDUAL AI - Complete Codebase Documentation", align="L")
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*BLUE_ACCENT)
        self.line(10, 12, 200, 12)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*LIGHT_GRAY)
        self.cell(0, 10, f"Generated {datetime.now().strftime('%B %d, %Y')} | RISEDUAL AI by Red Slate Holdings", align="C")

    def add_cover_page(self):
        self.add_page()
        # Navy background
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 297, "F")
        
        # Title
        self.set_y(60)
        self.set_font("Helvetica", "B", 42)
        self.set_text_color(*WHITE)
        self.cell(0, 18, "RISEDUAL AI", align="C", new_x="LMARGIN", new_y="NEXT")
        
        self.set_font("Helvetica", "", 16)
        self.set_text_color(*BLUE_ACCENT)
        self.cell(0, 10, "Complete Codebase Documentation", align="C", new_x="LMARGIN", new_y="NEXT")
        
        self.ln(8)
        self.set_draw_color(*BLUE_ACCENT)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(12)
        
        # Subtitle info
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*LIGHT_GRAY)
        info_lines = [
            "AI-Powered Trading Intelligence Platform",
            "React + FastAPI + MongoDB + Stripe",
            "",
            f"Document Generated: {datetime.now().strftime('%B %d, %Y')}",
            f"Total Source Files: 60+",
            f"Architecture: Full-Stack PWA",
            "",
            "Red Slate Holdings",
            "managingdirector@redslateholdings.com",
        ]
        for line in info_lines:
            self.cell(0, 7, line, align="C", new_x="LMARGIN", new_y="NEXT")
        
        # Bottom accent
        self.set_y(250)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 120, 150)
        self.cell(0, 6, "CONFIDENTIAL - PROPRIETARY SOURCE CODE", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 6, "This document contains the complete source code for the RISEDUAL AI platform.", align="C")

    def add_toc_page(self):
        self.add_page()
        self.set_font("Helvetica", "B", 24)
        self.set_text_color(*NAVY)
        self.cell(0, 14, "Table of Contents", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.set_draw_color(*BLUE_ACCENT)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(8)
        # TOC entries will be filled later via placeholder
        self._toc_page = self.page_no()
        self._toc_y = self.get_y()

    def write_toc(self):
        """Write TOC entries (call after all content is added)."""
        current_page = self.page
        self.page = self._toc_page
        self.set_y(self._toc_y)
        
        last_section = ""
        for entry in self.toc_entries:
            section = entry.get("section", "")
            title = entry["title"]
            page = entry["page"]
            level = entry.get("level", 1)
            
            if section != last_section and section:
                self.ln(3)
                self.set_font("Helvetica", "B", 11)
                self.set_text_color(*BLUE_ACCENT)
                self.cell(0, 7, section, new_x="LMARGIN", new_y="NEXT")
                last_section = section
            
            indent = 5 * level
            self.set_font("Helvetica", "", 9)
            self.set_text_color(60, 60, 80)
            
            title_w = self.get_string_width(title) + indent
            page_str = str(page)
            page_w = self.get_string_width(page_str)
            
            self.set_x(10 + indent)
            self.cell(title_w + 2, 5.5, title)
            
            # Dots
            dots_w = 190 - 10 - indent - title_w - 2 - page_w - 2
            if dots_w > 5:
                dot_count = int(dots_w / self.get_string_width("."))
                self.cell(dots_w, 5.5, "." * dot_count)
            
            self.cell(page_w + 2, 5.5, page_str, new_x="LMARGIN", new_y="NEXT")
            
            if self.get_y() > 270:
                self.add_page()
        
        self.page = current_page

    def add_section_header(self, title, section=""):
        self.add_page()
        page = self.page_no()
        self.toc_entries.append({"title": title, "page": page, "section": section, "level": 0})
        
        # Section header bar
        self.set_fill_color(*NAVY)
        self.rect(10, self.get_y() - 2, 190, 16, "F")
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(*WHITE)
        self.set_x(15)
        self.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(6)

    def add_subsection(self, title, section=""):
        page = self.page_no()
        self.toc_entries.append({"title": title, "page": page, "section": section, "level": 1})
        
        self.ln(4)
        self.set_draw_color(*BLUE_ACCENT)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*NAVY)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def add_text(self, text, size=10, bold=False):
        self.set_font("Helvetica", "B" if bold else "", size)
        self.set_text_color(40, 40, 60)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def add_code_file(self, filepath, section=""):
        """Add a source code file with syntax display."""
        filename = filepath.replace("/app/", "")
        page = self.page_no()
        self.toc_entries.append({"title": filename, "page": page, "section": section, "level": 2})
        
        # File header
        self.ln(3)
        self.set_fill_color(30, 41, 59)
        self.rect(10, self.get_y(), 190, 8, "F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BLUE_ACCENT)
        self.set_x(13)
        self.cell(0, 8, f"FILE: {filename}", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)
        
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            self.set_font("Courier", "", 8)
            self.set_text_color(200, 50, 50)
            self.cell(0, 5, f"[Error reading file: {e}]", new_x="LMARGIN", new_y="NEXT")
            return
        
        lines = content.split("\n")
        line_count = len(lines)
        
        # File stats
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 140, 170)
        self.cell(0, 4, f"{line_count} lines | {len(content)} characters", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)
        
        # Code content
        self.set_font("Courier", "", 6.5)
        self.set_text_color(50, 55, 70)
        
        for i, line in enumerate(lines, 1):
            # Clean non-printable characters
            clean_line = ""
            for ch in line:
                if ord(ch) >= 32 or ch == '\t':
                    if ord(ch) < 128:
                        clean_line += ch
                    else:
                        clean_line += '?'
                elif ch == '\t':
                    clean_line += "    "
            
            clean_line = clean_line.replace('\t', '    ')
            
            # Line number
            line_num = f"{i:4d} | "
            
            # Truncate very long lines
            max_chars = 130
            if len(clean_line) > max_chars:
                clean_line = clean_line[:max_chars] + " ..."
            
            display = line_num + clean_line
            
            # Alternate background for readability
            if i % 2 == 0:
                self.set_fill_color(245, 247, 250)
                self.cell(190, 3.8, display, fill=True, new_x="LMARGIN", new_y="NEXT")
            else:
                self.cell(190, 3.8, display, new_x="LMARGIN", new_y="NEXT")
            
            # Page break check
            if self.get_y() > 278:
                self.add_page()
                self.set_font("Courier", "", 6.5)
                self.set_text_color(50, 55, 70)
                # Continuation header
                self.set_font("Helvetica", "I", 7)
                self.set_text_color(*BLUE_ACCENT)
                self.cell(0, 4, f"... {filename} (continued)", new_x="LMARGIN", new_y="NEXT")
                self.ln(1)
                self.set_font("Courier", "", 6.5)
                self.set_text_color(50, 55, 70)
        
        self.ln(3)

    def add_env_file(self, filepath, section=""):
        """Add .env file with sensitive values masked."""
        filename = filepath.replace("/app/", "")
        page = self.page_no()
        self.toc_entries.append({"title": filename + " (keys masked)", "page": page, "section": section, "level": 2})
        
        self.ln(3)
        self.set_fill_color(30, 41, 59)
        self.rect(10, self.get_y(), 190, 8, "F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BLUE_ACCENT)
        self.set_x(13)
        self.cell(0, 8, f"ENV: {filename} (sensitive values masked)", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        
        try:
            with open(filepath, "r") as f:
                lines = f.readlines()
        except:
            return
        
        self.set_font("Courier", "", 7.5)
        self.set_text_color(50, 55, 70)
        
        sensitive_keys = ["KEY", "SECRET", "PASSWORD", "TOKEN", "VAPID", "JWT"]
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                self.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
                continue
            
            if "=" in line:
                key, _, value = line.partition("=")
                is_sensitive = any(s in key.upper() for s in sensitive_keys)
                if is_sensitive and value and value != "your_paypal_client_id" and value != "your_paypal_secret":
                    masked = value[:4] + "*" * min(20, len(value) - 4) + "..." if len(value) > 4 else "****"
                    display = f"{key}={masked}"
                else:
                    display = line
                self.cell(0, 4.5, display[:140], new_x="LMARGIN", new_y="NEXT")
            else:
                self.cell(0, 4.5, line[:140], new_x="LMARGIN", new_y="NEXT")
        
        self.ln(3)


def build_pdf():
    pdf = RiseDualPDF()
    
    # ═══════════════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_cover_page()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TABLE OF CONTENTS (placeholder - filled at end)
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_toc_page()
    
    # ═══════════════════════════════════════════════════════════════════════
    # 1. PROJECT OVERVIEW
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("1. Project Overview", "Documentation")
    
    overview = """RISEDUAL AI is a full-stack AI-powered trading intelligence platform built for Red Slate Holdings. It provides real-time market data, AI-driven predictions, dark pool analysis, options flow screening, crypto tracking, and a comprehensive trading workspace with subscription-based access control.

TECH STACK:
- Frontend: React 18, TailwindCSS, Shadcn/UI, Recharts, PWA (Service Worker + Web Push)
- Backend: Python FastAPI, MongoDB (Motor Async Driver), APScheduler
- AI: OpenAI GPT-5.2 via Emergent Integrations (Universal Key)
- Payments: Stripe ($45/month subscription)
- Email: Resend (transactional + daily digest)
- Auth: JWT Bearer tokens (PyJWT + bcrypt)
- Market Data: Alpha Vantage API (stocks, crypto, options)

KEY FEATURES:
1. Real-time Stock & Crypto Tickers with price tracking
2. Multimodal AI Chat Assistant (text + image analysis)
3. AI Market Prediction Engine (scrapes news, macro data, real estate, foreign markets)
4. Dark Pool Data Monitoring (Pro feature, blur wall for free users)
5. Options Flow Radar & Screener
6. Perplexity-style Company Research with AI synthesis
7. Macro Intelligence Dashboard (World Events, Foreign Markets, Congressional Trades)
8. Trading Journal with P&L Analytics & AI Hypothesis linking
9. Stripe Subscription Gateway ($45/month or $486/year)
10. 7 Paywall/Pro Features (chat limits, watchlist caps, PDF export, etc.)
11. Gamified Referral Program with Leaderboard & Social Sharing
12. Admin-Controlled Promo Campaigns with sticky banners
13. Resend Email Notifications (Welcome, Referral, Reward)
14. Daily Digest Emails at 6 AM UTC (APScheduler)
15. PWA Web Push Notifications (5 trigger types, rate-limited)
16. Owner Admin Panel (user management, Pro grants, promo control)
17. Responsive Mobile Design with Bottom Navigation
18. Chart Pattern Library (SVG interactive patterns)
19. Portfolio Analyzer (Pro feature)
20. AI Market Signals (Pro feature)

USER ROLES:
- Owner: Full admin access, can activate/deactivate users, grant/revoke Pro
- Admin: Elevated access, can manage promos and broadcast notifications
- Pro User: Unlimited access to all features ($45/month)
- Free User: Limited access (5 chats/day, 5 watchlist items, blur walls, 5 journal trades)
- Trial User: Pro access for 7 days (via referral program)"""
    
    pdf.add_text(overview)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 2. ARCHITECTURE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("2. Architecture & Directory Structure", "Documentation")
    
    arch = """DIRECTORY STRUCTURE:
/app
|-- frontend/                    # React PWA Application
|   |-- public/
|   |   |-- index.html           # HTML entry point with PWA meta tags
|   |   |-- manifest.json        # PWA manifest (name, icons, theme)
|   |   |-- service-worker.js    # Push notification handler + offline cache
|   |   |-- logo-*.png           # Brand logos (32, 192, 512, AI avatar)
|   |-- src/
|   |   |-- App.js               # Root component, routing, auth state
|   |   |-- App.css              # Global styles, glassmorphism, animations
|   |   |-- index.js             # React entry point
|   |   |-- index.css            # TailwindCSS + custom CSS variables
|   |   |-- mockData.js          # Fallback data for demo mode
|   |   |-- services/
|   |   |   |-- api.js           # Axios instance, auth interceptors
|   |   |-- contexts/
|   |   |   |-- AuthContext.jsx   # JWT auth state management
|   |   |-- hooks/
|   |   |   |-- use-toast.js     # Toast notification hook
|   |   |   |-- usePushNotifications.js  # Web Push subscription hook
|   |   |-- components/
|   |   |   |-- Navbar.jsx           # Main navigation bar
|   |   |   |-- MobileBottomNav.jsx  # Mobile bottom tab navigation
|   |   |   |-- AuthModal.jsx        # Login/Register modal
|   |   |   |-- UserWorkspace.jsx    # User dashboard workspace
|   |   |   |-- AdminPanel.jsx       # Owner admin panel
|   |   |   |-- StockTicker.jsx      # Real-time stock ticker strip
|   |   |   |-- CryptoTicker.jsx     # Crypto price ticker
|   |   |   |-- CryptoSection.jsx    # Crypto market section
|   |   |   |-- DataTable.jsx        # Reusable data table component
|   |   |   |-- DarkPoolData.jsx     # Dark pool activity (Pro blur wall)
|   |   |   |-- OptionsRadar.jsx     # Options flow radar display
|   |   |   |-- OptionsFlowScreener.jsx # Advanced options screener
|   |   |   |-- FilterPanel.jsx      # Market data filter panel
|   |   |   |-- TradeGPTChat.jsx     # Multimodal AI chat interface
|   |   |   |-- AIHypothesis.jsx     # AI hypothesis generation
|   |   |   |-- ChartPatternLibrary.jsx # SVG chart pattern reference
|   |   |   |-- CompanyResearch.jsx  # Perplexity-style research
|   |   |   |-- MarketPrediction.jsx # AI market prediction display
|   |   |   |-- MacroDashboard.jsx   # Macro intelligence dashboard
|   |   |   |-- MarketSignals.jsx    # Pro market signals
|   |   |   |-- PortfolioAnalyzer.jsx # Portfolio analysis (Pro)
|   |   |   |-- TradingJournal.jsx   # Trade journal + analytics
|   |   |   |-- Watchlist.jsx        # User watchlist
|   |   |   |-- QuickTrade.jsx       # Quick trade panel
|   |   |   |-- BrokerConnect.jsx    # Broker connection modal
|   |   |   |-- SubscriptionPricing.jsx # Stripe pricing/checkout
|   |   |   |-- PaymentStatus.jsx    # Payment confirmation page
|   |   |   |-- ProBlurWall.jsx      # Blur overlay for free users
|   |   |   |-- AlertsPanel.jsx      # Alerts & notifications
|   |   |   |-- AdditionalSections.jsx # Extra dashboard sections
|   |   |   |-- PromoBanner.jsx      # Admin promo banner display
|   |   |   |-- ReferralLeaderboard.jsx # Referral leaderboard
|   |   |   |-- SocialShareButtons.jsx # Social media share buttons
|   |   |-- components/ui/           # Shadcn/UI component library
|   |-- package.json
|   |-- tailwind.config.js
|   |-- .env
|
|-- backend/                     # FastAPI Backend
|   |-- server.py                # App factory, DB init, router mounts, APScheduler
|   |-- .env                     # Environment variables
|   |-- requirements.txt         # Python dependencies
|   |-- models/
|   |   |-- chat.py              # Chat & AI message schemas
|   |   |-- subscription.py      # Subscription plan models
|   |   |-- trading.py           # Trading & order models
|   |-- routes/
|   |   |-- __init__.py
|   |   |-- auth.py              # Auth: login, register, admin, JWT
|   |   |-- ai.py                # AI: chat, hypothesis, predictions, research
|   |   |-- subscription.py      # Stripe checkout, webhooks, plan mgmt
|   |   |-- workspace.py         # Watchlist, history, notifications
|   |   |-- trading.py           # Broker, orders, positions
|   |   |-- market.py            # Stocks, crypto, dark pool, options
|   |   |-- referral.py          # Referral codes, tracking, rewards
|   |   |-- promo.py             # Admin promo campaign CRUD
|   |   |-- digest.py            # Daily digest trigger, opt-in/out
|   |   |-- push.py              # Push subscribe, broadcast, test
|   |   |-- journal.py           # Trading journal CRUD + analytics
|   |-- services/
|   |   |-- ai_service.py        # GPT-5.2 chat, vision, analysis
|   |   |-- market_data_service.py # Alpha Vantage data fetching
|   |   |-- market_prediction_service.py # AI prediction engine
|   |   |-- company_research_service.py  # Company research synthesis
|   |   |-- hypothesis_service.py # AI hypothesis generation
|   |   |-- financial_scraping_service.py # News & financial scraping
|   |   |-- real_estate_scraping_service.py # Real estate data
|   |   |-- crypto_scraping_service.py # Crypto market scraping
|   |   |-- world_events_service.py # Global events scraping
|   |   |-- foreign_markets_service.py # Foreign market data
|   |   |-- gov_filings_service.py # Congressional trade data
|   |   |-- broker_service.py    # Mock broker integration
|   |   |-- payment_service.py   # Stripe payment processing
|   |   |-- email_service.py     # Resend email templates
|   |   |-- digest_service.py    # Daily digest generation
|   |   |-- push_service.py      # Web Push notification service
|   |-- tests/                   # Pytest test suite (17 test files)"""
    
    pdf.add_text(arch)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 3. SETUP & DEPLOYMENT
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("3. Setup & Deployment Guide", "Documentation")
    
    setup = """PREREQUISITES:
- Node.js 18+ and Yarn
- Python 3.10+
- MongoDB 6.0+
- Stripe Account (for payment processing)
- Alpha Vantage API Key (free tier: 5 calls/min)
- Resend API Key (for email notifications)

BACKEND SETUP:
  cd backend
  python -m venv venv
  source venv/bin/activate  # Linux/Mac
  pip install -r requirements.txt
  # Copy .env.example to .env and fill in your values
  uvicorn server:app --host 0.0.0.0 --port 8001 --reload

FRONTEND SETUP:
  cd frontend
  yarn install
  # Copy .env.example to .env and set REACT_APP_BACKEND_URL
  yarn start

ENVIRONMENT VARIABLES (Backend .env):
  MONGO_URL=mongodb://localhost:27017
  DB_NAME=tradealgo_db
  JWT_SECRET=<generate-random-256-bit-hex>
  ALPHA_VANTAGE_API_KEY=<your-key>
  EMERGENT_LLM_KEY=<your-emergent-universal-key>
  STRIPE_API_KEY=sk_live_...
  STRIPE_PUBLISHABLE_KEY=pk_live_...
  STRIPE_PRICE_ID=price_...
  RESEND_API_KEY=re_...
  SENDER_EMAIL=noreply@yourdomain.com
  VAPID_PRIVATE_KEY=<generate-vapid-keys>
  VAPID_PUBLIC_KEY=<generate-vapid-keys>
  OWNER_EMAIL=<owner-email>
  OWNER_PASSWORD=<owner-password>
  ADMIN_EMAIL=<admin-email>
  ADMIN_PASSWORD=<admin-password>
  CORS_ORIGINS=*

ENVIRONMENT VARIABLES (Frontend .env):
  REACT_APP_BACKEND_URL=https://yourdomain.com
  REACT_APP_VAPID_PUBLIC_KEY=<same-as-backend-public-key>

DATABASE:
  MongoDB collections are auto-created on first use:
  - users, chat_sessions, payment_transactions, watchlists
  - notifications, referral_codes, promos, push_subscriptions
  - push_log, trades

DEPLOYMENT NOTES:
  - All backend API routes must be prefixed with /api
  - Frontend proxies /api/* requests to the backend
  - VAPID keys must match between frontend and backend
  - Owner and Admin accounts are auto-seeded on startup
  - APScheduler runs daily digest at 6 AM UTC
  - Service worker must be served from root (/) for PWA"""
    
    pdf.add_text(setup)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 4. DATABASE SCHEMAS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("4. Database Schemas", "Documentation")
    
    schemas = """MONGODB COLLECTIONS:

users:
  {
    email: String (unique),
    password: String (bcrypt hashed),
    name: String,
    role: "user" | "admin" | "owner",
    subscription: "free" | "pro" | "trial",
    trial_expires_at: DateTime | null,
    is_active: Boolean (default true),
    referred_by: String | null,
    created_at: DateTime
  }

chat_sessions:
  {
    session_id: String (UUID),
    user_id: String,
    messages: [
      { role: "user" | "assistant", content: String, timestamp: DateTime,
        image_data: String | null }
    ],
    title: String,
    created_at: DateTime,
    updated_at: DateTime
  }

payment_transactions:
  {
    session_id: String (Stripe session),
    user_id: String | null,
    amount: Number,
    currency: String,
    plan: String,
    status: "pending" | "completed" | "failed",
    created_at: DateTime
  }

watchlists:
  {
    user_id: String,
    symbols: [String],
    updated_at: DateTime
  }

notifications:
  {
    user_id: String,
    type: String,
    title: String,
    message: String,
    read: Boolean,
    created_at: DateTime
  }

referral_codes:
  {
    user_id: String,
    code: String (unique, 8-char),
    rewards_earned: Number (default 0),
    rewards_remaining: Number (default 12),
    referrals: [
      { email: String, signed_up_at: DateTime, subscribed: Boolean }
    ],
    created_at: DateTime
  }

promos:
  {
    title: String,
    description: String,
    link: String | null,
    is_active: Boolean,
    created_by: String,
    created_at: DateTime
  }

push_subscriptions:
  {
    user_id: String,
    subscription_data: Object (Web Push subscription),
    is_active: Boolean,
    created_at: DateTime,
    updated_at: DateTime
  }

push_log:
  {
    user_id: String,
    notification_type: String,
    sent_at: DateTime
  }

trades:
  {
    user_id: String,
    ticker: String,
    type: "long" | "short",
    entry_price: Number,
    exit_price: Number | null,
    quantity: Number,
    status: "open" | "closed",
    notes: String,
    tags: [String],
    ai_hypothesis_id: String | null,
    opened_at: DateTime,
    closed_at: DateTime | null,
    created_at: DateTime
  }"""
    
    pdf.add_text(schemas)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 5. API REFERENCE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("5. API Reference", "Documentation")
    
    api_docs = """ALL ENDPOINTS (prefixed with /api):

AUTH (/api/auth):
  POST /api/auth/register        - Register new user (email, password, name, referral_code?)
  POST /api/auth/login           - Login (email, password) -> access_token, refresh_token
  POST /api/auth/refresh         - Refresh access token
  GET  /api/auth/me              - Get current user profile [Auth Required]
  GET  /api/auth/admin/users     - List all users [Owner Only]
  PUT  /api/auth/admin/users/{id}/toggle-active  - Activate/deactivate user [Owner]
  PUT  /api/auth/admin/users/{id}/toggle-pro     - Grant/revoke Pro [Owner]

AI (/api/ai):
  POST /api/chat                 - Send chat message (multimodal) [Auth, Rate Limited]
  GET  /api/chat/sessions        - List chat sessions [Auth]
  GET  /api/chat/sessions/{id}   - Get session messages [Auth, Pro for history]
  POST /api/hypothesis/generate  - Generate AI hypothesis [Auth]
  GET  /api/predictions          - Get AI market predictions [Auth]
  GET  /api/research/{symbol}    - Perplexity-style company research [Auth]
  POST /api/portfolio/analyze    - AI portfolio analysis [Pro Only]
  GET  /api/signals              - AI market signals [Pro Only]

MARKET (/api/market):
  GET  /api/stocks/top           - Top stocks with real-time prices
  GET  /api/crypto/prices        - Crypto prices (BTC, ETH, etc.)
  GET  /api/darkpool             - Dark pool activity data [Pro Only]
  GET  /api/options/flow         - Options flow data
  GET  /api/macro/dashboard      - Macro intelligence dashboard

SUBSCRIPTION (/api/subscription):
  GET  /api/subscription/plans   - Available subscription plans
  POST /api/subscription/create-checkout-session - Stripe checkout [Auth]
  POST /api/subscription/webhook - Stripe webhook handler
  GET  /api/payment/status/{session_id} - Payment status check

WORKSPACE (/api/workspace):
  GET  /api/workspace/watchlist       - Get user watchlist [Auth]
  POST /api/workspace/watchlist       - Add to watchlist [Auth, Capped for Free]
  DELETE /api/workspace/watchlist/{s} - Remove from watchlist [Auth]
  GET  /api/workspace/history         - Chat history [Auth, Pro Only]
  GET  /api/workspace/notifications   - User notifications [Auth]
  PUT  /api/workspace/notifications/{id}/read - Mark read [Auth]

TRADING (/api/trading):
  POST /api/broker/connect       - Connect broker account
  POST /api/trading/order        - Place trade order
  GET  /api/trading/positions    - Get open positions
  GET  /api/trading/history      - Trade execution history

REFERRAL (/api/referral):
  POST /api/referral/register    - Generate referral code [Auth]
  GET  /api/referral/status      - Get referral stats [Auth]
  GET  /api/referral/leaderboard - Public referral leaderboard

PROMO (/api/promo):
  POST /api/promo/create         - Create promo campaign [Admin/Owner]
  GET  /api/promo/active          - Get active promos
  GET  /api/promo/all            - List all promos [Admin/Owner]
  PUT  /api/promo/{id}/toggle    - Toggle promo active state [Admin/Owner]
  DELETE /api/promo/{id}         - Delete promo [Admin/Owner]

DIGEST (/api/digest):
  POST /api/digest/trigger       - Manually trigger digest [Admin]
  POST /api/digest/opt-in        - Opt into daily digest [Auth]
  POST /api/digest/opt-out       - Opt out of daily digest [Auth]
  GET  /api/digest/preview       - Preview digest email [Auth]

PUSH (/api/push):
  POST /api/push/subscribe       - Subscribe to push notifications [Auth]
  POST /api/push/unsubscribe     - Unsubscribe from push [Auth]
  GET  /api/push/status          - Get push subscription status [Auth]
  POST /api/push/test            - Send test notification [Auth]
  POST /api/push/broadcast       - Broadcast to all subscribers [Admin]

JOURNAL (/api/journal):
  GET  /api/journal/trades       - List user trades [Auth]
  POST /api/journal/trades       - Create trade entry [Auth, Capped for Free]
  PUT  /api/journal/trades/{id}  - Update trade [Auth]
  DELETE /api/journal/trades/{id} - Delete trade [Auth]
  PUT  /api/journal/trades/{id}/close - Close trade with exit price [Auth]
  POST /api/journal/trades/{id}/attach-hypothesis - Attach AI hypothesis [Auth]
  GET  /api/journal/analytics    - Trade analytics (win rate, P&L) [Auth]

AUTHENTICATION:
  All [Auth] endpoints require: Authorization: Bearer <access_token>
  Tokens expire after 24 hours. Use /api/auth/refresh to renew.
  
RATE LIMITING:
  Free users: 5 AI chats/day, 5 watchlist items, 5 journal trades
  Pro users: Unlimited access to all features
  Push notifications: Free users get 1/day, Pro users unlimited"""
    
    pdf.add_text(api_docs)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 6. ENVIRONMENT FILES
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("6. Environment Configuration", "Configuration")
    pdf.add_env_file("/app/backend/.env", "Configuration")
    pdf.add_env_file("/app/frontend/.env", "Configuration")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 7. BACKEND SOURCE CODE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("7. Backend - Core", "Backend Source Code")
    
    backend_core = [
        "/app/backend/server.py",
        "/app/backend/requirements.txt",
    ]
    for f in backend_core:
        if os.path.exists(f):
            pdf.add_code_file(f, "Backend Source Code")
    
    pdf.add_subsection("Backend - Models", "Backend Source Code")
    backend_models = sorted([
        "/app/backend/models/chat.py",
        "/app/backend/models/subscription.py",
        "/app/backend/models/trading.py",
    ])
    for f in backend_models:
        if os.path.exists(f):
            pdf.add_code_file(f, "Backend Source Code")
    
    pdf.add_subsection("Backend - Routes", "Backend Source Code")
    routes_dir = "/app/backend/routes"
    route_files = sorted([os.path.join(routes_dir, f) for f in os.listdir(routes_dir) if f.endswith(".py")])
    for f in route_files:
        pdf.add_code_file(f, "Backend Source Code")
    
    pdf.add_subsection("Backend - Services", "Backend Source Code")
    services_dir = "/app/backend/services"
    service_files = sorted([os.path.join(services_dir, f) for f in os.listdir(services_dir) if f.endswith(".py")])
    for f in service_files:
        pdf.add_code_file(f, "Backend Source Code")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 8. FRONTEND SOURCE CODE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("8. Frontend - Core", "Frontend Source Code")
    
    frontend_core = [
        "/app/frontend/src/App.js",
        "/app/frontend/src/App.css",
        "/app/frontend/src/index.js",
        "/app/frontend/src/index.css",
        "/app/frontend/src/mockData.js",
        "/app/frontend/src/services/api.js",
        "/app/frontend/src/contexts/AuthContext.jsx",
        "/app/frontend/src/hooks/use-toast.js",
        "/app/frontend/src/hooks/usePushNotifications.js",
        "/app/frontend/src/lib/utils.js",
    ]
    for f in frontend_core:
        if os.path.exists(f):
            pdf.add_code_file(f, "Frontend Source Code")
    
    pdf.add_subsection("Frontend - Components", "Frontend Source Code")
    comp_dir = "/app/frontend/src/components"
    comp_files = sorted([os.path.join(comp_dir, f) for f in os.listdir(comp_dir) 
                         if f.endswith(".jsx") and not os.path.isdir(os.path.join(comp_dir, f))])
    for f in comp_files:
        pdf.add_code_file(f, "Frontend Source Code")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 9. FRONTEND CONFIG
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("9. Frontend - Configuration & PWA", "Frontend Config")
    
    frontend_config = [
        "/app/frontend/package.json",
        "/app/frontend/tailwind.config.js",
        "/app/frontend/postcss.config.js",
        "/app/frontend/craco.config.js",
        "/app/frontend/jsconfig.json",
        "/app/frontend/components.json",
        "/app/frontend/public/manifest.json",
        "/app/frontend/public/service-worker.js",
        "/app/frontend/public/index.html",
    ]
    for f in frontend_config:
        if os.path.exists(f):
            pdf.add_code_file(f, "Frontend Config")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 10. TEST SUITE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_section_header("10. Test Suite (Backend)", "Tests")
    
    tests_dir = "/app/backend/tests"
    if os.path.exists(tests_dir):
        test_files = sorted([os.path.join(tests_dir, f) for f in os.listdir(tests_dir) if f.endswith(".py")])
        for f in test_files:
            pdf.add_code_file(f, "Tests")
    
    # ═══════════════════════════════════════════════════════════════════════
    # WRITE TOC & SAVE
    # ═══════════════════════════════════════════════════════════════════════
    # pdf.write_toc()  # Skip dynamic TOC for simplicity/stability
    
    print(f"Generating PDF with {pdf.page_no()} pages...")
    pdf.output(OUTPUT_PATH)
    print(f"PDF saved to: {OUTPUT_PATH}")
    print(f"File size: {os.path.getsize(OUTPUT_PATH) / (1024*1024):.1f} MB")


if __name__ == "__main__":
    build_pdf()
