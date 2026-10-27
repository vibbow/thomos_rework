"""用假数据渲染控制面板,不碰真机。python mock_panel.py <index.html> <port>"""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
html = open(sys.argv[1], 'rb').read()
now = time.time()
def dev(tid, name, online, ip, **st):
    base = dict(flag=0, sw=0, mode=0, airSpeed=0, exhaustSpeed=0, PM25=0, temp=20, hum=0, co2=0,
                filter=45, ltcFilter=73, eleFilter=73, highFilter=45, alarm=0, ltcAlarm=0, eleAlarm=0, highAlarm=0)
    base.update(st)
    return dict(devTid=tid, name=name, online=online, ip=ip, lastSeen=now - (5 if online else 7200), status=base,
                info=dict(MAC='X', rssi=-64, SSID='Things'))
DEVS = {d['devTid']: d for d in [
    dev('ESP_2M_CC50E3D7BD8A', '负一楼 新风控制器', True, '172.17.2.109', sw=1, mode=0, airSpeed=20, exhaustSpeed=20, PM25=21, temp=38, hum=56),
    dev('ESP_2M_2CF432568AA9', '一楼 新风控制器', True, '172.17.2.105', filter=0, highFilter=0, ltcFilter=33, eleFilter=33, alarm=1, highAlarm=1),
    dev('ESP_2M_2CF432568A04', '二楼 新风控制器', False, '172.17.2.106', mode=1, filter=86, highFilter=86, ltcFilter=93, eleFilter=93),
    dev('ESP_AUTO', '三楼 自动模式运行', True, '172.17.2.110', sw=1, mode=1, airSpeed=10, exhaustSpeed=10, PM25=90, temp=42, hum=40, co2=800, filter=20),
]}
LOG = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype='application/json'):
        self.send_response(code); self.send_header('Content-Type', ctype); self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
    def do_GET(self):
        if self.path == '/': return self.send(200, html, 'text/html; charset=utf-8')
        if self.path == '/api/devices': return self.send(200, list(DEVS.values()))
        if self.path == '/_log': return self.send(200, LOG)
        self.send(404, {'error': 'x'})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tid = self.path.split('/')[3]
        if self.path.endswith('/command'):
            LOG.append((tid, body))
            if body.get('airSpeed') == 69: return self.send(502, {'error': '设备响应超时'})
            DEVS[tid]['status'].update(body); return self.send(200, {'ok': True, 'device': DEVS[tid]})
        DEVS[tid]['name'] = body['name']; self.send(200, DEVS[tid])
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[2])), H).serve_forever()
