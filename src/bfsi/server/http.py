"""Local-only API, resumable jobs and structured result poster downloads."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import urlsplit, parse_qs
import webbrowser
from .jobs import APP, RUNS, SUCCESS, Jobs, get_run, read_status
from ..config import WEB_DIST, DEMO_PAYLOAD

JOBS = None
POSTER_LOCK = threading.Lock()

class Handler(SimpleHTTPRequestHandler):
    def local_host(self):
        hosts = self.headers.get_all('Host', [])
        allowed = (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self.json({'error': '仅允许本机地址访问'}, 403)
            return False
        return True

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def json(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def result(self, run_id):
        status=read_status(run_id)
        if status['state'] not in SUCCESS:
            raise RuntimeError('任务尚未成功完成，不能读取或导出结果。')
        payload=json.loads((get_run(run_id) / 'payload.json').read_text())
        if payload.get('analysis',{}).get('run_id')!=run_id or payload.get('video',{}).get('video_bvid')!=status.get('bvid'):
            raise ValueError('结果的视频或任务标识不匹配，拒绝显示和导出。')
        return payload

    def do_GET(self):
        if not self.local_host(): return
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == '/api/health':
                return self.json({'ok': True, 'application': 'BFSI', 'version': '0.1.0', 'capabilities': ['full', 'resume', 'poster'], 'active_run': JOBS.active})
            if path == '/api/latest':
                p = APP / 'latest.json'
                return self.json(json.loads(p.read_text()) if p.exists() else {'run_id': None})
            if path in ('/api/sample', '/api/demo'):
                return self.json(json.loads(DEMO_PAYLOAD.read_text(encoding='utf-8')))
            if path == '/api/runs':
                items = []
                for p in RUNS.glob('*/status.json'):
                    try: items.append(read_status(p.parent.name))
                    except (ValueError, OSError, KeyError): continue
                return self.json(sorted(items, key=lambda s: s.get('created_at', ''), reverse=True))
            match = re.fullmatch(r'/api/runs/([^/]+)(/result|/poster)?', path)
            if match:
                run_id, action = match.groups()
                if action == '/result': return self.json(self.result(run_id))
                if action == '/poster':
                    payload = self.result(run_id)
                    # Revalidate the cached artifact against this exact immutable payload.
                    from ..reporting.poster import export_poster
                    with POSTER_LOCK: artifact = export_poster(payload, get_run(run_id))
                    data = (get_run(run_id) / 'exports/result_poster.png').read_bytes()
                    filename = f"BFSI_{payload['video'].get('video_bvid') or run_id}_{run_id}.png"
                    disposition = 'attachment' if parse_qs(url.query).get('download') == ['1'] else 'inline'
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Disposition', f'{disposition}; filename="{filename}"')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers(); self.wfile.write(data)
                    return
                return self.json(read_status(run_id))
            if path.startswith('/api/'): return self.json({'error': '接口不存在'}, 404)
            if '..' in path.split('/'): return self.json({'error': '无效路径'}, 400)
            return super().do_GET()
        except FileNotFoundError: return self.json({'error': '任务或结果不存在'}, 404)
        except RuntimeError as e: return self.json({'error': str(e)}, 409)
        except (ValueError, KeyError) as e: return self.json({'error': str(e)}, 400)
        except Exception as e: return self.json({'error': f'读取失败：{e}'}, 500)

    def do_POST(self):
        if not self.local_host(): return
        path = urlsplit(self.path).path
        match = re.fullmatch(r'/api/runs/([^/]+)/(pause|resume|export)', path)
        if path != '/api/analyze' and not match: return self.json({'error': '接口不存在'}, 404)
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.json({'error': '仅允许本地 Dashboard 提交任务'}, 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site': return self.json({'error': '拒绝跨站任务请求'}, 403)
        try:
            if self.headers.get_content_type() != 'application/json': raise ValueError('请发送 JSON 请求')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096: raise ValueError('请求大小不正确')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError('请发送 JSON 对象')
            if match:
                run_id, action = match.groups()
                if action == 'pause': return self.json(JOBS.pause(run_id), 202)
                if action == 'resume': return self.json(JOBS.resume(run_id), 202)
                payload = self.result(run_id)
                from ..reporting.poster import export_poster
                with POSTER_LOCK: artifact = export_poster(payload, get_run(run_id))
                return self.json({**artifact, 'preview_url': f'/api/runs/{run_id}/poster', 'download_url': f'/api/runs/{run_id}/poster?download=1'})
            if not isinstance(body.get('input'), str): raise ValueError('请提供 BV 号或视频链接')
            return self.json(JOBS.submit(body['input'], body.get('limit', 100), body.get('mode', 'quick')), 202)
        except FileNotFoundError: return self.json({'error': '任务或结果不存在'}, 404)
        except RuntimeError as e: return self.json({'error': str(e)}, 409)
        except (ValueError, TypeError) as e: return self.json({'error': str(e)}, 400)
        except Exception as e: return self.json({'error': f'操作失败：{e}'}, 500)

    def do_HEAD(self):
        if self.local_host(): return super().do_HEAD()


def main():
    global JOBS
    parser = argparse.ArgumentParser(description='BFSI 本地视频分析 Dashboard')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    build = WEB_DIST
    if not (build / 'index.html').exists(): raise SystemExit('Dashboard 构建不存在，请在仓库根目录运行（或设置 BFSI_PROJECT_ROOT），并先在 web/ 运行 pnpm run build。')
    try: httpd = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(build)))
    except OSError as e: raise SystemExit(f'端口{args.port}已被占用，请关闭旧的 Dashboard 启动窗口后重试。{e}')
    JOBS = Jobs()
    address = f'http://127.0.0.1:{args.port}/'
    print(f'BFSI 已启动：{address}\n分析时请保留本窗口。Ctrl+C保存当前任务检查点后退出；重启后可继续。', flush=True)
    if not args.no_open: threading.Timer(.4, lambda: webbrowser.open(address)).start()
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        httpd.server_close()
        if JOBS.active: print('正在保存检查点并等待在途批次结束…', flush=True)
        JOBS.shutdown()

if __name__ == '__main__': main()
