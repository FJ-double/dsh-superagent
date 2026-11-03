# 工作台 WebUI 镜像（纯标准库，无第三方依赖，构建极快）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    WORKBENCH_CONFIG=/app/config.toml

WORKDIR /app
COPY pyproject.toml ./
COPY workbench ./workbench

RUN useradd -r -u 10001 -m workbench \
 && mkdir -p /app/.data && chown -R workbench:workbench /app
USER workbench

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/',timeout=3)"

# 口令通过环境变量给，不要把口令写进镜像
CMD ["python", "-m", "workbench.cli", "serve", "--host", "0.0.0.0", "--port", "8787"]
