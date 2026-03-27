FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY *.py ./
COPY config_s*.py ./
COPY dashboard.html ./

# State files and logs will be mounted as a volume
VOLUME ["/app/data"]

EXPOSE 8080 8081

# Default: live bot. Override with --paper for paper mode.
CMD ["python", "bot.py"]
