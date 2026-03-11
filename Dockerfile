FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy

WORKDIR /app

ARG APP_BUILD_SHA=unknown
ARG APP_BUILD_TIME=unknown
ARG TOP10_STRUCTURE_PARSER_VERSION=v2_strict

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_BUILD_SHA=${APP_BUILD_SHA}
ENV APP_BUILD_TIME=${APP_BUILD_TIME}
ENV TOP10_STRUCTURE_PARSER_VERSION=${TOP10_STRUCTURE_PARSER_VERSION}

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
