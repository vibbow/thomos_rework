package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// simDevice 按抓包里的真实消息序列模拟一台 THD-4B。
type simDevice struct {
	tid, license string
	c            net.Conn
	state        map[string]int
	r            *bufio.Reader
	dropNext     int // 故意不应答接下来的 N 条 appSend,模拟设备偶发无响应
}

func startCloud(t *testing.T, reg *Registry) (cl *Cloud, infoAddr string) {
	t.Helper()
	cl = NewCloud(reg, "127.0.0.1", 0)
	cl.PollFirst, cl.PollEvery = 50*time.Millisecond, time.Hour
	infoLn, _ := net.Listen("tcp", "127.0.0.1:0")
	hubLn, _ := net.Listen("tcp", "127.0.0.1:0")
	cl.hubPort = hubLn.Addr().(*net.TCPAddr).Port
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go serveTCP(ctx, infoLn, cl.ServeInfo)
	go serveTCP(ctx, hubLn, cl.ServeHub)
	return cl, infoLn.Addr().String()
}

func jline(v any) []byte { b, _ := json.Marshal(v); return append(b, '\n') }

func ask(t *testing.T, addr string, v any) map[string]any {
	t.Helper()
	c, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.Write(jline(v))
	var out map[string]any
	line, _ := bufio.NewReader(c).ReadBytes('\n')
	json.Unmarshal(line, &out)
	return out
}

func newSim(t *testing.T, infoAddr, tid, license string) *simDevice {
	t.Helper()
	prod := ask(t, infoAddr, map[string]any{"msgId": 1, "action": "getProdInfo", "params": map[string]any{"devTid": tid, "prodKey": "x", "supportSSL": 1}})
	p := prod["params"].(map[string]any)
	c, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", int(p["servicePort"].(float64))))
	if err != nil {
		t.Fatal(err)
	}
	d := &simDevice{tid: tid, license: license, c: c, r: bufio.NewReader(c), state: map[string]int{
		"flag": 0, "sw": 0, "mode": 0, "airSpeed": 0, "exhaustSpeed": 0, "PM25": 0, "temp": 20, "hum": 0, "co2": 0,
		"filter": 45, "ltcFilter": 73, "eleFilter": 73, "highFilter": 45, "alarm": 0, "ltcAlarm": 0, "eleAlarm": 0, "highAlarm": 0}}
	t.Cleanup(func() { c.Close() })
	c.Write(jline(map[string]any{"msgId": 0, "action": "devLogin", "params": map[string]any{"license": license, "devTid": tid, "prodKey": "x"}}))
	c.Write([]byte{0, 0}) // 真机会夹杂的两个 0 字节
	c.Write(jline(map[string]any{"msgId": 2, "action": "reportDevInfo", "params": map[string]any{"devTid": tid, "MAC": "AABBCCDDEEFF", "binVer": "4.2.1.1", "lanIp": "10.0.0.9", "SSID": "Things", "rssi": -60}}))
	c.Write(jline(map[string]any{"msgId": 3, "action": "getTimerList", "params": map[string]any{"devTid": tid}}))
	c.Write(jline(map[string]any{"msgId": 4, "action": "heartbeat"}))
	return d
}

// run 处理服务器发来的消息直到连接关闭;返回收到的 action 列表。
func (d *simDevice) run(got chan<- string) {
	for {
		line, err := d.r.ReadBytes('\n')
		if err != nil {
			close(got)
			return
		}
		var m map[string]any
		json.Unmarshal([]byte(strings.Trim(string(line), "\x00 \r\n")), &m)
		act, _ := m["action"].(string)
		got <- act
		if act != "appSend" {
			continue
		}
		if d.dropNext > 0 {
			d.dropNext--
			continue
		}
		p := m["params"].(map[string]any)
		raw := p["data"].(map[string]any)["raw"].(string)
		f := mustHex(raw)
		cmd, pl := f[4], f[5:len(f)-1]
		switch cmd {
		case 2:
			d.state["sw"] = int(pl[1])
		case 3:
			d.state["mode"] = int(pl[1])
		case 4:
			d.state["airSpeed"] = int(pl[1])
		case 5:
			d.state["exhaustSpeed"] = int(pl[1])
		}
		d.c.Write(jline(map[string]any{"msgId": m["msgId"], "action": "appSendResp", "code": 200, "desc": "succuss",
			"params": map[string]any{"devTid": d.tid, "appTid": p["appTid"], "ctrlKey": p["ctrlKey"], "data": map[string]any{"raw": raw}}}))
		d.c.Write(jline(map[string]any{"msgId": 500, "action": "devSend", "params": map[string]any{"devTid": d.tid, "appTid": []any{}, "data": map[string]any{"raw": d.report()}}}))
	}
}

func mustHex(s string) []byte {
	var b []byte
	for i := 0; i+1 < len(s); i += 2 {
		var x byte
		fmt.Sscanf(s[i:i+2], "%02x", &x)
		b = append(b, x)
	}
	return b
}

func (d *simDevice) report() string {
	b := []byte{0x48, 25, 1, 1, 1}
	for _, f := range reportFields {
		v := d.state[f.name]
		for i := f.size - 1; i >= 0; i-- {
			b = append(b, byte(v>>(8*i)))
		}
	}
	b = append(b, checksum(b))
	return fmt.Sprintf("%X", b)
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	for i := 0; i < 100; i++ {
		if cond() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("超时: %s", what)
}

func api(t *testing.T, srv *httptest.Server, method, path, token, body string) (int, map[string]any) {
	t.Helper()
	req, _ := http.NewRequest(method, srv.URL+path, strings.NewReader(body))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	var out any
	json.Unmarshal(data, &out)
	m, _ := out.(map[string]any)
	if m == nil {
		m = map[string]any{"list": out}
	}
	return resp.StatusCode, m
}

func TestEndToEnd(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "devices.json")
	reg, err := LoadRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	cl, infoAddr := startCloud(t, reg)
	srv := httptest.NewServer(NewWebHandler(cl, reg, "secret"))
	defer srv.Close()

	if code, _ := api(t, srv, "GET", "/api/devices", "", ""); code != 401 {
		t.Fatalf("无令牌应 401,得到 %d", code)
	}
	if code, _ := api(t, srv, "GET", "/api/devices", "wrong", ""); code != 401 {
		t.Fatalf("错令牌应 401,得到 %d", code)
	}
	if resp, err := http.Get(srv.URL + "/"); err != nil || resp.StatusCode != 200 {
		t.Fatalf("首页应免鉴权: %v", err)
	}

	const tid, lic = "ESP_2M_SIMULATED01", "0123456789abcdef0123456789abcdef"
	sim := newSim(t, infoAddr, tid, lic)
	got := make(chan string, 64)
	go sim.run(got)

	eventually(t, "设备上线并收到首次状态", func() bool {
		d := reg.Get(tid)
		return d != nil && d.View().Online && d.View().Status["filter"] == 45
	})
	if v := reg.Get(tid).View(); v.IP != "10.0.0.9" || v.Info["MAC"] != "AABBCCDDEEFF" {
		t.Errorf("reportDevInfo 未记录: %+v", v)
	}

	// 控制:一次设置多项
	code, r := api(t, srv, "POST", "/api/devices/"+tid+"/command", "secret", `{"airSpeed":30,"sw":1,"mode":2,"exhaustSpeed":25}`)
	if code != 200 {
		t.Fatalf("command: %d %v", code, r)
	}
	st := r["device"].(map[string]any)["status"].(map[string]any)
	if st["sw"] != 1.0 || st["mode"] != 2.0 || st["airSpeed"] != 30.0 || st["exhaustSpeed"] != 25.0 {
		t.Errorf("状态不对: %v", st)
	}
	if n := len(r["sent"].([]any)); n != 4 {
		t.Errorf("应发 4 帧,实际 %d", n)
	}

	// 参数校验
	for _, bad := range []string{`{"airSpeed":71}`, `{"sw":2}`, `{"sw":true}`, `{"sw":1.5}`, `{"sw":"1"}`, `{"foo":1}`, `{}`, `[]`, `not json`} {
		if code, _ := api(t, srv, "POST", "/api/devices/"+tid+"/command", "secret", bad); code != 400 {
			t.Errorf("%s 应 400,得到 %d", bad, code)
		}
	}
	if code, _ := api(t, srv, "POST", "/api/devices/nope/command", "secret", `{"sw":1}`); code != 404 {
		t.Errorf("未知设备应 404,得到 %d", code)
	}

	// 改名并持久化
	if code, _ := api(t, srv, "POST", "/api/devices/"+tid+"/name", "secret", `{"name":"客厅新风"}`); code != 200 {
		t.Errorf("rename: %d", code)
	}
	if code, _ := api(t, srv, "POST", "/api/devices/"+tid+"/name", "secret", `{"name":"  "}`); code != 400 {
		t.Errorf("空名称应 400,得到 %d", code)
	}
	reg2, err := LoadRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	d2 := reg2.Get(tid)
	if d2 == nil || d2.license != lic || len(d2.ctrlKey) != 32 || d2.name != "客厅新风" {
		t.Errorf("持久化不完整: %+v", d2)
	}

	// 同一设备再次登录应顶掉旧连接;license 不一致应被拒绝
	imposter := newSim(t, infoAddr, tid, "ffffffffffffffffffffffffffffffff")
	imposter.c.SetReadDeadline(time.Now().Add(2 * time.Second))
	line, _ := imposter.r.ReadBytes('\n')
	if strings.Contains(string(line), "devLoginResp") {
		t.Errorf("license 不匹配的设备不应登录成功: %s", line)
	}
	if !reg.Get(tid).View().Online {
		t.Errorf("冒名者不应影响真设备的在线状态")
	}

	// 设备断开 -> 离线 -> 指令返回 502
	sim.c.Close()
	eventually(t, "设备离线", func() bool { return !reg.Get(tid).View().Online })
	if code, _ := api(t, srv, "POST", "/api/devices/"+tid+"/command", "secret", `{"sw":0}`); code != 502 {
		t.Errorf("离线应 502,得到 %d", code)
	}
}

func TestUnknownActionIsAnsweredAndRecorded(t *testing.T) {
	reg, _ := LoadRegistry(filepath.Join(t.TempDir(), "d.json"))
	cl, infoAddr := startCloud(t, reg)
	sim := newSim(t, infoAddr, "ESP_NEW", "aa")
	got := make(chan string, 64)
	go sim.run(got)
	eventually(t, "上线", func() bool { d := reg.Get("ESP_NEW"); return d != nil && d.View().Online })
	sim.c.Write(jline(map[string]any{"msgId": 77, "action": "someNewThing", "params": map[string]any{"x": 1}}))
	eventually(t, "收到 someNewThingResp", func() bool {
		for {
			select {
			case a := <-got:
				if a == "someNewThingResp" {
					return true
				}
			default:
				return false
			}
		}
	})
	if u := cl.Unknown(); len(u) != 1 || !strings.Contains(string(u[0]), "someNewThing") {
		t.Errorf("未识别消息应被记录: %v", u)
	}
}

func TestCommandRetriesWhenDeviceDoesNotAnswer(t *testing.T) {
	reg, _ := LoadRegistry(filepath.Join(t.TempDir(), "d.json"))
	cl, infoAddr := startCloud(t, reg)
	cl.CmdTimeout = 300 * time.Millisecond
	cl.PollFirst = time.Hour // 排除自动查询的干扰
	sim := newSim(t, infoAddr, "ESP_RETRY", "bb")
	got := make(chan string, 64)
	go sim.run(got)
	eventually(t, "上线", func() bool { d := reg.Get("ESP_RETRY"); return d != nil && d.View().Online })

	sim.dropNext = 1 // 第一次不应答,第二次应答 -> 应成功
	if _, err := cl.Command(context.Background(), "ESP_RETRY", "setSw", map[string]int{"sw": 1}); err != nil {
		t.Fatalf("重试后应成功: %v", err)
	}
	eventually(t, "状态已更新", func() bool { return reg.Get("ESP_RETRY").View().Status["sw"] == 1 })

	sim.dropNext = 2 // 两次都不应答 -> 应报错
	start := time.Now()
	if _, err := cl.Command(context.Background(), "ESP_RETRY", "setSw", map[string]int{"sw": 0}); err == nil {
		t.Fatal("两次都无应答应返回错误")
	}
	if d := time.Since(start); d < 500*time.Millisecond || d > 3*time.Second {
		t.Errorf("应尝试两次(约 600ms),实际 %v", d)
	}
	if reg.Get("ESP_RETRY").View().Status["sw"] != 1 {
		t.Error("失败的指令不应改变状态")
	}
}
