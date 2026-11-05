FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .
EXPOSE 8000
