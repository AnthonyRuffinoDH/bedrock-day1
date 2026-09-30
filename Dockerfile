FROM python:3.13-slim

WORKDIR /app

COPY packages/herocore-bridge packages/herocore-bridge
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py generate_curl.py mcp_tools.json* ./

CMD ["python", "bot.py"]
