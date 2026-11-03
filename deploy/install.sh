#!/usr/bin/env bash
# 裸机安装（Linux）：把工作台装成 systemd 服务。仅用系统 python3，不需要 pip 装任何东西。
set -euo pipefail

TARGET=${TARGET:-/opt/workbench}
SERVICE_NAME=workbench
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 检查 python =="
PY=$(command -v python3 || true)
[[ -n "$PY" ]] || { echo "没有 python3，请先装 python3.11+"; exit 1; }
"$PY" - <<'PYCHECK'
import sys
assert sys.version_info >= (3, 11), f"需要 Python 3.11+，当前 {sys.version.split()[0]}"
print("python ok:", sys.version.split()[0])
PYCHECK

echo "== 建目录与用户 =="
sudo mkdir -p "$TARGET"
if ! id workbench >/dev/null 2>&1; then
  sudo useradd -r -s /usr/sbin/nologin workbench || sudo adduser -r -s /sbin/nologin workbench
fi

echo "== 拷代码 =="
sudo rsync -a --delete "$SRC/workbench" "$SRC/pyproject.toml" "$TARGET/"
if [[ ! -f "$TARGET/config.toml" ]]; then
  sudo cp "$SRC/config.example.toml" "$TARGET/config.toml"
  echo "已生成 $TARGET/config.toml，请填 [vault.*] 与 [webui] token"
fi

echo "== 数据目录权限 =="
sudo mkdir -p "$TARGET/.data"
sudo chown -R workbench:workbench "$TARGET/.data"

echo "== 口令文件 =="
if [[ ! -f /etc/workbench.env ]]; then
  TOKEN=$("$PY" -c "import secrets;print(secrets.token_urlsafe(18))")
  echo "WB_WEB_TOKEN=$TOKEN" | sudo tee /etc/workbench.env >/dev/null
  sudo chmod 600 /etc/workbench.env
  echo "已生成口令，写在 /etc/workbench.env；页面首次打开时输入："
  echo "  $TOKEN"
fi

echo "== 安装 systemd 服务 =="
sudo cp "$SRC/deploy/workbench.service" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sleep 1
sudo systemctl --no-pager --lines=5 status "$SERVICE_NAME" || true

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "完成。浏览器打开： http://${IP:-<本机IP>}:8787/"
echo "日志：journalctl -u $SERVICE_NAME -f"
