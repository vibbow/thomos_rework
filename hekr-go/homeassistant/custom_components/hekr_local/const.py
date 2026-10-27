"""hekr-local 集成的常量。"""
from datetime import timedelta

DOMAIN = "hekr_local"

CONF_TOKEN = "token"
DEFAULT_PORT = 8080

SCAN_INTERVAL = timedelta(seconds=5)
REQUEST_TIMEOUT = 10
COMMAND_TIMEOUT = 25  # 服务器会重试一次,最坏约 9 秒

# 风量档位: 设备里是 0..70,乘 10 即 m³/h
MAX_SPEED = 70
SPEED_UNIT_FACTOR = 10

MODE_MANUAL = 0
MODE_AUTO = 1
MODE_BOOST = 2
MODE_TO_PRESET = {MODE_MANUAL: "manual", MODE_AUTO: "auto", MODE_BOOST: "boost"}
PRESET_TO_MODE = {v: k for k, v in MODE_TO_PRESET.items()}

# 温度字段有 20 的偏移: 实际温度 = temp - 20
TEMP_OFFSET = 20
