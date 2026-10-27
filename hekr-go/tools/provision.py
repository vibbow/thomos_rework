"""给托马仕/Hekr 新风设备做一键配网(不依赖官方云和旧 APP)。

步骤:
  1. 让本机连上要给设备使用的 **2.4G** WiFi(最好是无线网卡,不要走网线);
  2. 先运行本脚本;
  3. 立刻让设备进入配网状态:关机状态下长按 开关键+风速键 6 秒以上
     (高配系列长按 WiFi 图标),WiFi 图标闪烁即可;
  4. 设备配上网后会广播 devConfig,脚本显示进度,收到匹配的 PIN 即成功。

用法:
  python provision.py --ssid Things --password 密码
  python provision.py --ssid Things --password 密码 --local-ip 192.168.1.50
"""
import argparse
import json
import secrets
import socket
import string
import sys
import threading
import time

from smartconfig import send_loop

STEP_TEXT = {0: '获取安全码', 1: '设备收到配置', 2: '连接路由器', 3: '连接路由器', 4: '连接路由器',
             5: '验证设备', 6: '验证设备', 7: '验证设备', 8: '验证设备', 9: '设备登录服务器', 10: '绑定'}


def guess_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))  # UDP connect 不会真的发包
        return s.getsockname()[0]
    finally:
        s.close()


def listen(pin, done, port=24254):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', port))
    s.settimeout(1)
    seen = set()
    while not done.is_set():
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            j = json.loads(data.decode('utf-8', 'replace'))
            p = j['params']
        except (ValueError, KeyError):
            continue
        if j.get('action') != 'devConfig':
            continue
        key = (p.get('devTid'), p.get('STEP'), p.get('code'))
        if key in seen:
            continue
        seen.add(key)
        mine = p.get('PIN') == pin
        print('  [%s] %s STEP=%s(%s) code=%s PIN%s 来自 %s' % (
            time.strftime('%H:%M:%S'), p.get('devTid'), p.get('STEP'),
            STEP_TEXT.get(p.get('STEP'), '?'), p.get('code'), '匹配' if mine else '不匹配', addr[0]))
        if mine and p.get('STEP') in (9, 10) and p.get('code') == 200:
            print('配网成功:设备 %s 已连上路由器(IP %s)' % (p.get('devTid'), addr[0]))
            done.set()
    s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ssid', required=True)
    ap.add_argument('--password', required=True)
    ap.add_argument('--pin', help='配网安全码(>=6位字母数字),默认随机')
    ap.add_argument('--local-ip', help='连着 WiFi 的网卡 IP,默认自动判断')
    ap.add_argument('--timeout', type=int, default=180, help='最长发送秒数(设备 3 分钟后会退出配网)')
    a = ap.parse_args()

    pin = a.pin or ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    if len(pin) < 6:
        sys.exit('PIN 至少 6 位')
    ip = a.local_ip or guess_local_ip()
    print('本机 IP: %s   SSID: %s   PIN: %s' % (ip, a.ssid, pin))
    print('现在让设备进入配网状态(WiFi 图标闪烁)……')

    done = threading.Event()
    t = threading.Thread(target=listen, args=(pin, done), daemon=True)
    t.start()
    deadline = time.time() + a.timeout
    try:
        send_loop(a.ssid, a.password, pin, ip, lambda: done.is_set() or time.time() > deadline)
    except KeyboardInterrupt:
        pass
    finally:
        ok = done.is_set()
        done.set()
        t.join(2)
    if not ok:
        sys.exit('超时未收到设备确认')


if __name__ == '__main__':
    main()
