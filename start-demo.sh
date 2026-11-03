#!/usr/bin/env bash
set -euo pipefail
roomcraft_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$roomcraft_root"
demo_python="$roomcraft_root/.venv/bin/python"
editor_dir="$roomcraft_root/runs/open-source-demo/vendor/floorplan-editor"
if [[ ! -x "$demo_python" || ! -f web/demo/data/edited/model.glb || ! -d "$editor_dir" ]]; then
  echo '请先按 README 完成依赖安装和案例生成。' >&2
  exit 1
fi
demo_web_pid=''
demo_editor_pid=''
cleanup() {
  [[ -z "$demo_web_pid" ]] || kill "$demo_web_pid" 2>/dev/null || true
  [[ -z "$demo_editor_pid" ]] || kill "$demo_editor_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
"$demo_python" -m http.server 53210 --bind 127.0.0.1 --directory web &
demo_web_pid=$!
PYTHONPATH="$editor_dir" "$demo_python" -m floorplan_editor serve runs/open-source-demo/editor --port 53212 --no-browser &
demo_editor_pid=$!
echo '案例：http://127.0.0.1:53210/demo/'
echo '校对：http://127.0.0.1:53212/  · Ctrl+C 关闭服务'
wait "$demo_web_pid" "$demo_editor_pid"
