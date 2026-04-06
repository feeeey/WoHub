FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

ENV TZ=Asia/Shanghai
ENV DEBIAN_FRONTEND=noninteractive
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . ./
RUN mkdir -p output cookies

EXPOSE 5000
CMD ["python", "main.py"]
