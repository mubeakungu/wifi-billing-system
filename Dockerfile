# WiFi billing system: portal, dashboard, API and RADIUS server in one container
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund --fetch-retries=5 --fetch-timeout=120000
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt
COPY backend/ ./
COPY --from=frontend /app/frontend/build /app/frontend/build
ENV FLASK_ENV=production PORT=5000 RADIUS_ENABLED=1 PYTHONUNBUFFERED=1
EXPOSE 5000/tcp 1812/udp 1813/udp
# Create tables and the first admin if needed, then start web + RADIUS
CMD ["sh", "-c", "python init_db.py && python billing_system_backend.py"]
