FROM python:3.13.7-slim

WORKDIR /app

COPY pyproject.toml poetry.lock README.md LICENSE ./

RUN mkdir notion_automation \
 && touch notion_automation/__init__.py \
 && pip install poetry \
 && poetry config virtualenvs.create false \
 && poetry install

COPY ./notion_automation ./notion_automation
COPY ./entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]