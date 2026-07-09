# Plataforma NetUs Ads Cockpit — imagen para EasyPanel
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pg8000
COPY scripts/ ./scripts/
ENV PORT=8080
EXPOSE 8080
CMD ["python", "scripts/server.py"]
