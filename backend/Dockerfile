FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .
RUN chmod +x /app/start.sh

EXPOSE 8000

CMD ["/app/start.sh"]
