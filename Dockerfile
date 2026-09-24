FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -m pip install --no-cache-dir requests numpy pandas scikit-learn

COPY research_v63_cross_asset.py ./

CMD ["python", "-u", "research_v63_cross_asset.py"]
