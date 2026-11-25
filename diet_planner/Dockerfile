# Obraz wieloarchitekturowy z Docker Hub (amd64 i aarch64) – Supervisor wybiera architekturę sam,
# więc nie potrzeba build.yaml ani obrazu z ghcr.io.
FROM python:3.12-alpine

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
