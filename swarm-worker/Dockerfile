FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2 if needed
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run with unbuffered output so logs show up immediately in docker-compose
CMD ["python", "-u", "swarm_worker.py"]
