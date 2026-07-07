FROM pandoc/typst:latest

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    py3-gunicorn \
    font-noto-cjk

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py .
COPY templates/ templates/

RUN mkdir -p /app/projects /app/output

ENTRYPOINT []
EXPOSE 5000
CMD ["python3", "app.py"]
