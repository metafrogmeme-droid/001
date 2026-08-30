# RUNECLAW component & data-flow inventory

Commit: `fcbb63295b91528116b74d3c733e3c991f71a9e1`  
Branch: `claude/runeclaw-full-audit-8ks0k3`

## 1. Express route surface (app/routes/)

90 route modules, 271 handlers.

| module | handlers | auth symbol present |
|---|---|---|
| agent_record.js | 1 | NO |
| agents.js | 2 | yes |
| airdrops.js | 2 | yes |
| alerts.js | 3 | yes |
| allowances.js | 2 | NO |
| arena.js | 24 | yes |
| authority.js | 5 | yes |
| botstrategy.js | 3 | yes |
| call.js | 2 | NO |
| chat.js | 2 | yes |
| command.js | 1 | yes |
| contract.js | 2 | yes |
| controls.js | 9 | yes |
| copy.js | 4 | yes |
| counterparty.js | 1 | yes |
| credentials.js | 3 | yes |
| cross_yield.js | 1 | yes |
| dapps.js | 1 | NO |
| defi.js | 1 | yes |
| discovery.js | 4 | NO |
| duel.js | 4 | yes |
| embed.js | 2 | yes |
| exposure.js | 1 | yes |
| farcaster_auth.js | 2 | yes |
| feed.js | 1 | NO |
| frame.js | 7 | NO |
| gas.js | 1 | yes |
| guardian.js | 3 | yes |
| guardian_readiness.js | 1 | yes |
| guardian_review.js | 2 | yes |
| holdings.js | 1 | yes |
| idleyield.js | 1 | yes |
| ingest.js | 3 | yes |
| insight.js | 2 | NO |
| lab.js | 3 | yes |
| leaderboard.js | 3 | yes |
| learn.js | 10 | yes |
| letter.js | 3 | yes |
| llm.js | 4 | yes |
| macro.js | 1 | NO |
| market.js | 13 | yes |
| mcp.js | 3 | yes |
| meme.js | 1 | yes |
| miniapp.js | 1 | yes |
| networth.js | 1 | yes |
| news.js | 4 | yes |
| nft.js | 4 | yes |
| patterns.js | 2 | NO |
| portfolio.js | 2 | yes |
| positions.js | 1 | yes |
| profile.js | 2 | yes |
| proofofpnl.js | 1 | yes |
| public_agent.js | 1 | NO |
| public_agent_identity.js | 2 | NO |
| public_chat.js | 1 | yes |
| public_duel.js | 2 | NO |
| public_flight.js | 2 | NO |
| public_invite.js | 1 | NO |
| public_leaderboard.js | 1 | NO |
| public_letter.js | 3 | NO |
| public_proofofpnl.js | 1 | NO |
| public_status.js | 2 | NO |
| public_strategies.js | 1 | NO |
| public_user_strategies.js | 2 | NO |
| push.js | 3 | yes |
| replay.js | 1 | yes |
| reports.js | 2 | yes |
| reputation.js | 1 | yes |
| research.js | 2 | yes |
| roots.js | 4 | yes |
| sentry.js | 1 | yes |
| share.js | 1 | yes |
| signals.js | 3 | yes |
| since.js | 1 | yes |
| spot.js | 2 | NO |
| staking.js | 2 | yes |
| strategy_templates.js | 1 | NO |
| stream.js | 1 | NO |
| sync.js | 26 | yes |
| tax.js | 2 | yes |
| today.js | 1 | NO |
| tool8257.js | 4 | yes |
| track.js | 2 | NO |
| trades.js | 7 | yes |
| user_strategies.js | 6 | yes |
| wallet.js | 1 | yes |
| watchlist.js | 2 | yes |
| web3.js | 3 | yes |
| web3_execute.js | 6 | yes |
| webtrade.js | 4 | yes |

## 2. Telegram command surface

138 `_cmd_*` handlers in telegram_handler.py.

```
/accounts, /agent, /alpha, /analyze, /anchor, /approvals, /approve, /arb, /arena, /attribution, /audit, /autoconfirm, /backtest, /backup, /broadcast, /buy, /calibration, /channel, /classpf, /close_all, /compliance, /connect, /costs, /crossasset, /daily_report, /dashboard, /deepscan, /dip, /disconnect, /drawdownlimit, /duel, /emergency_stop, /enforcing, /equitycurve, /escape, /eventrisk, /exchange, /exposure, /flags, /forcescan, /fullscan, /funding, /fundingscan, /gates, /golive, /grant_live, /guardian, /halt, /health, /help, /holdtime, /idleyield, /intraday, /journal, /lang, /latest_signal, /leaderboard, /learn, /leverage, /linkwallet, /livebalance, /liveclose, /livepositions, /llmab, /llmreset, /llmstatus, /llmtiers, /macro, /memeplan, /mode, /momentum, /montecarlo, /mynotes, /mystrategy, /networth, /news, /open_positions, /optimize, /orders, /paper, /parity, /patterns, /pause, /performance, /playbook, /policy, /portfolio, /proposals, /readiness, /rejected, /research, /reset, /resume, /revoke, /revoke_live, /risk, /run, /rwa, /scalp, /scan, /sell, /sentinel, /session, /set_tier, /setcap, /setexchange, /setgateway, /setllm, /settier, /shadow, /share, /signals, /slippage, /squeeze, /stake, /start, /status, /stockscan, /strategy, /sweep, /swing, /token, /trade, /twin, /ultra, /unstake, /users, /vault, /venue, /venues, /version, /walkforward, /watch, /weblive, /whynot, /xray, /yield, /zones
```

Registration site: `bot/skills/telegram_handler.py:1041` (`app.add_handler(CommandHandler(cmd, handler))`).

## 3. Python HTTP surfaces

- `api_bridge.py` — 0 aiohttp, 19 fastapi/decorator routes
- `dashboard_api.py` — 0 aiohttp, 0 fastapi/decorator routes
- `bot/web/user_gateway.py` — 59 aiohttp, 0 fastapi/decorator routes
- `bot/web/dashboard_server.py` — 10 aiohttp, 0 fastapi/decorator routes
- `bot/api/auth_routes.py` — 0 aiohttp, 7 fastapi/decorator routes
- `bot/api/lab.py` — 0 aiohttp, 3 fastapi/decorator routes
- `bot/web/web_live_admin.py` — 0 aiohttp, 0 fastapi/decorator routes

## 4. AI / LLM components

- `bot/llm/__init__.py` (33 lines)
- `bot/llm/key_health.py` (204 lines)
- `bot/llm/provider.py` (1506 lines)
- `bot/llm/shadow_eval.py` (281 lines)
- `bot/llm/usage.py` (121 lines)
- `bot/mcp/__init__.py` (1 lines)
- `bot/mcp/server.py` (541 lines)

Model identifiers referenced in `bot/llm/provider.py`: `claude-fable-5`, `claude-haiku-4-5-20251001`, `claude-opus-4-8`, `claude-sonnet-5`, `gpt-4-turbo`, `gpt-4o`, `gpt-4o-mini`, `llama3`

## 5. Data stores

- `bot/api/token_store.py`
- `bot/core/user_leverage_store.py`
- `bot/core/user_memory_store.py`
- `bot/core/user_profile_store.py`
- `bot/core/user_strategy_store.py`
- `bot/guardian/user_authority_store.py`
- `bot/learning/store.py`
- `bot/nlp/conversation_store.py`
- `bot/utils/user_store.py`
- `app/db.js` (3082 lines)
- `bot/db/__init__.py`
- `bot/db/models.py`

## 6. External integrations (from code, not docs)

- `github.com` (102 refs)
- `t.me` (24 refs)
- `api.bitget.com` (13 refs)
- `mainnet.base.org` (10 refs)
- `pmvc58g2.mule.page` (10 refs)
- `www.w3.org` (8 refs)
- `www.humanoid-traders.com` (8 refs)
- `basescan.org` (7 refs)
- `example.test` (7 refs)
- `telegram.org` (6 refs)
- `x.example` (6 refs)
- `app.test` (5 refs)
- `api.dexscreener.com` (4 refs)
- `my-cnd-server.com` (4 refs)
- `www.sandbox.game` (4 refs)
- `otherside.xyz` (4 refs)
- `runeclaw.test` (4 refs)
- `push.example` (4 refs)
- `a.example` (4 refs)
- `discord.com` (4 refs)
- `frames.example` (4 refs)
- `x.io` (4 refs)
- `base-rpc.publicnode.com` (4 refs)
- `etherscan.io` (3 refs)
- `api.openai.com` (3 refs)
- `console.groq.com` (3 refs)
- `openrouter.ai` (3 refs)
- `api.bybit.com` (3 refs)
- `humanoid-traders.com` (3 refs)
- `b.example` (3 refs)
- `runeclaw.example` (3 refs)
- `x.test` (3 refs)
- `ethereum-rpc.publicnode.com` (3 refs)
- `play.decentraland.org` (3 refs)
- `sepolia.basescan.org` (2 refs)