# Ouro Harvest ($oh) Auto-Player

## What it does
Runs a standalone session of $oh:
- Select user
- Send `$oh`
- Parse the 5x5 grid
- Choose clicks with EV + lookahead
- Log results to `logs/OuroHarvest.json`

## Run
```bat
run_oh.bat
```

Or:
```bash
python -m mudae.cli.oh
```

## Configuration
Copy `config/oh_config.example.json` to `config/oh_config.json`, then edit your local copy:
- `emoji_map`: map emoji names to color keys
- `values`: base values per color
- `expected_values`: overrides for special colors (LIGHT/DARK/HIDDEN). Use `null` to auto-calc.
- `reveal_counts`: extra tiles revealed by BLUE/TEAL
- `prior_weights`: starting weights for color priors
- `monte_carlo_samples`: sampling depth for reveal EV
- `time_limit_sec`: fallback time limit

Learned priors are stored in the local runtime file `config/oh_stats.json`, which is intentionally not tracked in git.
