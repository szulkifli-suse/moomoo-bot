# moomoo-bot

Build prompt for a six-agent US tech swing-trading bot that runs on Hermes and trades through moomoo.

- [hermes-trading-desk-prompt.md](hermes-trading-desk-prompt.md): copy everything below the line into Hermes.

## Summary

- Stocks: S&P 500 technology names only, held for days
- Capital: USD 1,000, cash only, fractional shares
- Agents: Scout → Filter → Analyst (AI) → Risk → Executor, with a Logger recording every step
- Exits: -8% stop, a 5% trailing stop once a position is up 15%, and a 20-day time limit
- Safety: shadow run first, `LIVE_TRADING` flag, `STOP` kill switch, -3% daily pause, -15% drawdown halt

This trades real money. It can lose money. Use at your own risk.
