---
name: portfolio-rebalancer
description: "Automated multi-asset portfolio rebalancing engine for AgentOS: calculate allocation drift against target weights, generate fee-minimized buy/sell rebalancing orders, simulate dry-run executions, and output executive portfolio reports. Use when: (1) analyzing portfolio drift from target percentages, (2) constructing rebalancing trade orders, (3) dry-run simulating rebalancing actions, or (4) checking whether a portfolio needs rebalancing based on drift thresholds."
argument-hint: "<drift|plan|simulate|selftest> --portfolio <file.json> --targets <file.json> [--prices <prices.json>] [--tolerance <pct>] [--min-trade <usd>] [--fee-bps <bps>] [--cash-buffer <pct>] [--dry-run] [--json]"
triggers: [portfolio rebalance, rebalancer, portfolio drift, asset allocation, target weights, rebalance plan, dry-run rebalance, crypto rebalance]
provenance:
  origin: agentos-original
  license: MIT
  maintained_by: AgentOS
metadata:
  agentos:
    emoji: "⚖️"
    category: crypto
    risk: low
    capabilities: [network-read]
    requires:
      anyBins: ["python3", "python"]
---

# Portfolio Rebalancer Skill

Automated multi-asset portfolio rebalancing engine for AgentOS. Calculates allocation drift, generates optimal buy/sell rebalancing orders with SELLs-first liquidity sequencing, and provides pure dry-run simulation capabilities.

## Safety & Design Principles
- **Safety-First Defaults:** All CLI execution paths default to read-only simulation (`--dry-run`).
- **Zero Hardcoded Network Dependencies:** Fully decoupled from live APIs. Accepts portfolio states and market prices as standard JSON inputs.
- **Structured JSON Output:** Pass `--json` to stream clean, typed JSON dictionaries directly on stdout for AgentOS runtime and sub-agent ingestion.

## Script Path

Set once per shell:

```bash
S="{baseDir}/scripts"
```

## Quick Reference

| Command | Action | Key Options |
|---|---|---|
| `python3 $S/rebalancer.py drift` | Calculate allocation drift vs target weights | `--portfolio <file> --targets <file> [--tolerance 5.0] [--json]` |
| `python3 $S/rebalancer.py plan` | Generate actionable buy/sell rebalance orders | `--portfolio <file> --targets <file> [--prices <file>] [--min-trade 10.0] [--fee-bps 10.0] [--json]` |
| `python3 $S/rebalancer.py simulate` | Dry-run simulate rebalance execution | `--portfolio <file> --targets <file> [--prices <file>] [--tolerance 5.0] [--json]` |
| `python3 $S/rebalancer.py selftest` | Run built-in engine verification suite | None |

---

## Input Formats

### 1. Portfolio JSON (`portfolio.json`)

```json
{
  "cash_usd": "5000.00",
  "positions": [
    {
      "symbol": "BTC",
      "amount": "1.0",
      "price_usd": "60000.00"
    },
    {
      "symbol": "ETH",
      "amount": "10.0",
      "price_usd": "3000.00"
    },
    {
      "symbol": "SOL",
      "amount": "50.0",
      "price_usd": "200.00"
    }
  ]
}
```

### 2. Target Allocation JSON (`targets.json`)

```json
{
  "targets": [
    {
      "symbol": "BTC",
      "target_weight": "0.40"
    },
    {
      "symbol": "ETH",
      "target_weight": "0.40"
    },
    {
      "symbol": "SOL",
      "target_weight": "0.20"
    }
  ]
}
```

*Note: Target weights are automatically normalized to 100% if their sum differs slightly from 1.0.*

---

## Usage Guide & Workflows

### 1. Check Allocation Drift

Run the `drift` subcommand to evaluate whether any asset exceeds the configured tolerance threshold (default: ±5.0% relative drift):

```bash
python3 "$S/rebalancer.py" drift --portfolio portfolio.json --targets targets.json --tolerance 5.0
```

To emit machine-readable JSON for automated agent processing:

```bash
python3 "$S/rebalancer.py" drift --portfolio portfolio.json --targets targets.json --json
```

### 2. Generate Rebalancing Orders

Generate minimum-transaction rebalancing orders:

```bash
python3 "$S/rebalancer.py" plan \
  --portfolio portfolio.json \
  --targets targets.json \
  --tolerance 5.0 \
  --min-trade 25.0 \
  --fee-bps 10.0
```

- **Liquidity Sequencing:** Orders automatically place `SELL` orders first to raise cash before executing `BUY` orders.
- **Dust Protection:** The `--min-trade` option skips micro-trades whose transaction costs would outweigh rebalancing benefits.
- **Cash Buffer:** Use `--cash-buffer <pct>` to keep a fixed percentage of total portfolio value in unallocated cash/stablecoins.

### 3. Dry-Run Simulation

Simulate pre-rebalance vs post-rebalance max drift and portfolio turnover before executing live trades:

```bash
python3 "$S/rebalancer.py" simulate --portfolio portfolio.json --targets targets.json --json
```

### 4. Verification

Run the self-test suite:

```bash
python3 "$S/rebalancer.py" selftest
```
