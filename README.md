# BusTrust: Dublin bus reliability predictor

An unofficial Dublin take on Mumbai's m-Indicator: one fast, free app for buses (Luas,
DART and bikes follow in 2027). Its standout feature is honesty about **how far to trust
each countdown**.

For every bus due at a stop, BusTrust shows:

1. The **official** NTA prediction, unaltered and labelled as official.
2. A **likely arrival range** built to hold 80% of the time.
3. A **no-show risk** for that trip (the "ghost bus" problem).
4. The route's **reliability this week**.

> Status: under construction. See [docs/](docs/) for the architecture and build plan.

## Layout

```
ingest/     poller, schedule loader, weather fetcher, feed simulator
pipeline/   arrival reconstruction, route scores, features, data checks
ml/         training, evaluation, conformal calibration, scoring job
api/        FastAPI service
web/        Next.js PWA
infra/      docker-compose.yml, Caddyfile, cron
notebooks/  exploration only; reusable code moves into a package
tests/      unit and data-quality tests
docs/       architecture, data dictionary, model card
```

## Local setup

```bash
uv sync                           # or: uv pip install -e . --group dev
./scripts/fix_macos_libomp.sh     # macOS without Homebrew: lets LightGBM find OpenMP
cp .env.example .env
```

Data: National Transport Authority, provided as is under CC BY 4.0. BusTrust is not
affiliated with TFI, the NTA or any operator.
