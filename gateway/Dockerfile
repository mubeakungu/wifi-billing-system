# Built-in hotspot gateway (see gateway.py). Build context is the repository root.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends nftables iptables dnsmasq-base conntrack iproute2 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY gateway/gateway.py ./
COPY backend/hotspot/login.html ./hotspot/login.html
ENV PYTHONUNBUFFERED=1
CMD ["python", "gateway.py"]
