FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY jarvy ./jarvy
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 jarvy && mkdir -p /data && chown jarvy:jarvy /data
USER jarvy
ENV JARVY_DATA_DIR=/data JARVY_HOST=0.0.0.0
EXPOSE 8000
CMD ["jarvy", "serve"]
