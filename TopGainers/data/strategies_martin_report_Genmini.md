# Martin Strategies Backtest Report

Dataset: gus_stats_ibkr.tbs
Number of records: 304

## Strategy Performance

| Strategy   | Description                  |   Win Rate |   Max Drawdown |   Win Ratio |   Sharpe Ratio |
|:-----------|:-----------------------------|-----------:|---------------:|------------:|---------------:|
| S1         | E:+10% SL:40% TP:25% EOD     |      72.77 |         -71.43 |        0.67 |           4.35 |
| S2         | E:+10% SL:40% TP:25% EOD     |      72.77 |         -71.43 |        0.67 |           4.35 |
| S3         | E:+10% SL:50% TP:25% EOD     |      74.26 |         -58.77 |        0.60 |           4.10 |
| S4         | E:+10% SL:50% TP:25% 13:00h  |      74.26 |         -52.51 |        0.62 |           4.30 |
| S5         | E:9:45h SL:30% TP:30% 13:00h |      73.68 |         -70.23 |        0.69 |           4.93 |
| S6         | E:0% SL:40% TP:30% 13:00h    |      73.93 |         -50.68 |        0.61 |           4.18 |
| S7         | E:0% SL:60% TP:15% EOD       |      79.87 |         -40.14 |        0.39 |           3.11 |
| S8         | E:0% SL:60% TP:55% 12:45h    |      75.58 |         -37.29 |        0.60 |           4.15 |
| S9         | E:+2% SL:45% TP:15% 15:55h   |      81.75 |         -39.80 |        0.41 |           4.47 |
| S10        | E:0% SL:60% TP:45% 12:45h    |      75.58 |         -37.29 |        0.59 |           4.09 |
| S11        | E:0% SL:60% TP:40% 12:45h    |      75.91 |         -37.05 |        0.58 |           4.11 |
| S12        | E:0% SL:60% TP:35% 12:45h    |      75.91 |         -36.88 |        0.58 |           4.11 |
| S13        | E:0% SL:50% TP:55% 12:45h    |      74.92 |         -44.90 |        0.62 |           4.28 |

### Definitions
- **Win Rate**: Percentage of trades with PnL > 0.
- **Max Drawdown**: Maximum peak-to-valley percentage decline in the equity curve (compounded daily returns).
- **Win Ratio**: Average Win % / Average Loss %.
- **Sharpe Ratio**: Annualized Sharpe Ratio (assuming 252 trading days) based on daily returns.
