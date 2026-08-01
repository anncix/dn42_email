FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/

# 创建数据目录
RUN mkdir -p /data

# 环境变量
ENV LIGHTMAIL_DB=/data/lightmail.db
ENV LIGHTMAIL_DOMAIN=example.dn42

# 暴露端口
EXPOSE 8025

# 启动命令
CMD ["python", "app/main.py"]
