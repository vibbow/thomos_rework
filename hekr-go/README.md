# hekr-local

本地版 Hekr 云:让托马仕 THD-4B 新风设备脱离官方服务器,纯局域网运行。
单个静态二进制,零第三方依赖(只用 Go 标准库),自带网页界面和 HTTP API。

## 构建

```sh
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o dist/hekr-local-linux-amd64 .
# 树莓派等 ARM 板子把 GOARCH 换成 arm64
go test ./...
```

## 部署到 Ubuntu(systemd)

```sh
scp dist/hekr-local-linux-amd64 deploy/hekr-local.service devices.json you@server:/tmp/
# 在服务器上:
sudo install -m 755 /tmp/hekr-local-linux-amd64 /usr/local/bin/hekr-local
sudo useradd --system --no-create-home --shell /usr/sbin/nologin hekr
sudo install -m 644 /tmp/hekr-local.service /etc/systemd/system/
echo 'HEKR_TOKEN=换成一串随机字符' | sudo tee /etc/hekr-local.env && sudo chmod 600 /etc/hekr-local.env
sudo mkdir -p /var/lib/hekr-local && sudo install -m 600 -o hekr -g hekr /tmp/devices.json /var/lib/hekr-local/devices.json
sudo systemctl daemon-reload && sudo systemctl enable --now hekr-local
journalctl -u hekr-local -f
```

网页:`http://服务器IP:8080/?token=你的令牌`(令牌会存进浏览器)。

## 让设备连过来

设备开机后先问 `info.hekr.me:91`,再连 `hub.hekr.me:83`。在路由器上:

1. **DNS 解析**:`info.hekr.me`、`hub.hekr.me` 都指向本服务器 IP。
2. **兜底(建议)**:设备可能缓存了云端 IP,不走 DNS。在路由器上把
   `106.75.84.199:83` 和 `117.50.2.139:91` 的 TCP 流量 DNAT 到本服务器同端口。

已经连着官方云的设备不会立刻切换;给某一台断电重启,它就会连到本服务器。
想退回官方云:删掉上面两项设置,再重启设备。

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|---|---|---|
| `HEKR_DATA` | `devices.json` | 设备数据(名称、密钥),systemd 单元里设为 `/var/lib/hekr-local/devices.json` |
| `HEKR_BIND` | `0.0.0.0` | 监听地址 |
| `HEKR_INFO_PORT` | `91` | 设备问路端口 |
| `HEKR_HUB_PORT` | `83` | 设备长连接端口 |
| `HEKR_HUB_HOST` | `hub.hekr.me` | 告诉设备去连的地址 |
| `HEKR_WEB_PORT` | `8080` | 网页与 API |
| `HEKR_TOKEN` | 空 | `/api/` 的 Bearer 令牌;空则不鉴权,**只建议在可信内网这样用** |
| `HEKR_LOG` | `info` | `debug` 会打印每条设备消息 |

## 安全说明

- 设备首次登录时记下它的 `license`,之后必须一致,否则拒绝(防局域网里有人冒名)。
  确实换了设备/重置后被拒,把 `devices.json` 里该设备的 `license` 清空即可。
- 没见过的设备会被自动登记(新设备配网需要);未识别的消息会被完整记录,可在 `/api/unknown` 查看。
- 设备与本服务之间是明文 TCP(设备固件如此),只应放在家庭内网。

## HTTP API

```
GET  /api/devices                      设备列表与状态
GET  /api/devices/{devTid}
POST /api/devices/{devTid}/command     {"sw":1,"mode":0,"airSpeed":30,"exhaustSpeed":25}
POST /api/devices/{devTid}/name        {"name":"客厅"}
GET  /api/unknown                      最近未识别的设备消息
```

`sw` 0/1;`mode` 0 手动/1 自动/2 强风;`airSpeed`、`exhaustSpeed` 0..70(×10 即 m³/h)。

## 配网工具(tools/,Python 3,无依赖)

换 WiFi 时用,不依赖官方云。需在**连着目标 2.4G WiFi 的电脑**上运行:

```sh
python tools/provision.py --ssid Things --password 密码
```

然后让设备进入配网状态(关机时长按 开关键+风速键 6 秒以上,高配机型长按 WiFi 图标,
WiFi 图标闪烁)。成功后设备会连路由器并连到本服务器。
`provision_softap.py` 是热点方式的备用实现,THD-4B 官方教程用的是一键配网,未必支持。

## 排错:设备一直连不上(SYN 到了但没有应答)

现象:在服务器上 `tcpdump` 能看到设备发来的 SYN 不断重传,日志里却没有任何连接。
原因通常是**回包路由不对**。例如设备在 `172.17.2.0/24`,而服务器装了 Docker,
默认网桥 `172.17.0.0/16` 把回包吞进了 `docker0`。检查:

```sh
ip route get <设备IP>      # 应该经网关走物理网卡,而不是 docker0
```

修复:加一条更精确的路由,并用 systemd drop-in 固化(`/etc/systemd/system/hekr-local.service.d/route.conf`):

```ini
[Service]
ExecStartPre=-+/usr/sbin/ip route replace 172.17.2.0/24 via 192.168.188.1 dev ens34
```
