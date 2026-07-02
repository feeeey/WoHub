FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

ENV TZ=Asia/Shanghai
ENV DEBIAN_FRONTEND=noninteractive
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# Override on networks where pypi.org is unreachable (e.g. China mirrors)
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . ./
RUN mkdir -p output cookies

EXPOSE 5000
CMD ["python", "main.py"]
