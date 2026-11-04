FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy every public top-level module used by analysis and paper trading.
COPY *.py ./
COPY scripts/run_paper_rebalance.py ./scripts/

# Alpaca credentials are injected at runtime and never baked into the image.
CMD ["python", "scripts/run_paper_rebalance.py"]
