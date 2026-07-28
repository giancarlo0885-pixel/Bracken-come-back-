FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -m ensurepip --upgrade \
    && python -m pip install --upgrade pip setuptools wheel

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["python", "start_web.py"]
