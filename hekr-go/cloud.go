package main

// 仿 Hekr 云端的设备侧服务:
//
//	info 服务(默认 91): getProdInfo —— 告诉设备该连哪个 hub
//	hub  服务(默认 83): devLogin / reportDevInfo / heartbeat / devSend / getTimerList ...
//
// 消息为一行一个 JSON(以 \n 结尾),设备还会夹杂 0x00 字节,需要容忍。

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const (
	idleTimeout  = 10 * time.Minute
	writeTimeout = 10 * time.Second
	maxLine      = 64 * 1024
)

type Cloud struct {
	reg        *Registry
	hubHost    string
	hubPort    int
	PollFirst  time.Duration
	PollEvery  time.Duration
	CmdTimeout time.Duration // 单次指令等待设备应答的时间

	unkMu   sync.Mutex
	unknown []json.RawMessage
}

func NewCloud(reg *Registry, hubHost string, hubPort int) *Cloud {
	return &Cloud{reg: reg, hubHost: hubHost, hubPort: hubPort, PollFirst: 2 * time.Second, PollEvery: time.Minute, CmdTimeout: 4 * time.Second}
}

type message struct {
	MsgID  json.RawMessage `json:"msgId"`
	Action string          `json:"action"`
	Code   int             `json:"code"`
	Params json.RawMessage `json:"params"`
}

// conn 是一台设备的 hub 连接。
type conn struct {
	c   net.Conn
	dev *Device

	wmu    sync.Mutex
	nextID atomic.Int64
	seq    atomic.Uint32

	pmu     sync.Mutex
	pending map[int64]chan error
	closed  bool
}

func newConn(c net.Conn) *conn {
	cn := &conn{c: c, pending: map[int64]chan error{}}
	cn.nextID.Store(1000)
	return cn
}

func (cn *conn) send(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	cn.wmu.Lock()
	defer cn.wmu.Unlock()
	cn.c.SetWriteDeadline(time.Now().Add(writeTimeout))
	_, err = cn.c.Write(append(b, '\n'))
	return err
}

func (cn *conn) nextSeq() byte { return byte(cn.seq.Add(1)) }

// appSend 向设备下发 raw 帧(对应云端的 appSend),等待设备的 appSendResp。
func (cn *conn) appSend(ctx context.Context, raw string) error {
	id := cn.nextID.Add(1)
	ch := make(chan error, 1)
	cn.pmu.Lock()
	if cn.closed {
		cn.pmu.Unlock()
		return errors.New("设备已断开")
	}
	cn.pending[id] = ch
	cn.pmu.Unlock()
	defer func() {
		cn.pmu.Lock()
		delete(cn.pending, id)
		cn.pmu.Unlock()
	}()

	cn.dev.mu.RLock()
	ctrl, tid := cn.dev.ctrlKey, cn.dev.DevTid
	cn.dev.mu.RUnlock()
	err := cn.send(map[string]any{"msgId": id, "action": "appSend", "params": map[string]any{
		"devTid": tid, "ctrlKey": ctrl, "appTid": "hekr-local", "data": map[string]any{"raw": raw}}})
	if err != nil {
		return err
	}
	select {
	case err := <-ch:
		return err
	case <-ctx.Done():
		return errors.New("设备响应超时")
	}
}

func (cn *conn) failPending(err error) {
	cn.pmu.Lock()
	defer cn.pmu.Unlock()
	cn.closed = true
	for _, ch := range cn.pending {
		select {
		case ch <- err:
		default:
		}
	}
}

func trimLine(b []byte) []byte { return bytes.Trim(b, "\x00 \r\t\n") }

func readLine(r *bufio.Reader, c net.Conn) ([]byte, error) {
	c.SetReadDeadline(time.Now().Add(idleTimeout))
	line, err := r.ReadSlice('\n')
	if errors.Is(err, bufio.ErrBufferFull) {
		return nil, fmt.Errorf("单行超过 %d 字节", maxLine)
	}
	return line, err
}

func marshalMsgID(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return raw
}

// ServeInfo 处理 info 端口(设备启动后先来问"我该连哪个服务器")。
func (cl *Cloud) ServeInfo(c net.Conn) {
	defer c.Close()
	cn := newConn(c)
	r := bufio.NewReaderSize(c, maxLine)
	for {
		line, err := readLine(r, c)
		if err != nil {
			return
		}
		line = trimLine(line)
		if len(line) == 0 {
			continue
		}
		var m message
		if json.Unmarshal(line, &m) != nil {
			continue
		}
		slog.Info("info 请求", "from", c.RemoteAddr().String(), "msg", string(line))
		if m.Action == "getProdInfo" {
			cn.send(map[string]any{"msgId": marshalMsgID(m.MsgID),
				"dcInfo": map[string]any{"dc": "gz", "area": "asia", "domain": "hekr.me", "fromArea": "asia", "fromDC": "gz"},
				"action": "getProdInfoResp", "code": 200, "desc": "success",
				"params": map[string]any{"mid": "34uRRMYGJWdX", "workMode": 0, "tokenType": 2,
					"serviceHost": cl.hubHost, "servicePort": cl.hubPort, "encryptType": "None", "connectType": "tcp"}})
		} else {
			cl.recordUnknown("info", c, line)
			cn.send(map[string]any{"msgId": marshalMsgID(m.MsgID), "action": m.Action + "Resp", "code": 200, "desc": "success"})
		}
	}
}

// ServeHub 处理设备的长连接。
func (cl *Cloud) ServeHub(c net.Conn) {
	cn := newConn(c)
	ctx, cancel := context.WithCancel(context.Background())
	defer func() {
		cancel()
		cn.failPending(errors.New("设备断开"))
		if d := cn.dev; d != nil {
			d.mu.Lock()
			if d.conn == cn {
				d.conn = nil
				d.online = false
			}
			d.mu.Unlock()
			slog.Info("设备断开", "devTid", d.DevTid)
		}
		c.Close()
	}()

	r := bufio.NewReaderSize(c, maxLine)
	polling := false
	for {
		line, err := readLine(r, c)
		if err != nil {
			return
		}
		line = trimLine(line)
		if len(line) == 0 {
			continue
		}
		var m message
		if err := json.Unmarshal(line, &m); err != nil {
			slog.Warn("hub 收到非 JSON", "from", c.RemoteAddr().String(), "data", string(line[:min(len(line), 200)]))
			continue
		}
		slog.Debug("hub 收到", "from", c.RemoteAddr().String(), "msg", string(line))
		switch cl.dispatch(cn, &m, line) {
		case actClose:
			return
		case actLoggedIn:
			if !polling {
				polling = true
				go cl.poll(ctx, cn)
			}
		}
		if cn.dev != nil {
			cn.dev.mu.Lock()
			cn.dev.lastSeen = time.Now()
			cn.dev.mu.Unlock()
		}
	}
}

type action int

const (
	actNone action = iota
	actLoggedIn
	actClose
)

func (cl *Cloud) dispatch(cn *conn, m *message, line []byte) action {
	reply := func(extra map[string]any) {
		resp := map[string]any{"msgId": marshalMsgID(m.MsgID), "action": m.Action + "Resp", "code": 200, "desc": "success"}
		for k, v := range extra {
			resp[k] = v
		}
		cn.send(resp)
	}

	switch m.Action {
	case "devLogin":
		var p struct {
			DevTid  string `json:"devTid"`
			License string `json:"license"`
		}
		if json.Unmarshal(m.Params, &p) != nil || p.DevTid == "" {
			slog.Warn("devLogin 缺少 devTid", "msg", string(line))
			return actClose
		}
		dev, created := cl.reg.GetOrCreate(p.DevTid)
		dev.mu.Lock()
		// license 首次登录时记下(信任首次使用),之后必须一致,避免局域网里有人冒名顶替。
		if dev.license != "" && p.License != dev.license {
			dev.mu.Unlock()
			slog.Warn("license 不匹配,拒绝登录(如确为同一台设备,请清空 devices.json 里的 license)", "devTid", p.DevTid)
			return actClose
		}
		changed := created || dev.license != p.License
		dev.license = p.License
		old := dev.conn
		cn.dev = dev // 必须先于 dev.conn 赋值,否则 Command 可能拿到 dev 为空的连接
		dev.conn, dev.online = cn, true
		dev.ip, _, _ = net.SplitHostPort(cn.c.RemoteAddr().String())
		dev.lastSeen = time.Now()
		resp := map[string]any{"msgId": 0, "action": "devLoginResp", "code": 200, "desc": "success", "params": map[string]any{
			"devTid": dev.DevTid, "token": nil, "ctrlKey": dev.ctrlKey, "bindKey": dev.bindKey,
			"forceBind": false, "bind": true, "license": dev.license}}
		dev.mu.Unlock()
		if old != nil && old != cn {
			old.c.Close()
		}
		cn.send(resp)
		if changed {
			if err := cl.reg.Save(); err != nil {
				slog.Error("保存 devices.json 失败", "err", err)
			}
		}
		slog.Info("设备登录", "devTid", dev.DevTid, "ip", dev.ip, "new", created)
		return actLoggedIn
	}

	if cn.dev == nil {
		slog.Warn("未登录的设备发来消息,忽略", "action", m.Action)
		return actClose
	}

	switch m.Action {
	case "heartbeat":
		reply(nil)
	case "reportDevInfo":
		var info map[string]any
		if json.Unmarshal(m.Params, &info) == nil {
			cn.dev.mu.Lock()
			cn.dev.info = info
			if ip, ok := info["lanIp"].(string); ok && ip != "" {
				cn.dev.ip = ip
			}
			cn.dev.mu.Unlock()
		}
		reply(nil)
	case "getTimerList":
		reply(map[string]any{"params": map[string]any{"tasksCount": 0, "taskList": []any{}}})
	case "devSend":
		var p struct {
			Data struct {
				Raw string `json:"raw"`
			} `json:"data"`
		}
		if json.Unmarshal(m.Params, &p) == nil && p.Data.Raw != "" {
			if f, err := parseFrame(p.Data.Raw); err != nil {
				slog.Warn("帧无效", "devTid", cn.dev.DevTid, "err", err)
			} else if len(f.Fields) > 0 {
				cn.dev.mu.Lock()
				for k, v := range f.Fields {
					cn.dev.status[k] = v
				}
				cn.dev.mu.Unlock()
			}
		}
		reply(nil)
	case "appSendResp":
		if id, err := strconv.ParseInt(string(m.MsgID), 10, 64); err == nil {
			cn.pmu.Lock()
			ch := cn.pending[id]
			cn.pmu.Unlock()
			if ch != nil {
				var err error
				if m.Code != 200 {
					err = fmt.Errorf("设备拒绝指令 (code %d)", m.Code)
				}
				select {
				case ch <- err:
				default:
				}
			}
		}
	default:
		if len(m.Action) > 4 && m.Action[len(m.Action)-4:] == "Resp" {
			return actNone // 设备对我们请求的应答,不需要再回
		}
		cl.recordUnknown("hub", cn.c, line)
		reply(nil)
	}
	return actNone
}

func (cl *Cloud) recordUnknown(where string, c net.Conn, line []byte) {
	slog.Warn("未识别的消息", "where", where, "from", c.RemoteAddr().String(), "msg", string(line))
	cl.unkMu.Lock()
	defer cl.unkMu.Unlock()
	cl.unknown = append(cl.unknown, json.RawMessage(bytes.Clone(line)))
	if len(cl.unknown) > 50 {
		cl.unknown = cl.unknown[len(cl.unknown)-50:]
	}
}

func (cl *Cloud) Unknown() []json.RawMessage {
	cl.unkMu.Lock()
	defer cl.unkMu.Unlock()
	return append([]json.RawMessage{}, cl.unknown...)
}

// poll 定期向设备查询状态(设备平时只在状态变化时才上报)。
func (cl *Cloud) poll(ctx context.Context, cn *conn) {
	select {
	case <-ctx.Done():
		return
	case <-time.After(cl.PollFirst):
	}
	t := time.NewTicker(cl.PollEvery)
	defer t.Stop()
	for {
		raw, _ := buildFrame("queryDev", cn.nextSeq(), nil)
		qctx, cancel := context.WithTimeout(ctx, 5*time.Second)
		if err := cn.appSend(qctx, raw); err != nil && ctx.Err() == nil {
			slog.Warn("查询状态失败", "devTid", cn.dev.DevTid, "err", err)
		}
		cancel()
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// Command 向在线设备下发一条指令,返回发出的 raw 帧。
func (cl *Cloud) Command(ctx context.Context, tid, tag string, vals map[string]int) (string, error) {
	dev := cl.reg.Get(tid)
	if dev == nil {
		return "", errors.New("没有这台设备")
	}
	dev.mu.RLock()
	cn := dev.conn
	dev.mu.RUnlock()
	if cn == nil {
		return "", errors.New("设备不在线")
	}
	// 设备偶尔不应答。指令都是"设为某个值",重发是幂等的,所以失败时重试一次。
	var raw string
	var err error
	for attempt := 1; attempt <= 2; attempt++ {
		raw, err = buildFrame(tag, cn.nextSeq(), vals)
		if err != nil {
			return "", err
		}
		start := time.Now()
		cctx, cancel := context.WithTimeout(ctx, cl.CmdTimeout)
		err = cn.appSend(cctx, raw)
		cancel()
		took := time.Since(start).Round(time.Millisecond)
		if err == nil {
			slog.Info("指令已确认", "devTid", tid, "cmd", tag, "raw", raw, "attempt", attempt, "took", took)
			return raw, nil
		}
		slog.Warn("指令失败", "devTid", tid, "cmd", tag, "raw", raw, "attempt", attempt, "took", took, "err", err)
		if ctx.Err() != nil {
			break
		}
	}
	return raw, err
}
