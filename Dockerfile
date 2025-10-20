# Stage 1: Build stage to install dependencies
FROM python:3.12-slim as builder

WORKDIR /app

# Install poetry and export requirements
RUN pip install --no-cache-dir poetry
COPY pyproject.toml poetry.lock* ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# Install dependencies to a target directory
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Final stage
FROM python:3.12-slim

WORKDIR /app

# Create a non-root user
RUN addgroup --system app && adduser --system --group app

# Copy installed dependencies from the builder stage
COPY --from=builder /install /usr/local

# Copy application code
COPY ./app ./app

# Set ownership and switch to non-root user
RUN chown -R app:app /app
USER app

# Expose port and run the application
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
