"""用 Edge 的调试协议点击面板,验证交互逻辑(对着 mock_panel.py,不碰真机)。"""
import base64, json, os, socket, struct, subprocess, sys, tempfile, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
PORT, URL = 9333, 'http://127.0.0.1:18123/'
EDGE = next(p for p in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe') if os.path.exists(p))
prof = tempfile.mkdtemp()
proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--remote-debugging-port=%d' % PORT, '--user-data-dir=' + prof,
                         '--window-size=1100,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT)); break
        except OSError: time.sleep(0.2)
    ws_url = next(t['webSocketDebuggerUrl'] for t in tabs if t['type'] == 'page')
    host, rest = ws_url[5:].split('/', 1)
    s = socket.create_connection((host.split(':')[0], int(host.split(':')[1])), 10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(('GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (rest, host, key)).encode())
    buf = b''
    while b'\r\n\r\n' not in buf: buf += s.recv(1)
    def send(obj):
        d = json.dumps(obj).encode(); m = os.urandom(4); n = len(d)
        h = b'\x81' + (bytes([0x80 | n]) if n < 126 else b'\xfe' + struct.pack('>H', n) if n < 65536 else b'\xff' + struct.pack('>Q', n))
        s.sendall(h + m + bytes(b ^ m[i % 4] for i, b in enumerate(d)))
    def recv_exact(n):
        out = b''
        while len(out) < n: out += s.recv(n - len(out))
        return out
    def recv():
        h = recv_exact(2); n = h[1] & 0x7f
        if n == 126: n = struct.unpack('>H', recv_exact(2))[0]
        elif n == 127: n = struct.unpack('>Q', recv_exact(8))[0]
        return json.loads(recv_exact(n))
    mid = [0]
    def cmd(method, **params):
        mid[0] += 1; send({'id': mid[0], 'method': method, 'params': params})
        while True:
            r = recv()
            if r.get('id') == mid[0]: return r
    def js(expr):
        r = cmd('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=True)
        if 'exceptionDetails' in r.get('result', {}): raise RuntimeError(r['result']['exceptionDetails'])
        return r['result']['result'].get('value')
    def wait(cond, what, t=6):
        for _ in range(int(t * 10)):
            if js(cond): return
            time.sleep(0.1)
        raise AssertionError('超时: ' + what)

    cmd('Page.enable'); cmd('Page.navigate', url=URL)
    wait("document.querySelectorAll('.card').length === 4", '4 张卡片渲染')
    card = lambda name: "[...document.querySelectorAll('.card')].find(c => c.querySelector('.name').textContent.startsWith('%s'))" % name
    ok = lambda label: print('  ✓', label)

    order = js("[...document.querySelectorAll('.name')].map(e => e.textContent.slice(0,3))")
    assert order == ['负一楼', '一楼 ', '二楼 ', '三楼 '], order; ok('楼层排序: ' + str(order))

    # 1. 一楼(关机)点电源 -> 应下发 sw=1 并变为运行中,PM2.5 等传感器显示出来(mock 里该设备传感器值为 0)
    js(card('一楼') + ".querySelector('.power').click()")
    wait(card('一楼') + ".classList.contains('running')", '一楼开机后变为运行中')
    ok('点电源 → 一楼开机并更新界面')
    # 2. 负一楼切自动 -> 滑块应禁用,出现提示
    js(card('负一楼') + ".querySelector('[data-mode=\"1\"]').click()")
    wait(card('负一楼') + ".querySelector('.hint').textContent.includes('自动模式') && !" + card('负一楼') + ".classList.contains('busy')", '自动模式提示出现')
    assert js(card('负一楼') + ".querySelector('input').disabled"), '自动模式下滑块应禁用'
    hint = js(card('负一楼') + ".querySelector('.hint').textContent")
    ok('切自动 → 滑块禁用,提示: ' + hint)
    # 3. 切回手动,拖动新风滑块到 35 -> 应下发 airSpeed=35,标签显示 350 m³/h
    js(card('负一楼') + ".querySelector('[data-mode=\"0\"]').click()")
    wait("!" + card('负一楼') + ".querySelector('input').disabled", '手动模式滑块恢复')
    js("(()=>{const r=" + card('负一楼') + ".querySelector('input[data-k=airSpeed]'); r.value=35; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change'));})()")
    wait(card('负一楼') + ".querySelector('.slider output').textContent === '350 m³/h' && !" + card('负一楼') + ".classList.contains('busy')", '滑块提交完成')
    ok('拖动新风到 35 → 显示 350 m³/h')
    # 4. 失败路径:airSpeed=69 被 mock 拒绝 -> 弹错误提示,滑块回到服务器值(35)
    js("(()=>{const r=" + card('负一楼') + ".querySelector('input[data-k=airSpeed]'); r.value=69; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change'));})()")
    wait("document.querySelector('#toast').classList.contains('err')", '出现错误提示')
    toast = js("document.querySelector('#toast').textContent")
    wait(card('负一楼') + ".querySelector('input[data-k=airSpeed]').value === '35'", '失败后滑块回滚到 35')
    ok('失败 → 提示「%s」,滑块回滚到 35' % toast)
    # 5. 改名
    js("window.prompt = () => '客厅新风'")
    js(card('二楼') + ".querySelector('.name').click()")
    wait("[...document.querySelectorAll('.name')].some(e => e.textContent === '客厅新风')", '改名生效')
    ok('点名称改名 → 客厅新风')
    # 6. 离线设备所有控件禁用
    dis = js(card('客厅') + ".querySelector('.power').disabled && [...card.querySelectorAll('.seg button,input')].every(x=>x.disabled)".replace('card.', card('客厅') + '.'))
    assert dis, '离线设备控件应全部禁用'; ok('离线设备控件全部禁用')

    log = json.load(urllib.request.urlopen(URL + '_log'))
    print('\n服务器收到的请求:'); [print('  ', t[-4:], b) for t, b in log]
    assert [b for _, b in log] == [{'sw': 1}, {'mode': 1}, {'mode': 0}, {'airSpeed': 35}, {'airSpeed': 69}], log
    print('\n面板交互测试全部通过')
finally:
    proc.terminate()
