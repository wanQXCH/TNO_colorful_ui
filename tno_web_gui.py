#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TNO UI 换色 Mod 生成器 — Web 界面
=================================
本地 HTTP 服务（仅绑定 127.0.0.1），浏览器访问精美界面，功能与 tkinter 版一致：
统一 Mod 目录列表、取色器、压暗/压缩/并行选项、生成/仅扫描、实时进度与日志、
结果统计、preview 对照图、一键安装到 HOI4 mod 目录。

用法:
    python tno_web_gui.py            # 启动并自动打开浏览器
    python tno_web_gui.py --port 9000 --no-browser

只依赖 Python 标准库；生成核心复用 tno_color_gen.py。
"""
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import tno_color_gen as gen  # noqa: E402

HOST = '127.0.0.1'
PORT_START, PORT_END = 8765, 8795
WEB_DIR = os.path.join(BASE, 'web_gui')

JOBS = {}
JOB_LOCK = threading.Lock()
JOB_SEQ = [0]


# ---------------------------------------------------------------------------
# 作业管理
# ---------------------------------------------------------------------------

class Job(object):
    def __init__(self, kind, payload):
        with JOB_LOCK:
            JOB_SEQ[0] += 1
            self.id = 'job%d' % JOB_SEQ[0]
        self.kind = kind                    # 'generate' | 'scan'
        self.payload = payload
        self.status = 'running'             # running | done | canceled | error
        self.cancel = threading.Event()
        self.log = []                       # [(seq, text), ...]
        self.log_seq = 0
        self.progress = {'i': 0, 'n': 0, 'blue': 0}
        self.stats = None
        self.elapsed = 0.0
        self.error = None
        self.out = payload.get('out', '')
        self.preview_path = None
        self.installed_to = None
        self.started = time.time()

    def log_line(self, s):
        with JOB_LOCK:
            self.log_seq += 1
            self.log.append((self.log_seq, s))
            if len(self.log) > 5000:
                del self.log[:len(self.log) - 5000]

    def set_progress(self, i, n, blue):
        self.progress = {'i': i, 'n': n, 'blue': blue}


def _fmt_stats(stats):
    return ('统计: 扫描 %(scanned)d 个图片，将改色 %(blue)d 个，跳过 %(skipped_dir)d 个'
            '（照片 %(photo_skip)d、几乎无变化 %(tiny)d），不支持 %(unsupported)d 个'
            % stats)


def _install_copy(job, out):
    moddir = gen.find_hoi4_mod_dir()
    if not moddir:
        job.log_line('未找到 HOI4 mod 目录，跳过安装。')
        return
    import shutil
    base = os.path.basename(os.path.abspath(out))
    dst = os.path.join(moddir, base)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(out, dst)
    shutil.copy2(out + '.mod', os.path.join(moddir, base + '.mod'))
    job.installed_to = dst
    job.log_line('已复制到 %s' % dst)


def _run_job(job):
    p = job.payload
    try:
        roots = [r for r in p.get('mods', []) if os.path.isdir(r)]
        if not roots:
            raise ValueError('没有有效的 Mod 目录（请检查路径是否存在）')
        target = gen.parse_color(p.get('color', ''))
        darken = float(p.get('darken', 0.0) or 0.0)
        darken = min(1.0, max(0.0, darken))
        flag_blend = float(p.get('flag_blend', 0.4) if p.get('flag_blend') is not None else 0.4)
        flag_blend = min(1.0, max(0.0, flag_blend))
        compress = bool(p.get('compress'))
        jobs_n = int(p.get('jobs', 0) or 0)

        if job.kind == 'scan':
            params = gen.make_params(target, darken, flag_blend)
            job.log_line('扫描 %s ...' % ' + '.join(os.path.abspath(r) for r in roots))
            stats, _ = gen.scan_and_build(
                roots, params, os.path.join(roots[0], '.scan_tmp'),
                log_cb=job.log_line, dry_run=True, jobs=jobs_n,
                progress_cb=job.set_progress, cancel_event=job.cancel)
            job.stats = stats
            job.log_line(_fmt_stats(stats))
        else:
            out = p.get('out') or os.path.join(os.getcwd(), 'generated_mods',
                                               'TNO_UI_%02X%02X%02X' % target)
            mod_name = p.get('name') or 'TNO UI #%02X%02X%02X GUI' % target
            job.out = os.path.abspath(out)
            stats, _ = gen.generate_mod(
                roots, target, job.out, mod_name=mod_name, darken=darken,
                compress=compress, jobs=jobs_n, flag_blend=flag_blend,
                progress_cb=job.set_progress, log_cb=job.log_line,
                cancel_event=job.cancel)
            job.stats = stats
            job.preview_path = os.path.join(job.out, 'preview.png')
            if not job.cancel.is_set() and p.get('install'):
                _install_copy(job, job.out)
        job.status = 'canceled' if job.cancel.is_set() else 'done'
    except Exception as e:
        import traceback
        job.error = '%s\n%s' % (e, traceback.format_exc())
        job.log_line('出错: %s' % job.error)
        job.status = 'error'
    finally:
        job.elapsed = round(time.time() - job.started, 1)


def start_job(kind, payload):
    job = Job(kind, payload)
    with JOB_LOCK:
        JOBS[job.id] = job
    t = threading.Thread(target=_run_job, args=(job,), daemon=True)
    t.start()
    return job


# ---------------------------------------------------------------------------
# 系统对话框（native 目录选择 / 打开文件夹）
# ---------------------------------------------------------------------------

def native_pick_dir():
    result = {'path': None, 'error': None}

    def worker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            r = tk.Tk()
            r.withdraw()
            r.attributes('-topmost', True)
            d = filedialog.askdirectory(title='选择 Mod 目录')
            result['path'] = d or None
            try:
                r.destroy()
            except Exception:
                pass
        except Exception as e:
            result['error'] = str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(1800)
    return result


def open_dir(path):
    if not path or not os.path.isdir(path):
        return False
    if sys.platform.startswith('win'):
        os.startfile(path)  # noqa
    else:
        webbrowser.open('file://' + os.path.abspath(path))
    return True


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'TNOColorGenWeb/1.0'

    # -- helpers -----------------------------------------------------------
    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if not isinstance(body, (bytes, bytearray)):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            else:
                body = str(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_file(self, path, ctype=None, cache=False):
        if not os.path.isfile(path):
            self._send(404, {'error': 'not found'})
            return
        with open(path, 'rb') as f:
            data = f.read()
        if ctype is None:
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        extra = {} if cache else {'Cache-Control': 'no-store'}
        self._send(200, data, ctype + ('; charset=utf-8' if ctype.startswith('text/') else ''), extra)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def log_message(self, fmt, *args):  # 静默
        pass

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if path in ('/', '/index.html'):
                return self._send_file(os.path.join(WEB_DIR, 'index.html'))
            if path == '/favicon.ico':
                return self._send(204, b'')
            if path == '/api/state':
                return self._send(200, self._state())
            if path == '/api/auto-assemble':
                return self._send(200, self._auto_assemble())
            if path == '/api/inspect':
                return self._send(200, self._inspect(q.get('path', '')))
            if path == '/api/jobs':
                with JOB_LOCK:
                    listing = [{'id': j.id, 'kind': j.kind, 'status': j.status,
                                'elapsed': j.elapsed, 'out': j.out}
                               for j in sorted(JOBS.values(), key=lambda j: j.id)[-30:]]
                return self._send(200, {'jobs': listing})
            if path.startswith('/api/jobs/'):
                return self._job_route(path, q)
            return self._send(404, {'error': '未知路径: %s' % path})
        except Exception as e:
            self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})

    def _job_route(self, path, q):
        rest = path[len('/api/jobs/'):]
        if '/' in rest:
            jid, sub = rest.split('/', 1)
        else:
            jid, sub = rest, ''
        with JOB_LOCK:
            job = JOBS.get(jid)
        if not job:
            return self._send(404, {'error': '作业不存在'})
        if sub == 'preview':
            if not job.preview_path or not os.path.isfile(job.preview_path):
                return self._send(404, {'error': '预览图尚未生成'})
            return self._send_file(job.preview_path, 'image/png')
        # 状态轮询（增量日志）
        since = int(q.get('since', 0) or 0)
        with JOB_LOCK:
            lines = [(s, t) for s, t in job.log if s > since]
            last_seq = job.log_seq
        return self._send(200, {
            'id': job.id, 'kind': job.kind, 'status': job.status,
            'progress': job.progress, 'stats': job.stats,
            'elapsed': job.elapsed, 'error': job.error,
            'out': job.out, 'installed_to': job.installed_to,
            'has_preview': bool(job.preview_path and os.path.isfile(job.preview_path)),
            'log': lines, 'since': last_seq,
        })

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        try:
            if path == '/api/jobs':
                p = self._body()
                kind = 'generate' if p.get('mode') != 'scan' else 'scan'
                job = start_job(kind, p)
                return self._send(200, {'id': job.id, 'kind': job.kind})
            if path.startswith('/api/jobs/') and path.endswith('/cancel'):
                jid = path[len('/api/jobs/'):-len('/cancel')]
                with JOB_LOCK:
                    job = JOBS.get(jid)
                if not job:
                    return self._send(404, {'error': '作业不存在'})
                job.cancel.set()
                job.log_line('用户请求取消…')
                return self._send(200, {'ok': True})
            if path == '/api/pick-dir':
                return self._send(200, native_pick_dir())
            if path == '/api/open-dir':
                p = self._body()
                ok = open_dir(p.get('path', ''))
                return self._send(200, {'ok': ok})
            return self._send(404, {'error': '未知路径: %s' % path})
        except Exception as e:
            self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})

    # -- 数据 ---------------------------------------------------------------
    def _state(self):
        roots = gen.assemble_mods(BASE)
        names = {}
        for r in roots:
            nm = gen.mod_name_from_descriptor(r) or os.path.basename(r)
            names[r] = nm
        return {
            'cwd': BASE,
            'mods': roots,
            'mod_names': names,
            'hoi4_mod_dir': gen.find_hoi4_mod_dir(),
            'presets': [[k, l, '#%02X%02X%02X' % rgb] for k, l, rgb in gen.PRESETS],
            'default_out': os.path.join(BASE, 'generated_mods', 'TNO_UI_FFBA5C'),
            'python': sys.version.split()[0],
        }

    def _auto_assemble(self):
        roots = gen.assemble_mods(BASE)
        names = {}
        for r in roots:
            nm = gen.mod_name_from_descriptor(r) or os.path.basename(r)
            names[r] = nm
        return {'mods': roots, 'mod_names': names}

    def _inspect(self, p):
        if not p:
            return {'ok': False, 'error': '未填写路径'}
        if not os.path.isdir(p):
            return {'ok': False, 'exists': False, 'error': '目录不存在'}
        return {
            'ok': True, 'exists': True,
            'base': os.path.basename(p.rstrip('\\/')),
            'name': gen.mod_name_from_descriptor(p),
            'deps': gen.mod_deps_from_descriptor(p),
            'has_gfx': os.path.isdir(os.path.join(p, 'gfx')),
            'score': gen._tno_score(p),
        }


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def find_port():
    for port in range(PORT_START, PORT_END + 1):
        try:
            s = ThreadingHTTPServer((HOST, port), Handler)
            s.server_close()
            return port
        except OSError:
            continue
    raise RuntimeError('找不到可用端口（%d-%d 都被占用）' % (PORT_START, PORT_END))


def run_web(port=0, open_browser=True):
    port = int(port or find_port())
    try:
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        if not port:
            raise
        httpd = ThreadingHTTPServer((HOST, find_port()), Handler)
    url = 'http://%s:%d' % (HOST, httpd.server_address[1])
    print('=' * 62)
    print(' TNO UI 换色 Mod 生成器 - Web 界面')
    print(' 本地服务: %s   （按 Ctrl+C 停止）' % url)
    print(' 关闭本窗口即退出服务。')
    print('=' * 62)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='TNO UI 换色 Mod 生成器 - Web 界面')
    ap.add_argument('--port', type=int, default=0, help='端口（默认自动找 8765-8795 空闲端口）')
    ap.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    args = ap.parse_args(argv)
    return run_web(port=args.port, open_browser=not args.no_browser)


if __name__ == '__main__':
    sys.exit(main())
