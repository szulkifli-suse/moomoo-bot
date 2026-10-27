# moomoo-bot

Build prompt for a six-agent US tech swing-trading bot (Claude Trading Desk) that Claude Code builds, runs as a container on the `dark-ai` Kubernetes cluster, and trades through moomoo.

- [claude-trading-desk-prompt.md](claude-trading-desk-prompt.md): copy everything below the line into Claude Code.

## Summary

- Stocks: S&P 500 technology names only, held for days
- Capital: USD 1,000, cash only, fractional shares where moomoo allows them
- Agents: Scout (scored, not all-or-nothing) → Filter → Analyst (Claude, veto model) → Risk → Executor, with a Logger recording every step
- Sizing: 20% per position, 4 positions, 80% max invested; half size and 2 positions when QQQ is below its 50-day average
- Exits: 2×ATR stop (4–10%), sell half at +2R and move stop to entry, then 2×ATR trailing stop; 20-day time limit
- Proof first: 3-year backtest, 10+ days of shadow mode, and a go-live gate (profit factor ≥ 1.3 after fees)
- Safety: `LIVE_TRADING` flag, `STOP` kill switch, -3% daily pause, -15% drawdown halt, single-replica Deployment
- Dashboard: password-protected web dashboard (overview, positions, trades, funnel, activity, backtest), read-only apart from a Halt button
- Tuning: daily funnel and near-miss report, weekly tuning suggestions; thresholds live in `config.yaml` and only change by hand

This trades real money. It can lose money. Use at your own risk.

## Running it (dark-ai)

The app is in `claude_trader/` (bot), `dashboard/` (web dashboard), `deploy/k8s/` (manifests) and `scripts/`.
It runs in namespace `claude-trading`, dashboard at https://darkloner.lepak-awan.com, starting in shadow mode.

```bash
export KUBECONFIG=~/sa_lab/harvester-cloud/projects/aws/dark-ai-kubectl.yaml
scripts/build.sh            # build and push images with BuildKit on the cluster
scripts/deploy.sh           # apply manifests; keeps the current LIVE_TRADING value
scripts/create-secret.sh    # ANTHROPIC_API_KEY etc. from your shell; prints the dashboard password
scripts/opend-login.sh      # one-time moomoo login in the OpenD console (detach: Ctrl-P Ctrl-Q)
scripts/backtest.sh         # 3-year backtest; results on the dashboard
scripts/halt.sh / resume.sh # kill switch
scripts/go-live.sh          # real orders; only after the shadow run and go-live gate
```

Settings live in `config/config.yaml` (ConfigMap `claude-config`). Fees follow moomoo Malaysia's
US-stock schedule; the round-trip fee cap is 1.5% because USD 1,000 positions cost about 1.35%.
