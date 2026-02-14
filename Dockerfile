FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set python path to current directory so app modules can be found
ENV PYTHONPATH=/app

# Default command (can be overridden by docker-compose)
CMD ["python", "app/bot.py"]
