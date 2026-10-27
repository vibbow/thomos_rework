"""Hekr 一键配网(SmartConfig)编码器与发送端,移植自 APK:
  me.hekr.hekrconfig.utils.HekrAirKissEncoder
  me.hekr.hekrconfig.common.CommonDeviceConfig.sendMsgToDevice / DeviceAsyncTask

原理:设备进入配网状态后处于混杂模式监听 WiFi 帧,手机/电脑向一组组播地址
224.x.y.255:7001 发 UDP 包,包的**长度**和**目的地址**携带 SSID/密码/PIN。
设备解出后连路由器,并从 UDP 10000 向 255.255.255.255:24254 广播 devConfig 进度。
"""
import socket
import struct
import time


def crc8(data: bytes) -> int:
    """反射式 CRC-8,多项式 0x8C(与 APK 中 DEFAULT_AUTH_REQUEST_CODE=140 一致)。"""
    crc = 0
    for byte in data:
        for _ in range(8):
            mix = (crc ^ byte) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
            byte >>= 1
    return crc & 0xFF


class AirKissEncoder:
    def __init__(self, ssid: str, password: str, pin: str):
        if not password.isascii() or not pin.isascii():
            raise ValueError('密码和 PIN 目前只支持 ASCII 字符')
        self.data = []
        for _ in range(5):
            self._leading()
            self._magic(ssid, password, pin)
            for _ in range(15):
                self._prefix(password)
                body = (password + pin).encode()
                block = bytearray(4)
                i = 0
                while i < len(body) // 4:
                    block[:] = body[i * 4:i * 4 + 4]
                    self._sequence(i, bytes(block))
                    i += 1
                rest = len(body) % 4
                if rest:
                    # 与 APK 一致:block 保留上一块的内容,仅覆盖前 rest 字节并把其余补 0
                    block[:rest] = body[i * 4:i * 4 + rest]
                    for k in range(rest, 4):
                        block[k] = 0
                    self._sequence(i, bytes(block))

    def _leading(self):
        for _ in range(50):
            self.data.extend((1, 11, 21, 31))

    def _magic(self, ssid, password, pin):
        total = len(password) + len(pin) + len(ssid)
        a = (total >> 4) & 15
        if a == 0:
            a = 8
        c = crc8(ssid.encode())
        head = [a, (total & 15) | 16, 32 | ((c >> 4) & 15), (c & 15) | 48]
        for _ in range(20):
            self.data.extend(head)

    def _prefix(self, password):
        n = len(password)
        c = crc8(bytes([n]))
        self.data.extend(((n >> 4) & 15 | 64, (n & 15) | 80, ((c >> 4) & 15) | 96, (c & 15) | 112))

    def _sequence(self, index, block: bytes):
        self.data.append(crc8(bytes([index & 255]) + block) | 128)
        self.data.append(index | 128)
        for b in block:
            self.data.append(b | 256)


def sleep_time(pass_ms: int, nbytes: int) -> int:
    j2 = pass_ms // 1000 - 3
    return (100 // nbytes) * (1 + (max(j2, 0) // 6))


def packets(ssid: str, password: str, pin: str, encoded):
    """生成 (目的地址, 包长) 序列,对应 APK 的 sendMsgToDevice 一轮发送。"""
    payload = (ssid + '\n' + password + '\n' + pin).encode()
    n = len(payload)
    for i in range(len(encoded) * (n + 2)):
        size = encoded[i % len(encoded)]
        k = i % (n + 2)
        if size < 0:
            continue
        if k == 0 or k == n + 1:
            addr = '224.127.%d.255' % n
        else:
            addr = '224.%d.%d.255' % (k - 1, payload[k - 1])
        yield addr, size


def send_loop(ssid, password, pin, local_ip, stop, port=7001):
    """持续发送,直到 stop() 返回 True。local_ip 必须是连着 WiFi 的那块网卡的 IP。"""
    enc = AirKissEncoder(ssid, password, pin).data
    payload_len = len((ssid + '\n' + password + '\n' + pin).encode())
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', 1))
    start = time.time()
    try:
        while not stop():
            passed = int((time.time() - start) * 1000)
            gap = sleep_time(passed, len(ssid.encode()) + len(password.encode()) + 2 + (0 if passed > 1000 else 1))
            for addr, size in packets(ssid, password, pin, enc):
                if stop():
                    return
                s.sendto(b'\x01' * size, (addr, port))
                time.sleep(max(gap, 4) / 1000)
            time.sleep(max(gap, 4) / 1000)
    finally:
        s.close()
