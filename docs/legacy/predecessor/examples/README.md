> Historical v4.1.0 reference only. Commands below require predecessor software; current users follow the [Reprovane Quick Start](https://github.com/tvquynh/reprovane/blob/main/QUICKSTART.md).

# Examples

Run these from the project root (the folder that contains `literature_pipeline.py`) after creating `config.json`:

```powershell
.\examples\run_test.ps1          # 100-paper test query (malware detection machine learning, 2020-2026)
.\examples\run_malware_q1q2.ps1  # multi-query journal Q1/Q2 run using examples\queries_example.txt
```

`queries_example.txt` holds one query per line; lines starting with `#` are comments.

`venue_tiers_example.tsv` shows the format of a venue list for `--venue-tiers` (see "Quality signals and venue tiers" in the main README). Its tiers are examples, not a recommendation; write and approve your own list.
