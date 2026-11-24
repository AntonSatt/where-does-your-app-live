FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim AS runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
# The service never runs pip, setuptools or wheel at run time. Removing them drops the
# base image's vulnerable copies (setuptools, and the urllib3/msgpack that pip vendors).
RUN python -m pip uninstall -y pip setuptools wheel
USER 10001
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "", "--port", "8000"]
