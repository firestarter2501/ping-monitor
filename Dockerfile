FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables with defaults
ENV PING_MONITOR_CONFIG=/app/config.json \
    PING_MONITOR_PORT=8080

# Install required packages
# iputils-ping for ping command
# ca-certificates for HTTPS connections (required for Discord webhook)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY ping_monitor.py .
COPY config.json .
COPY templates/ templates/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash monitor && \
    chown -R monitor:monitor /app

USER monitor

# Expose port (default, can be overridden)
EXPOSE ${PING_MONITOR_PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; import os; port = int(os.environ.get('PING_MONITOR_PORT', 8080)); urllib.request.urlopen(f'http://localhost:{port}/api/status')" || exit 1

# Run the application
CMD ["python3", "ping_monitor.py"]