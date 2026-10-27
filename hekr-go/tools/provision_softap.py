"""通过 SoftAP 给 Hekr 设备配网(不依赖云端、不依赖旧 APP)。

用法:
  1. 让设备进入配网模式,电脑/手机连上设备发出的热点;
  2. python provision_softap.py --ssid 家里WiFi --password 密码

协议(来自 APK 的 CommonDeviceConfig.SoftAPAsyncTask):
  向 192.168.4.1:7001 建立 TCP 连接,发送
      ssid=<SSID>&password=<密码>&pinCode=<PIN>&end
  设备回一行 JSON: {"action":"devConfig","params":{"devTid","STEP","code","PIN","bind",...}}
  APP 每秒重发一次,直到收到 PIN 匹配且 bind != 0 的 devConfig。
  PIN 在官方流程中由云端生成(>=6位),自建服务器时随便给一个 >=6 位的即可。
"""
import argparse
import json
import secrets
import socket
import sys
import time


def attempt(host, port, payload, timeout):
    with socket.create_connection((host, port), timeout) as s:
        s.settimeout(timeout)
        s.sendall(payload.encode('utf-8'))
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
    return buf.split(b'\n')[0].decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ssid', required=True)
    ap.add_argument('--password', required=True)
    ap.add_argument('--pin', default=None, help='配网安全码,默认随机 8 位数字')
    ap.add_argument('--host', default='192.168.4.1')
    ap.add_argument('--port', type=int, default=7001)
    ap.add_argument('--timeout', type=float, default=5)
    ap.add_argument('--retries', type=int, default=60)
    a = ap.parse_args()

    pin = a.pin or ''.join(secrets.choice('0123456789') for _ in range(8))
    if len(pin) < 6:
        sys.exit('PIN 至少 6 位')
    payload = 'ssid=%s&password=%s&pinCode=%s&end' % (a.ssid, a.password, pin)
    print('目标 %s:%d  SSID=%s  PIN=%s' % (a.host, a.port, a.ssid, pin))

    for i in range(a.retries):
        try:
            line = attempt(a.host, a.port, payload, a.timeout)
        except OSError as e:
            print('[%02d] 连接失败: %s' % (i + 1, e))
            time.sleep(1)
            continue
        print('[%02d] 设备回应: %s' % (i + 1, line))
        try:
            j = json.loads(line)
            p = j.get('params', {})
        except ValueError:
            time.sleep(1)
            continue
        if j.get('action') == 'devConfig' and p.get('PIN') == pin and p.get('bind', 0) != 0:
            print('设备已收到配置: devTid=%s STEP=%s code=%s' % (p.get('devTid'), p.get('STEP'), p.get('code')))
            print('请把电脑切回正常 WiFi,设备会重启并连接路由器。')
            return
        time.sleep(1)
    sys.exit('超时:设备没有确认配置')


if __name__ == '__main__':
    main()
