# thomos_rework

托马仕(Thomos)THD-4B 新风设备的本地化改造。官方 App(包名 `com.hekr.thomos`,基于 Hekr 氦氪平台)在新版 Android 上已无法运行,云端也随时可能下线。这个项目通过逆向 App 与抓包还原了设备协议,提供一个**纯局域网运行的本地云**,让设备脱离官方服务器,并可接入 Home Assistant。

## 内容

| 目录 | 说明 |
|---|---|
| [hekr-go/](hekr-go/) | 本地版 Hekr 云服务:单个静态二进制,零第三方依赖,自带网页界面和 HTTP API |
| [hekr-go/homeassistant/](hekr-go/homeassistant/custom_components/hekr_local/) | Home Assistant 自定义集成(风扇、传感器、数值调节) |
| [hekr-go/tools/](hekr-go/tools/) | 不依赖官方云的配网工具(一键配网 / 热点方式) |

构建、部署、配置项、API 与排错见 [hekr-go/README.md](hekr-go/README.md)。

## 工作原理

1. 设备开机先问 `info.hekr.me:91`,再长连 `hub.hekr.me:83`(明文 TCP)。
2. 在路由器上把这两个域名解析(或把原云 IP 的 TCP 流量 DNAT)到本服务器,设备就会连到 hekr-local。
3. 设备登录时,本服务生成并下发 `ctrlKey` / `bindKey`,控制端凭 `ctrlKey` 下发指令。
4. 控制帧格式:`48 | 长度 | 02(下行)/01(上行) | msgId | cmdId | 字段 | 校验和`(校验和为前面字节之和 & 0xFF)。

| cmdId | 含义 |
|---|---|
| 0 / 1 | 查询设备 / 设备上报 |
| 2 | 开关 |
| 3 | 模式(0 手动 / 1 自动 / 2 强风) |
| 4 / 5 | 送风 / 排风风速 |
| 6 | 查询数量 |
| 8 | 切换 |

## 快速开始

```sh
cd hekr-go
go test ./...
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o hekr-local .
HEKR_TOKEN=随机字符串 ./hekr-local   # 网页 http://localhost:8080/?token=...
```

## 注意

- 设备与本服务之间是明文 TCP(设备固件如此),只应放在可信的家庭内网。
- 仓库**不包含**账号凭据、设备密钥(`devices.json`)、抓包文件和官方 APK;首次运行时设备登录会自动生成 `devices.json`。
- 本项目仅用于对自己拥有的设备做互操作性研究,与托马仕、Hekr 官方无关。
