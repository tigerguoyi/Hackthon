#!/bin/zsh
set -eu
export PYTHONDONTWRITEBYTECODE=1
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 '请先安装 Python 3.9 或更新版本。'
  read -r '?按回车关闭窗口。'
  exit 1
fi
exec python3 - <<'PY'
import errno
import os
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

try:
    if sys.version_info < (3, 9):
        raise RuntimeError('需要 Python 3.9 或更新版本')
    from server import Handler
    first = int(os.environ.get('TOUCHLINE_PORT', '5173'))
    if not 1024 <= first <= 65505:
        raise ValueError('TOUCHLINE_PORT 须在 1024–65505 之间')
    for port in range(first, first+31):
        try:
            http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
    else:
        raise RuntimeError('连续 31 个端口被占用，请先停止旧服务')
except Exception as error:
    print(f'启动失败：{error}', flush=True)
    input('按回车关闭窗口。')
    raise SystemExit(1)

url = f'http://127.0.0.1:{port}/team-integration.html'
print(f'Ne_Generator 本地演示：{url}', flush=True)
print('A/B/C 固定案例与新版 40 场决策快照；不调用外部模型。', flush=True)
print('保持窗口打开，按 Control+C 停止。', flush=True)
if os.environ.get('TOUCHLINE_NO_BROWSER') != '1':
    opener = threading.Timer(.5, webbrowser.open, args=(url,))
    opener.daemon = True
    opener.start()
try:
    with http:
        http.serve_forever()
except KeyboardInterrupt:
    print('\n演示服务已停止。', flush=True)
PY
