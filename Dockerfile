# A股信号灯 Dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY *.py ./
COPY models/ ./models/
COPY data_provider/ ./data_provider/
COPY indicators/ ./indicators/
COPY strategy/ ./strategy/
COPY trading/ ./trading/
COPY monitor/ ./monitor/
COPY notification/ ./notification/
COPY storage/ ./storage/
COPY web/ ./web/
COPY web/templates/ ./web/templates/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY logs/ ./logs/

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 环境变量默认值
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

# 暴露端口
EXPOSE 8080

# 默认命令: 启动Web服务
CMD ["python", "web/app.py"]
