# RISEDUAL AI — Changes Since PDF Baseline

**PDF baseline:** `RISEDUAL_AI_Complete_Codebase_2.pdf` — Generated **April 07, 2026**
**Current state:** `/app` on the preview environment — **$(date -u '+%Y-%m-%d %H:%M UTC')**

---

## 📊 Summary

| Bucket | Count |
|---|---|
| 🟢 NEW files since PDF | 639 |
| 🔴 DELETED files since PDF | 29 |
| 🟡 COMMON (in both — may have been edited) | 73 |
| **Total files in current codebase** | **712** |
| Total files in PDF snapshot | 102 |

---

## 🔽 How to get the full current content

Two paths depending on what you need:

1. **Full current codebase as a single TXT** — go to **Admin → Developer Tools → "Download Codebase"** (or hit `/api/admin/download/codebase-txt`). Output format matches the PDF (same `FILE:` headers), so you can diff section-by-section against `RISEDUAL_AI_Complete_Codebase_2.pdf` in VS Code / Beyond Compare / any text diff tool.
2. **Per-file pick** — use the three lists below to navigate. All paths are absolute on the preview env.

---

## 🔴 Files REMOVED since April 7, 2026

These 29 files existed in the PDF but no longer exist in current code. Every removal is a legitimate refactor consolidation — nothing was lost, just renamed/merged:

| File | Replaced by |
|---|---|
| `backend/craco.config.js` | *moved* → `frontend/craco.config.js` (frontend tooling wrongly nested in /backend) |
| `backend/package.json` | *moved* → `frontend/package.json` |
| `backend/postcss.config.js` | *moved* → `frontend/postcss.config.js` |
| `frontend/vite.config.js` | *replaced* → CRA + Craco (`frontend/craco.config.js`) |
| `frontend/src/components/AppContent.jsx` | *merged into* `frontend/src/App.js` |
| `frontend/src/components/TradeGPTChat.jsx` | *renamed to* `frontend/src/components/RiseDualGPTChat.jsx` |
| `frontend/src/components/CryptoSection.jsx` | *replaced by* Research hub modules |
| `frontend/src/hooks/useToast.js` | *replaced by* `shadcn/sonner` (see `components/ui/sonner.tsx`) |
| `frontend/src/services/*.js` (17 files) | *replaced by* React hooks + `AuthContext`/`authFetch` pattern |
| `frontend/src/styles/globals.css` | *merged into* `frontend/src/index.css` |
| `backend/tests/test_auth_login.py` | *replaced by* expanded test suite (see new tests section) |
| `backend/tests/test_foreign_markets.py` | *replaced by* refactored market-data tests |
| `backend/tests/test_tutorial_api.py` | *deprecated* — tutorial API dropped |
| `backend/tests/test_user_workspace.py` | *replaced by* `test_admin_workspace.py` + related |
| `backend/tests/test_watchlist.py` | *replaced by* new watchlist intelligence tests |


---

## 🟢 NEW files added since April 7, 2026 (639 total)

Grouped by top-level area for readability. Every entry is a full file that did not exist in the PDF.

### 📈 Distribution by area

| Area | New files | What it represents |
|---|---:|---|
| `backend/routes/`        |  50 | Modularized API routes (spun out of server.py) |
| `backend/services/`      |  86 | Business-logic services (ML, broker, digest, scanner, etc.) |
| `backend/risedual_core/` |  35 | Adversarial AI engine (Strategist + Auditor, ML, patterns, calibration) |
| `backend/tests/`         | 109 | New pytest suites (regression coverage) |
| `backend/scripts/`       |  12 | Ops scripts (deploy markers, self-test, seeds) |
| `frontend/src/components/` | 166 | New UI components (Hubs, Chat, War Room, Admin panels) |
| `frontend/src/hooks/`    |   9 | Custom hooks (useWatchlistData, useV2Nav, useChat, useStreamingAgent) |
| `frontend/src/contexts/` |   0 | React contexts (AuthContext, etc.) |
| `frontend/src/utils/`    |   5 | Shared utils (apiBase, logger, etc.) |
| `frontend/src/pages/`    |   0 | Route-level pages (compliance, share, etc.) |

<details>
<summary>▶️ Click to expand full NEW file list (639 paths)</summary>

```
/app/.pytest_cache/README.md
/app/DEPLOYMENT_GUIDE.md
/app/README.md
/app/RISEDUAL_AI_Codebase_Workup.md
/app/backend/.pytest_cache/README.md
/app/backend/backtest_results/backtest_backtest_fresh.json
/app/backend/risedual_core/MIGRATION.md
/app/backend/risedual_core/README.md
/app/backend/risedual_core/pyproject.toml
/app/backend/risedual_core/risedual_core.egg-info/SOURCES.txt
/app/backend/risedual_core/risedual_core.egg-info/dependency_links.txt
/app/backend/risedual_core/risedual_core.egg-info/requires.txt
/app/backend/risedual_core/risedual_core.egg-info/top_level.txt
/app/backend/risedual_core/risedual_core/__init__.py
/app/backend/risedual_core/risedual_core/adapters/__init__.py
/app/backend/risedual_core/risedual_core/adapters/cli.py
/app/backend/risedual_core/risedual_core/adapters/fastapi.py
/app/backend/risedual_core/risedual_core/clients/__init__.py
/app/backend/risedual_core/risedual_core/clients/alpha_vantage.py
/app/backend/risedual_core/risedual_core/clients/base.py
/app/backend/risedual_core/risedual_core/clients/finnhub.py
/app/backend/risedual_core/risedual_core/clients/fmp.py
/app/backend/risedual_core/risedual_core/clients/fred.py
/app/backend/risedual_core/risedual_core/llm/__init__.py
/app/backend/risedual_core/risedual_core/llm/anthropic.py
/app/backend/risedual_core/risedual_core/llm/base.py
/app/backend/risedual_core/risedual_core/llm/openai.py
/app/backend/risedual_core/risedual_core/llm/router.py
/app/backend/risedual_core/risedual_core/ml/__init__.py
/app/backend/risedual_core/risedual_core/ml/calibration.py
/app/backend/risedual_core/risedual_core/ml/calibration_gate.py
/app/backend/risedual_core/risedual_core/ml/features.py
/app/backend/risedual_core/risedual_core/ml/patterns.py
/app/backend/risedual_core/risedual_core/ml/regime_model.py
/app/backend/risedual_core/risedual_core/ml/signal_model.py
/app/backend/risedual_core/risedual_core/schemas/__init__.py
/app/backend/risedual_core/risedual_core/schemas/market.py
/app/backend/risedual_core/risedual_core/secrets/__init__.py
/app/backend/risedual_core/risedual_core/secrets/keyvault.py
/app/backend/risedual_core/risedual_core/tools/__init__.py
/app/backend/risedual_core/risedual_core/tools/registry.py
/app/backend/route_registry.py
/app/backend/routes/__init__.py
/app/backend/routes/accuracy.py
/app/backend/routes/admin.py
/app/backend/routes/ai.py
/app/backend/routes/analytics.py
/app/backend/routes/auth.py
/app/backend/routes/billing.py
/app/backend/routes/broker.py
/app/backend/routes/credits.py
/app/backend/routes/demo.py
/app/backend/routes/digest.py
/app/backend/routes/failure_loop.py
/app/backend/routes/fred.py
/app/backend/routes/headlines.py
/app/backend/routes/intelligence.py
/app/backend/routes/journal.py
/app/backend/routes/market.py
/app/backend/routes/market_data.py
/app/backend/routes/media.py
/app/backend/routes/ml_orchestrator.py
/app/backend/routes/orderflow_stream.py
/app/backend/routes/orderflow_ws.py
/app/backend/routes/paper_trading.py
/app/backend/routes/promo.py
/app/backend/routes/provider_health.py
/app/backend/routes/public_api.py
/app/backend/routes/push.py
/app/backend/routes/referral.py
/app/backend/routes/risk_calculator.py
/app/backend/routes/scanner.py
/app/backend/routes/sectors.py
/app/backend/routes/security_audit.py
/app/backend/routes/self_test.py
/app/backend/routes/share.py
/app/backend/routes/share_image.py
/app/backend/routes/signal.py
/app/backend/routes/smart_orders.py
/app/backend/routes/stockfit.py
/app/backend/routes/stockfit_13f.py
/app/backend/routes/strategy.py
/app/backend/routes/stream.py
/app/backend/routes/subscription.py
/app/backend/routes/success_fee.py
/app/backend/routes/trading.py
/app/backend/routes/trading_bots.py
/app/backend/routes/vault.py
/app/backend/routes/waitlist.py
/app/backend/routes/web_intelligence.py
/app/backend/routes/whale_radar.py
/app/backend/routes/workspace.py
/app/backend/scripts/backfill_historical.py
/app/backend/scripts/backfill_insider_activity.py
/app/backend/scripts/backfill_insider_edgar.py
/app/backend/scripts/backfill_insider_submissions.py
/app/backend/scripts/backfill_patterns.py
/app/backend/scripts/backfill_regimes.py
/app/backend/scripts/backfill_sector_momentum.py
/app/backend/scripts/backfill_sentiment.py
/app/backend/scripts/backtest.py
/app/backend/scripts/merge_owner_into_admin.py
/app/backend/scripts/train_regime_model.py
/app/backend/scripts/train_signal_model.py
/app/backend/services/ai_cache_service.py
/app/backend/services/ai_guardrails.py
/app/backend/services/ai_intelligence_service.py
/app/backend/services/ai_pool.py
/app/backend/services/ai_signal_validator.py
/app/backend/services/auth_helpers.py
/app/backend/services/backtester_service.py
/app/backend/services/cache.py
/app/backend/services/chat_memory_service.py
/app/backend/services/credit_service.py
/app/backend/services/crew_definitions.py
/app/backend/services/crew_engine.py
/app/backend/services/cusip_mapper.py
/app/backend/services/failure_loop_service.py
/app/backend/services/fear_greed_service.py
/app/backend/services/financial_tools.py
/app/backend/services/financial_tools_agent.py
/app/backend/services/finnhub_service.py
/app/backend/services/fred_service.py
/app/backend/services/headlines_pipeline.py
/app/backend/services/help_search_digest.py
/app/backend/services/hypothesis_logger.py
/app/backend/services/key_rotator.py
/app/backend/services/key_vault.py
/app/backend/services/lobbying_service.py
/app/backend/services/market_data_pool.py
/app/backend/services/market_memory_service.py
/app/backend/services/market_sentiment_service.py
/app/backend/services/memory_training_service.py
/app/backend/services/ml_alert_service.py
/app/backend/services/ml_alpaca_broker.py
/app/backend/services/ml_orchestrator.py
/app/backend/services/ml_paper_trader.py
/app/backend/services/ml_retrain_service.py
/app/backend/services/multi_model_hypothesis_service.py
/app/backend/services/order_flow_service.py
/app/backend/services/orderflow_ws_service.py
/app/backend/services/paper_trading_service.py
/app/backend/services/polygon_dark_pool_service.py
/app/backend/services/pool_config.py
/app/backend/services/portfolio_agent.py
/app/backend/services/post_mortem_service.py
/app/backend/services/prediction_labeler.py
/app/backend/services/prediction_tracker.py
/app/backend/services/price_provider.py
/app/backend/services/provider_pool.py
/app/backend/services/provider_registry.py
/app/backend/services/providerrouter.py
/app/backend/services/quiver_service.py
/app/backend/services/referral_rewards.py
/app/backend/services/scanner_service.py
/app/backend/services/search_war_room/__init__.py
/app/backend/services/search_war_room/adapters/__init__.py
/app/backend/services/search_war_room/adapters/ai_analysis.py
/app/backend/services/search_war_room/adapters/av_news.py
/app/backend/services/search_war_room/adapters/ddg.py
/app/backend/services/search_war_room/adapters/finnhub_news.py
/app/backend/services/search_war_room/adapters/fred.py
/app/backend/services/search_war_room/adapters/marketstack.py
/app/backend/services/search_war_room/adapters/newsapi.py
/app/backend/services/search_war_room/adapters/sec.py
/app/backend/services/search_war_room/adapters/stockfit.py
/app/backend/services/search_war_room/adapters/tavily.py
/app/backend/services/search_war_room/adapters/wikipedia.py
/app/backend/services/search_war_room/adapters/yahoo.py
/app/backend/services/search_war_room/cache.py
/app/backend/services/search_war_room/orchestrator.py
/app/backend/services/search_war_room/registry.py
/app/backend/services/search_war_room/schemas.py
/app/backend/services/search_war_room/synthesizer.py
/app/backend/services/sec_13f_service.py
/app/backend/services/sector_service.py
/app/backend/services/self_test_service.py
/app/backend/services/sliding_cache.py
/app/backend/services/smart_order_service.py
/app/backend/services/storage_service.py
/app/backend/services/strategy_service.py
/app/backend/services/stripe_billing_service.py
/app/backend/services/success_fee_service.py
/app/backend/services/trading_bot_service.py
/app/backend/services/usaspending_service.py
/app/backend/services/waitlist_service.py
/app/backend/services/war_room_service.py
/app/backend/services/watchlist_intelligence_service.py
/app/backend/services/web_intelligence_service.py
/app/backend/services/world_events_service.py
/app/backend/templates/waitlist_widget.js
/app/backend/tests/.pytest_cache/README.md
/app/backend/tests/__init__.py
/app/backend/tests/conftest.py
/app/backend/tests/conftest_creds.py
/app/backend/tests/test_broker_endpoints.py
/app/backend/tests/test_cache_admin.py
/app/backend/tests/test_comprehensive_predeployment.py
/app/backend/tests/test_fear_greed_lobbying.py
/app/backend/tests/test_forgot_password.py
/app/backend/tests/test_iteration100_refactoring.py
/app/backend/tests/test_iteration101_login_landing.py
/app/backend/tests/test_iteration102_user_badges.py
/app/backend/tests/test_iteration103_auth_login_fix.py
/app/backend/tests/test_iteration104_code_quality.py
/app/backend/tests/test_iteration105_component_refactoring.py
/app/backend/tests/test_iteration106_refactoring.py
/app/backend/tests/test_iteration107_waitlist_analytics.py
/app/backend/tests/test_iteration108_smart_orders.py
/app/backend/tests/test_iteration109_risk_calculator.py
/app/backend/tests/test_iteration110_market_scanner.py
/app/backend/tests/test_iteration111_rule_builder.py
/app/backend/tests/test_iteration112_ai_signal_validation.py
/app/backend/tests/test_iteration113_trading_bots.py
/app/backend/tests/test_iteration114_bots_dashboard_help.py
/app/backend/tests/test_iteration116_success_fee.py
/app/backend/tests/test_iteration117_badge_showcase.py
/app/backend/tests/test_iteration120_developer_api.py
/app/backend/tests/test_iteration121_credit_system.py
/app/backend/tests/test_iteration122_credit_tiers.py
/app/backend/tests/test_iteration123_failure_loop.py
/app/backend/tests/test_iteration124_cache_keyrotation_warroom.py
/app/backend/tests/test_iteration125_provider_pool.py
/app/backend/tests/test_iteration126_provider_health.py
/app/backend/tests/test_iteration127_provider_badges.py
/app/backend/tests/test_iteration128_dynamic_registration.py
/app/backend/tests/test_iteration129_key_vault.py
/app/backend/tests/test_iteration130_ml_v6.py
/app/backend/tests/test_iteration131_ml_paper_calibration.py
/app/backend/tests/test_iteration132_public_developer_api.py
/app/backend/tests/test_iteration133_stockfit_fundamentals.py
/app/backend/tests/test_iteration134_13f_holder_tracking.py
/app/backend/tests/test_iteration135_l2_chat_actions.py
/app/backend/tests/test_iteration24_fixes.py
/app/backend/tests/test_iteration25_code_quality.py
/app/backend/tests/test_iteration26_strategy_builder.py
/app/backend/tests/test_iteration27_backtester.py
/app/backend/tests/test_iteration28_marketplace.py
/app/backend/tests/test_iteration29_ai_intelligence.py
/app/backend/tests/test_iteration30_watchlist_intelligence.py
/app/backend/tests/test_iteration31_digest_watchlist_intel.py
/app/backend/tests/test_iteration32_async_blocking_fix.py
/app/backend/tests/test_iteration36_code_quality.py
/app/backend/tests/test_iteration39_new_features.py
/app/backend/tests/test_iteration41_ux_polish.py
/app/backend/tests/test_iteration42_code_quality.py
/app/backend/tests/test_iteration43_code_quality.py
/app/backend/tests/test_iteration44_refactoring.py
/app/backend/tests/test_iteration45_postmessage_fix.py
/app/backend/tests/test_iteration46_ai_features.py
/app/backend/tests/test_iteration47_api_base_fix.py
/app/backend/tests/test_iteration48_chat_sector_fix.py
/app/backend/tests/test_iteration49_multi_agent.py
/app/backend/tests/test_iteration50_accuracy_tracker.py
/app/backend/tests/test_iteration51_price_provider.py
/app/backend/tests/test_iteration52_sector_sentiment.py
/app/backend/tests/test_iteration53_oauth_sentiment_history.py
/app/backend/tests/test_iteration54_market_memory.py
/app/backend/tests/test_iteration55_memory_training.py
/app/backend/tests/test_iteration56_fear_greed_enrichment.py
/app/backend/tests/test_iteration57_strategist_context.py
/app/backend/tests/test_iteration58_memory_cleanup.py
/app/backend/tests/test_iteration59_toxic_spikes_alert.py
/app/backend/tests/test_iteration60_dual_signal_adversarial.py
/app/backend/tests/test_iteration61_failure_mode_classification.py
/app/backend/tests/test_iteration62_ai_post_mortem.py
/app/backend/tests/test_iteration63_sse_stream.py
/app/backend/tests/test_iteration64_memory_broker.py
/app/backend/tests/test_iteration65_order_flow.py
/app/backend/tests/test_iteration66_binance_l2.py
/app/backend/tests/test_iteration67_sse_orderflow.py
/app/backend/tests/test_iteration68_intensity_whale_alerts.py
/app/backend/tests/test_iteration69_whale_radar.py
/app/backend/tests/test_iteration73_paper_trading.py
/app/backend/tests/test_iteration74_portfolio_agent.py
/app/backend/tests/test_iteration75_confirmation_gated_orders.py
/app/backend/tests/test_iteration82_voice_capabilities.py
/app/backend/tests/test_iteration83_media_upload.py
/app/backend/tests/test_iteration84_broker_execution_privileges.py
/app/backend/tests/test_iteration86_code_quality.py
/app/backend/tests/test_iteration87_security_audit.py
/app/backend/tests/test_iteration89_chat_memory.py
/app/backend/tests/test_iteration90_memory_pinning.py
/app/backend/tests/test_iteration91_ticker_prediction.py
/app/backend/tests/test_iteration92_ticker_prediction_fix.py
/app/backend/tests/test_iteration93_code_quality.py
/app/backend/tests/test_iteration94_refactoring.py
/app/backend/tests/test_iteration95_waitlist.py
/app/backend/tests/test_iteration96_auto_invite.py
/app/backend/tests/test_iteration97_beta_key_redemption.py
/app/backend/tests/test_iteration98_code_quality.py
/app/backend/tests/test_iteration99_code_quality.py
/app/backend/tests/test_leaderboard_feature.py
/app/backend/tests/test_macro_data.py
/app/backend/tests/test_ml_retrain_service.py
/app/backend/tests/test_multi_model_hypothesis.py
/app/backend/tests/test_paywall_features.py
/app/backend/tests/test_refactored_components.py
/app/backend/tests/test_share_endpoint.py
/app/backend/tests/test_war_room.py
/app/backend_test.py
/app/contracts.md
/app/design_guidelines.json
/app/docker-compose.yml
/app/docs/ML_CORE_SPEC.md
/app/frontend/README.md
/app/frontend/components.json
/app/frontend/craco.config.js
/app/frontend/jsconfig.json
/app/frontend/plugins/health-check/health-endpoints.js
/app/frontend/plugins/health-check/webpack-health-plugin.js
/app/frontend/postcss.config.js
/app/frontend/src/components/AIIntelligence.jsx
/app/frontend/src/components/AIWarRoom.jsx
/app/frontend/src/components/AboutUs.jsx
/app/frontend/src/components/AccuracyBadge.jsx
/app/frontend/src/components/AdditionalSections.jsx
/app/frontend/src/components/AdversarialHub.jsx
/app/frontend/src/components/AlpacaOAuthDemo.jsx
/app/frontend/src/components/BacktestResults.jsx
/app/frontend/src/components/CalibrationChart.jsx
/app/frontend/src/components/ComplianceOAuth.jsx
/app/frontend/src/components/CreditBadge.jsx
/app/frontend/src/components/CreditStore.jsx
/app/frontend/src/components/DeveloperPortal.jsx
/app/frontend/src/components/ErrorBoundary.jsx
/app/frontend/src/components/FailureLoopDashboard.jsx
/app/frontend/src/components/FearGreedGauge.jsx
/app/frontend/src/components/Footer.jsx
/app/frontend/src/components/HelpCenter.jsx
/app/frontend/src/components/InfoTooltip.jsx
/app/frontend/src/components/LandingPage.jsx
/app/frontend/src/components/LegalPages.jsx
/app/frontend/src/components/LiveDemoOverlay.jsx
/app/frontend/src/components/LiveInsightsFeed.jsx
/app/frontend/src/components/MLControls.jsx
/app/frontend/src/components/MLPaperPnL.jsx
/app/frontend/src/components/MarketScanner.jsx
/app/frontend/src/components/MarketsSection.jsx
/app/frontend/src/components/MemoryDashboard.jsx
/app/frontend/src/components/MobileMenu.jsx
/app/frontend/src/components/ModalManager.jsx
/app/frontend/src/components/OnboardingTour.jsx
/app/frontend/src/components/OrderFlowHeatmap.jsx
/app/frontend/src/components/OrderFlowPanel.jsx
/app/frontend/src/components/PanelShell.jsx
/app/frontend/src/components/PaperTrading.jsx
/app/frontend/src/components/PnLTracker.jsx
/app/frontend/src/components/PublicProfile.jsx
/app/frontend/src/components/ResetPasswordModal.jsx
/app/frontend/src/components/RiseDualGPTChat.jsx
/app/frontend/src/components/RiskCalculator.jsx
/app/frontend/src/components/ScrollToTop.jsx
/app/frontend/src/components/SearchWarRoom.jsx
/app/frontend/src/components/SectorHeatmap.jsx
/app/frontend/src/components/ShareBoardLeaderboard.jsx
/app/frontend/src/components/ShareSmartMoneyBoard.jsx
/app/frontend/src/components/SmartOrderPanel.jsx
/app/frontend/src/components/SparkLine.jsx
/app/frontend/src/components/StockFit13F.jsx
/app/frontend/src/components/StockFitFundamentals.jsx
/app/frontend/src/components/StrategyBuilder.jsx
/app/frontend/src/components/StrategyMarketplace.jsx
/app/frontend/src/components/TradingBotPanel.jsx
/app/frontend/src/components/TradingJournal.jsx
/app/frontend/src/components/UserBadge.jsx
/app/frontend/src/components/WaitlistModal.jsx
/app/frontend/src/components/WatchlistIntelligence.jsx
/app/frontend/src/components/WhaleRadar.jsx
/app/frontend/src/components/admin/AdminTools.jsx
/app/frontend/src/components/admin/BrokerOAuthConfig.jsx
/app/frontend/src/components/admin/CacheMonitor.jsx
/app/frontend/src/components/admin/ChipAdoptionInsights.jsx
/app/frontend/src/components/admin/HelpSearchInsights.jsx
/app/frontend/src/components/admin/KeyVault.jsx
/app/frontend/src/components/admin/MediaManager.jsx
/app/frontend/src/components/admin/PromoManager.jsx
/app/frontend/src/components/admin/ProviderHealth.jsx
/app/frontend/src/components/admin/SecurityAudit.jsx
/app/frontend/src/components/admin/SelfTestPanel.jsx
/app/frontend/src/components/admin/UsersTab.jsx
/app/frontend/src/components/admin/WaitlistAdmin.jsx
/app/frontend/src/components/admin/WaitlistAnalytics.jsx
/app/frontend/src/components/alerts/NotificationItem.jsx
/app/frontend/src/components/auth/AuthForm.jsx
/app/frontend/src/components/auth/ForgotPasswordForm.jsx
/app/frontend/src/components/chat/AgentTrace.jsx
/app/frontend/src/components/chat/ChatComponents.jsx
/app/frontend/src/components/chat/ChatHeader.jsx
/app/frontend/src/components/chat/ChatHistorySidebar.jsx
/app/frontend/src/components/chat/ChatInput.jsx
/app/frontend/src/components/chat/ChatMessages.jsx
/app/frontend/src/components/chat/MemoryPanel.jsx
/app/frontend/src/components/chat/VoiceSelector.jsx
/app/frontend/src/components/heatmap/HeatmapHeader.jsx
/app/frontend/src/components/heatmap/HeatmapLegend.jsx
/app/frontend/src/components/heatmap/SectorTile.jsx
/app/frontend/src/components/hubs/IconTabBar.jsx
/app/frontend/src/components/hubs/OptionsHub.jsx
/app/frontend/src/components/hubs/ResearchHub.jsx
/app/frontend/src/components/hubs/StockDetailHub.jsx
/app/frontend/src/components/hubs/TerminalModeHub.jsx
/app/frontend/src/components/hubs/WarRoomHub.jsx
/app/frontend/src/components/hubs/WorkspaceHub.jsx
/app/frontend/src/components/hypothesis/HypothesisLocked.jsx
/app/frontend/src/components/hypothesis/HypothesisResults.jsx
/app/frontend/src/components/hypothesis/ModelSelector.jsx
/app/frontend/src/components/intelligence/BriefView.jsx
/app/frontend/src/components/intelligence/PatternsView.jsx
/app/frontend/src/components/macro/CongressTab.jsx
/app/frontend/src/components/macro/ForeignMarketsTab.jsx
/app/frontend/src/components/macro/FredEconomyTab.jsx
/app/frontend/src/components/macro/MacroShared.jsx
/app/frontend/src/components/macro/WorldEventsTab.jsx
/app/frontend/src/components/oauth-demo/DemoShared.jsx
/app/frontend/src/components/oauth-demo/StepAlpacaAuth.jsx
/app/frontend/src/components/oauth-demo/StepBrokerConnect.jsx
/app/frontend/src/components/oauth-demo/StepDashboard.jsx
/app/frontend/src/components/oauth-demo/StepDisclosure.jsx
/app/frontend/src/components/oauth-demo/StepLanding.jsx
/app/frontend/src/components/oauth-demo/StepSuccessRevoke.jsx
/app/frontend/src/components/prediction/PredictionCards.jsx
/app/frontend/src/components/scanner/RuleBuilder.jsx
/app/frontend/src/components/scanner/ValidationResults.jsx
/app/frontend/src/components/smart-orders/SmartOrderList.jsx
/app/frontend/src/components/smart-orders/SmartOrderPreview.jsx
/app/frontend/src/components/strategy/StrategyPreview.jsx
/app/frontend/src/components/ui/accordion.jsx
/app/frontend/src/components/ui/alert-dialog.jsx
/app/frontend/src/components/ui/alert.jsx
/app/frontend/src/components/ui/aspect-ratio.jsx
/app/frontend/src/components/ui/avatar.jsx
/app/frontend/src/components/ui/badge.jsx
/app/frontend/src/components/ui/breadcrumb.jsx
/app/frontend/src/components/ui/button.jsx
/app/frontend/src/components/ui/calendar.jsx
/app/frontend/src/components/ui/card.jsx
/app/frontend/src/components/ui/carousel.jsx
/app/frontend/src/components/ui/checkbox.jsx
/app/frontend/src/components/ui/collapsible.jsx
/app/frontend/src/components/ui/command.jsx
/app/frontend/src/components/ui/context-menu.jsx
/app/frontend/src/components/ui/dialog.jsx
/app/frontend/src/components/ui/drawer.jsx
/app/frontend/src/components/ui/dropdown-menu.jsx
/app/frontend/src/components/ui/form.jsx
/app/frontend/src/components/ui/hover-card.jsx
/app/frontend/src/components/ui/input-otp.jsx
/app/frontend/src/components/ui/input.jsx
/app/frontend/src/components/ui/label.jsx
/app/frontend/src/components/ui/menubar.jsx
/app/frontend/src/components/ui/navigation-menu.jsx
/app/frontend/src/components/ui/pagination.jsx
/app/frontend/src/components/ui/popover.jsx
/app/frontend/src/components/ui/progress.jsx
/app/frontend/src/components/ui/radio-group.jsx
/app/frontend/src/components/ui/resizable.jsx
/app/frontend/src/components/ui/scroll-area.jsx
/app/frontend/src/components/ui/select.jsx
/app/frontend/src/components/ui/separator.jsx
/app/frontend/src/components/ui/sheet.jsx
/app/frontend/src/components/ui/skeleton.jsx
/app/frontend/src/components/ui/slider.jsx
/app/frontend/src/components/ui/sonner.jsx
/app/frontend/src/components/ui/switch.jsx
/app/frontend/src/components/ui/table.jsx
/app/frontend/src/components/ui/tabs.jsx
/app/frontend/src/components/ui/textarea.jsx
/app/frontend/src/components/ui/toast.jsx
/app/frontend/src/components/ui/toaster.jsx
/app/frontend/src/components/ui/toggle-group.jsx
/app/frontend/src/components/ui/toggle.jsx
/app/frontend/src/components/ui/tooltip.jsx
/app/frontend/src/components/warroom/SearchWarRoomCards.jsx
/app/frontend/src/components/warroom/WarRoomCards.jsx
/app/frontend/src/components/watchlist/SmartMoneyShiftAlerts.jsx
/app/frontend/src/components/watchlist/WatchlistTable.jsx
/app/frontend/src/components/watchlist/WatchlistToolbar.jsx
/app/frontend/src/hooks/use-toast.js
/app/frontend/src/hooks/useChat.js
/app/frontend/src/hooks/useChatMemory.js
/app/frontend/src/hooks/useModals.js
/app/frontend/src/hooks/useReferralCapture.js
/app/frontend/src/hooks/useStreamingAgent.js
/app/frontend/src/hooks/useTTS.js
/app/frontend/src/hooks/useV2Nav.js
/app/frontend/src/hooks/useWatchlistData.js
/app/frontend/src/mockData.js
/app/frontend/src/styles/war-room-theme.css
/app/frontend/src/utils/apiBase.js
/app/frontend/src/utils/deepLink.js
/app/frontend/src/utils/exportHypothesis.js
/app/frontend/src/utils/logger.js
/app/frontend/src/utils/recentTickers.js
/app/frontend/tailwind.config.js
/app/generate_pdf.py
/app/image_testing.md
/app/memory/CHANGELOG.md
/app/memory/DEPLOYMENT_NOTES.md
/app/memory/HEALTH_LOG.md
/app/memory/PRD.md
/app/memory/test_credentials.md
/app/migrate_db.py
/app/test_reports/iteration_1.json
/app/test_reports/iteration_10.json
/app/test_reports/iteration_100.json
/app/test_reports/iteration_101.json
/app/test_reports/iteration_102.json
/app/test_reports/iteration_103.json
/app/test_reports/iteration_104.json
/app/test_reports/iteration_105.json
/app/test_reports/iteration_106.json
/app/test_reports/iteration_107.json
/app/test_reports/iteration_108.json
/app/test_reports/iteration_109.json
/app/test_reports/iteration_11.json
/app/test_reports/iteration_110.json
/app/test_reports/iteration_111.json
/app/test_reports/iteration_112.json
/app/test_reports/iteration_113.json
/app/test_reports/iteration_114.json
/app/test_reports/iteration_115.json
/app/test_reports/iteration_116.json
/app/test_reports/iteration_117.json
/app/test_reports/iteration_118.json
/app/test_reports/iteration_119.json
/app/test_reports/iteration_12.json
/app/test_reports/iteration_121.json
/app/test_reports/iteration_122.json
/app/test_reports/iteration_123.json
/app/test_reports/iteration_124.json
/app/test_reports/iteration_125.json
/app/test_reports/iteration_126.json
/app/test_reports/iteration_127.json
/app/test_reports/iteration_128.json
/app/test_reports/iteration_129.json
/app/test_reports/iteration_13.json
/app/test_reports/iteration_130.json
/app/test_reports/iteration_131.json
/app/test_reports/iteration_132.json
/app/test_reports/iteration_133.json
/app/test_reports/iteration_134.json
/app/test_reports/iteration_135.json
/app/test_reports/iteration_14.json
/app/test_reports/iteration_15.json
/app/test_reports/iteration_16.json
/app/test_reports/iteration_17.json
/app/test_reports/iteration_18.json
/app/test_reports/iteration_19.json
/app/test_reports/iteration_2.json
/app/test_reports/iteration_20.json
/app/test_reports/iteration_21.json
/app/test_reports/iteration_22.json
/app/test_reports/iteration_23.json
/app/test_reports/iteration_24.json
/app/test_reports/iteration_25.json
/app/test_reports/iteration_26.json
/app/test_reports/iteration_27.json
/app/test_reports/iteration_28.json
/app/test_reports/iteration_29.json
/app/test_reports/iteration_3.json
/app/test_reports/iteration_30.json
/app/test_reports/iteration_31.json
/app/test_reports/iteration_32.json
/app/test_reports/iteration_33.json
/app/test_reports/iteration_34.json
/app/test_reports/iteration_35.json
/app/test_reports/iteration_36.json
/app/test_reports/iteration_37.json
/app/test_reports/iteration_38.json
/app/test_reports/iteration_39.json
/app/test_reports/iteration_4.json
/app/test_reports/iteration_40.json
/app/test_reports/iteration_41.json
/app/test_reports/iteration_42.json
/app/test_reports/iteration_43.json
/app/test_reports/iteration_44.json
/app/test_reports/iteration_45.json
/app/test_reports/iteration_46.json
/app/test_reports/iteration_47.json
/app/test_reports/iteration_48.json
/app/test_reports/iteration_49.json
/app/test_reports/iteration_5.json
/app/test_reports/iteration_50.json
/app/test_reports/iteration_51.json
/app/test_reports/iteration_52.json
/app/test_reports/iteration_53.json
/app/test_reports/iteration_54.json
/app/test_reports/iteration_55.json
/app/test_reports/iteration_56.json
/app/test_reports/iteration_57.json
/app/test_reports/iteration_58.json
/app/test_reports/iteration_59.json
/app/test_reports/iteration_6.json
/app/test_reports/iteration_60.json
/app/test_reports/iteration_61.json
/app/test_reports/iteration_62.json
/app/test_reports/iteration_63.json
/app/test_reports/iteration_64.json
/app/test_reports/iteration_65.json
/app/test_reports/iteration_66.json
/app/test_reports/iteration_67.json
/app/test_reports/iteration_68.json
/app/test_reports/iteration_69.json
/app/test_reports/iteration_7.json
/app/test_reports/iteration_70.json
/app/test_reports/iteration_71.json
/app/test_reports/iteration_72.json
/app/test_reports/iteration_73.json
/app/test_reports/iteration_74.json
/app/test_reports/iteration_75.json
/app/test_reports/iteration_76.json
/app/test_reports/iteration_77.json
/app/test_reports/iteration_78.json
/app/test_reports/iteration_79.json
/app/test_reports/iteration_8.json
/app/test_reports/iteration_80.json
/app/test_reports/iteration_81.json
/app/test_reports/iteration_82.json
/app/test_reports/iteration_83.json
/app/test_reports/iteration_84.json
/app/test_reports/iteration_85.json
/app/test_reports/iteration_86.json
/app/test_reports/iteration_87.json
/app/test_reports/iteration_88.json
/app/test_reports/iteration_89.json
/app/test_reports/iteration_9.json
/app/test_reports/iteration_90.json
/app/test_reports/iteration_91.json
/app/test_reports/iteration_92.json
/app/test_reports/iteration_93.json
/app/test_reports/iteration_94.json
/app/test_reports/iteration_95.json
/app/test_reports/iteration_96.json
/app/test_reports/iteration_97.json
/app/test_reports/iteration_98.json
/app/test_reports/iteration_99.json
/app/test_result.md
/app/tests/__init__.py
```

</details>


---

## 🟡 COMMON files (in PDF baseline **and** current code — may have been modified)

These 73 paths existed in your PDF snapshot and still exist today. Most have been edited since.
Diff section-by-section against the PDF using the "Download Codebase" button output.

```
/app/backend/requirements.txt
/app/backend/server.py
/app/backend/services/ai_service.py
/app/backend/services/broker_service.py
/app/backend/services/company_research_service.py
/app/backend/services/crypto_scraping_service.py
/app/backend/services/digest_service.py
/app/backend/services/email_service.py
/app/backend/services/financial_scraping_service.py
/app/backend/services/foreign_markets_service.py
/app/backend/services/gov_filings_service.py
/app/backend/services/hypothesis_service.py
/app/backend/services/market_data_service.py
/app/backend/services/market_prediction_service.py
/app/backend/services/payment_service.py
/app/backend/services/push_service.py
/app/backend/services/real_estate_scraping_service.py
/app/backend/tests/test_admin_workspace.py
/app/backend/tests/test_auth_hypothesis.py
/app/backend/tests/test_bearer_auth.py
/app/backend/tests/test_chat_image.py
/app/backend/tests/test_company_research.py
/app/backend/tests/test_daily_digest.py
/app/backend/tests/test_email_notifications.py
/app/backend/tests/test_promo_campaign.py
/app/backend/tests/test_push_notifications.py
/app/backend/tests/test_referral_feature.py
/app/backend/tests/test_social_share_feature.py
/app/backend/tests/test_stripe_payment.py
/app/backend/tests/test_subscription_plans.py
/app/backend/tests/test_trading_journal.py
/app/frontend/package.json
/app/frontend/public/index.html
/app/frontend/public/manifest.json
/app/frontend/public/robots.txt
/app/frontend/public/service-worker.js
/app/frontend/src/App.css
/app/frontend/src/App.js
/app/frontend/src/components/AIHypothesis.jsx
/app/frontend/src/components/AdminPanel.jsx
/app/frontend/src/components/AlertsPanel.jsx
/app/frontend/src/components/AuthModal.jsx
/app/frontend/src/components/BrokerConnect.jsx
/app/frontend/src/components/ChartPatternLibrary.jsx
/app/frontend/src/components/CompanyResearch.jsx
/app/frontend/src/components/CryptoTicker.jsx
/app/frontend/src/components/DarkPoolData.jsx
/app/frontend/src/components/DataTable.jsx
/app/frontend/src/components/FilterPanel.jsx
/app/frontend/src/components/MacroDashboard.jsx
/app/frontend/src/components/MarketPrediction.jsx
/app/frontend/src/components/MarketSignals.jsx
/app/frontend/src/components/MobileBottomNav.jsx
/app/frontend/src/components/Navbar.jsx
/app/frontend/src/components/OptionsFlowScreener.jsx
/app/frontend/src/components/OptionsRadar.jsx
/app/frontend/src/components/PaymentStatus.jsx
/app/frontend/src/components/PortfolioAnalyzer.jsx
/app/frontend/src/components/ProBlurWall.jsx
/app/frontend/src/components/PromoBanner.jsx
/app/frontend/src/components/QuickTrade.jsx
/app/frontend/src/components/ReferralLeaderboard.jsx
/app/frontend/src/components/SocialShareButtons.jsx
/app/frontend/src/components/StockTicker.jsx
/app/frontend/src/components/SubscriptionPricing.jsx
/app/frontend/src/components/UserWorkspace.jsx
/app/frontend/src/components/Watchlist.jsx
/app/frontend/src/contexts/AuthContext.jsx
/app/frontend/src/hooks/usePushNotifications.js
/app/frontend/src/index.css
/app/frontend/src/index.js
/app/frontend/src/lib/utils.js
/app/frontend/src/services/api.js
```

---

## 🎯 Known behavioural deltas in the COMMON set (what to look for when diffing)

The following are the high-signal changes I'm confident about inside the common-file set — based on the deployment journal (`/app/memory/DEPLOYMENT_NOTES.md`), known fixes from the handoff summary, and in-session work:

### Backend
- **`backend/server.py`**
  - Startup flow now calls `register_all_routers()` + `wire_db()` from `route_registry.py` (server.py shrank dramatically — routes live in their own modules).
  - APScheduler block added: daily digest (6:00), watchlist pregen (5:30), memory cleanup (2:00), nightly ML retrain (2:30), headlines pipeline (15m), predictions (10m), ML labeler (1h), 13F scan (8:00), referral rewards (9:00 daily + monthly), help-search digest (Mon 7:00), USASpending warmup (3:30), **self-test monitor (15m)**.
  - Helper coroutines `_run_nightly_ml_retrain`, `_run_headlines_pipeline`, `_run_prediction_prewarm`, `_run_prediction_labeler`, `_run_13f_scan`, `_run_referral_*`, `_run_self_test_monitor`.

- **`backend/services/digest_service.py`**
  - Pro plan price hardcoded to **`$55/month`** (was `$45` in PDF era). Seven references throughout the daily digest email template.

- **`backend/services/payment_service.py`**
  - Canonical Stripe price constant `SUBSCRIPTION_PRICE_MONTHLY = 55.00`.
  - Annual option `SUBSCRIPTION_PRICE_ANNUAL = 594.00` ($49.50/month × 12 = 10% off — documented inline).

- **`backend/services/ai_service.py`**
  - Switched from direct OpenAI SDK → `emergentintegrations` with the Emergent LLM key (gpt-5.2, gpt-4o-mini routing).
  - Adversarial two-model flow: Strategist → Auditor veto.

- **`backend/services/broker_service.py`**
  - Full 3-legged OAuth implementation (Kraken, Alpaca, IBKR, Schwab).
  - Token rotation + audit log (`oauth_token_audit` collection).
  - `_execute_bot_trade` wired to Kraken/Alpaca for live executions.

- **`backend/services/market_prediction_service.py`**
  - Dynamic ATR tolerance for NEUTRAL calls.
  - Sliding price/prediction cache with reset caps.

- **`backend/services/hypothesis_service.py`**, **`market_data_service.py`**, **`foreign_markets_service.py`** — refactored to consume the price_provider pool and sliding cache.

### Frontend
- **`frontend/src/App.js`**
  - Heavily restructured: imports modularized, `useV2Nav` hook added, Classic UI fallback (`?v1=1`), new hub routing (Dashboard / War Room / Research / Options / Workspace).

- **`frontend/src/components/Navbar.jsx`**
  - v2 hub switcher added.
  - **(2026-02-19 this session)** Classic UI toggle pills retired from visible UI; `?v1=1` URL failsafe preserved.

- **`frontend/src/components/HelpCenter.jsx`**
  - Rewritten as 5-tab help system with search.
  - **(2026-02-19 this session)** 3 Classic UI mentions removed.

- **`frontend/src/components/SubscriptionPricing.jsx`** & **`LandingPage.jsx`**
  - **(earlier this session)** Pricing section optical-centering tweaks, Features grid made uniform 3×2, Action pricing table given a bordered container + colgroup for even column widths, Pro price synced to **$55**.

- **`frontend/src/components/AIHypothesis.jsx`**, **`MarketPrediction.jsx`**, **`MarketSignals.jsx`**, **`DarkPoolData.jsx`**, **`OptionsFlowScreener.jsx`** — all wired to new route prefixes under `/api/*`, updated for streaming agent responses.

- **`frontend/src/components/AdminPanel.jsx`**
  - Completely rebuilt with tabs pulling from `components/admin/*.jsx` (13 new admin panels including today's `SelfTestPanel.jsx`).

- **`frontend/src/components/BrokerConnect.jsx`**
  - OAuth-first flow; PDF-era was API-key-only.

- **`frontend/src/contexts/AuthContext.jsx`**
  - `authFetch` helper centralizes authed calls.
  - JWT refresh + logout event bus.

### Config
- **`frontend/package.json`** — new dependencies for shadcn/ui, sonner, lucide-react, @radix-ui/*, motion, react-router-dom.
- **`backend/requirements.txt`** — apscheduler, emergentintegrations, scikit-learn, xgboost, hmmlearn, stripe, motor, chromadb, openai/anthropic SDKs (via emergent layer).

---

## 📦 What's in the 29 DELETED files — summary

- `/app/backend/craco.config.js`
- `/app/backend/package.json`
- `/app/backend/postcss.config.js`
- `/app/backend/tests/test_auth_login.py`
- `/app/backend/tests/test_foreign_markets.py`
- `/app/backend/tests/test_tutorial_api.py`
- `/app/backend/tests/test_user_workspace.py`
- `/app/backend/tests/test_watchlist.py`
- `/app/frontend/src/components/AppContent.jsx`
- `/app/frontend/src/components/CryptoSection.jsx`
- `/app/frontend/src/components/TradeGPTChat.jsx`
- `/app/frontend/src/hooks/useToast.js`
- `/app/frontend/src/services/auth.js`
- `/app/frontend/src/services/broker.js`
- `/app/frontend/src/services/chat.js`
- `/app/frontend/src/services/company_research.js`
- `/app/frontend/src/services/crypto.js`
- `/app/frontend/src/services/digest.js`
- `/app/frontend/src/services/file_upload.js`
- `/app/frontend/src/services/market_data.js`
- `/app/frontend/src/services/market_prediction.js`
- `/app/frontend/src/services/push_notifications.js`
- `/app/frontend/src/services/referral.js`
- `/app/frontend/src/services/stripe.js`
- `/app/frontend/src/services/trading.js`
- `/app/frontend/src/services/user.js`
- `/app/frontend/src/services/workspace.js`
- `/app/frontend/src/styles/globals.css`
- `/app/frontend/vite.config.js`


---

## ▶️ Next steps to produce a diff you can work with

1. **Click** Admin → Developer Tools → *Download Codebase* (or `GET /api/admin/download/codebase-txt`).
2. You'll get `RISEDUAL_AI_Codebase.txt` — the exact same format as your PDF.
3. Convert the PDF to text (any PDF→text tool: `pdftotext`, macOS Preview → Export as Text, etc.).
4. Diff the two files in VS Code (`Ctrl+Shift+P` → *File: Compare Active File With…*), Beyond Compare, Kaleidoscope, or `diff -u old.txt new.txt | less`.
5. Focus your review on the 73 COMMON files above — those are the ones where you'll find the substantive changes. The 639 NEW files are additive.

This markdown (`/app/memory/CHANGES_SINCE_PDF.md`) is your index. Keep it alongside the diff.
