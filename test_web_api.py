# -*- coding: utf-8 -*-
"""Web GUI 端到端自检：页面 / state / inspect / 扫描作业 / 完整生成作业 / 预览。"""
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8765'


def get(path, timeout=60):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read()


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode('utf-8'))


def wait_server():
    for i in range(60):
        try:
            st, body = get('/api/state')
            return json.loads(body)
        except Exception:
            time.sleep(1)
    print('FAIL: server not ready')
    sys.exit(1)


def wait_job(jid, timeout_s=1200):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st, body = get('/api/jobs/%s?since=0' % jid)
        j = json.loads(body)
        if j['status'] != 'running':
            return j
        time.sleep(1.0)
    raise TimeoutError('job %s timeout' % jid)


def main():
    failed = []
    state = wait_server()
    print('state: mods=%d hoi4=%s presets=%d' % (
        len(state['mods']), state['hoi4_mod_dir'], len(state['presets'])))

    # 页面
    st, html = get('/')
    if b'TNO UI' in html and st == 200:
        print('PASS page (%d bytes)' % len(html))
    else:
        failed.append('page'); print('FAIL page')

    # inspect
    st, body = get('/api/inspect?path=' + urllib.parse.quote(state['mods'][0]))
    d = json.loads(body)
    if d.get('ok') and d.get('has_gfx'):
        print('PASS inspect: %s' % d.get('name'))
    else:
        failed.append('inspect'); print('FAIL inspect:', d)

    # 扫描作业（验证线程/进度/日志/统计管线）
    st, d = post('/api/jobs', {'mods': state['mods'], 'color': '#FFBA5C', 'mode': 'scan',
                              'darken': 0, 'compress': False, 'jobs': 0})
    jid = d['id']
    j = wait_job(jid, 900)
    s = j['stats'] or {}
    print('scan: status=%s scanned=%s blue=%s photo=%s' % (
        j['status'], s.get('scanned'), s.get('blue'), s.get('photo_skip')))
    if j['status'] == 'done' and s.get('scanned', 0) > 1000 and s.get('blue', 0) > 1000:
        print('PASS scan job')
    else:
        failed.append('scan'); print('FAIL scan job')

    # 完整生成作业（刷新示例 TNO_UI_GOLD，4 源）
    out = os.path.join(os.getcwd(), 'generated_mods', 'TNO_UI_GOLD')
    st, d = post('/api/jobs', {
        'mods': state['mods'], 'color': '#F5A524', 'mode': 'generate',
        'darken': 0, 'compress': False, 'jobs': 0, 'out': out,
        'name': 'TNO UI #F5A524 GUI', 'install': False})
    jid2 = d['id']
    print('generate job %s ...' % jid2)
    j2 = wait_job(jid2, 1800)
    s2 = j2['stats'] or {}
    print('gen: status=%s scanned=%s blue=%s photo=%s err=%s elapsed=%s' % (
        j2['status'], s2.get('scanned'), s2.get('blue'), s2.get('photo_skip'),
        j2.get('error'), j2.get('elapsed')))
    if j2['status'] == 'done':
        print('PASS generate job')
    else:
        failed.append('generate')

    # 预览图
    try:
        st, png = get('/api/jobs/%s/preview' % jid2)
        if st == 200 and len(png) > 5000:
            print('PASS preview (%d bytes)' % len(png))
        else:
            failed.append('preview'); print('FAIL preview')
    except Exception as e:
        failed.append('preview'); print('FAIL preview:', e)

    # 生成产物
    need = [os.path.join(out, 'descriptor.mod'), out + '.mod',
            os.path.join(out, 'thumbnail.png'), os.path.join(out, 'preview.png'),
            os.path.join(out, 'README.txt')]
    missing = [p for p in need if not os.path.exists(p)]
    nfiles = sum(len(f) for _, _, f in os.walk(out)) if os.path.isdir(out) else 0
    print('artifacts: files=%d missing=%s' % (nfiles, missing))
    if not missing and nfiles > 5000:
        print('PASS artifacts')
    else:
        failed.append('artifacts')

    if failed:
        print('RESULT: FAIL %s' % failed)
        sys.exit(1)
    print('RESULT: ALL PASS')


if __name__ == '__main__':
    main()
