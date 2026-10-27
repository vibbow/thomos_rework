package main

// HTTP 接口与网页界面。HA 集成也通过这里的 /api 访问。

import (
	"crypto/subtle"
	_ "embed"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

//go:embed static/index.html
var indexHTML []byte

// 设置项 -> 下发的指令、字段名、取值范围。顺序即下发顺序(先开机再调其它)。
var setters = []struct {
	key, tag string
	min, max int
}{
	{"sw", "setSw", 0, 1},
	{"mode", "setMode", 0, 2},
	{"airSpeed", "setSpeed", 0, 70},
	{"exhaustSpeed", "exhaustSpeed", 0, 70},
}

type webServer struct {
	cloud *Cloud
	reg   *Registry
	token string
}

func NewWebHandler(cloud *Cloud, reg *Registry, token string) http.Handler {
	w := &webServer{cloud: cloud, reg: reg, token: token}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", w.index)
	mux.HandleFunc("GET /healthz", func(rw http.ResponseWriter, r *http.Request) { rw.Write([]byte("ok")) })
	mux.HandleFunc("GET /api/devices", w.list)
	mux.HandleFunc("GET /api/devices/{tid}", w.get)
	mux.HandleFunc("POST /api/devices/{tid}/command", w.command)
	mux.HandleFunc("POST /api/devices/{tid}/name", w.rename)
	mux.HandleFunc("GET /api/unknown", w.unknown)
	return w.auth(mux)
}

func (w *webServer) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		if w.token != "" && strings.HasPrefix(r.URL.Path, "/api/") {
			got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			if subtle.ConstantTimeCompare([]byte(got), []byte(w.token)) != 1 {
				writeJSON(rw, http.StatusUnauthorized, map[string]any{"error": "未授权"})
				return
			}
		}
		next.ServeHTTP(rw, r)
	})
}

func writeJSON(rw http.ResponseWriter, code int, v any) {
	rw.Header().Set("Content-Type", "application/json; charset=utf-8")
	rw.WriteHeader(code)
	json.NewEncoder(rw).Encode(v)
}

func (w *webServer) index(rw http.ResponseWriter, r *http.Request) {
	rw.Header().Set("Content-Type", "text/html; charset=utf-8")
	rw.Write(indexHTML)
}

func (w *webServer) list(rw http.ResponseWriter, r *http.Request) {
	writeJSON(rw, http.StatusOK, w.reg.List())
}

func (w *webServer) find(rw http.ResponseWriter, r *http.Request) *Device {
	d := w.reg.Get(r.PathValue("tid"))
	if d == nil {
		writeJSON(rw, http.StatusNotFound, map[string]any{"error": "没有这台设备"})
	}
	return d
}

func (w *webServer) get(rw http.ResponseWriter, r *http.Request) {
	if d := w.find(rw, r); d != nil {
		writeJSON(rw, http.StatusOK, d.View())
	}
}

func (w *webServer) command(rw http.ResponseWriter, r *http.Request) {
	d := w.find(rw, r)
	if d == nil {
		return
	}
	bad := func(msg string) { writeJSON(rw, http.StatusBadRequest, map[string]any{"error": msg}) }

	var body map[string]json.RawMessage
	if err := json.NewDecoder(http.MaxBytesReader(rw, r.Body, 4096)).Decode(&body); err != nil || len(body) == 0 {
		bad("需要非空的 JSON 对象")
		return
	}
	known := map[string]bool{}
	for _, s := range setters {
		known[s.key] = true
	}
	for k := range body {
		if !known[k] {
			bad("不支持的项: " + k)
			return
		}
	}
	type step struct {
		tag, key string
		val      int
	}
	var steps []step
	for _, s := range setters {
		raw, ok := body[s.key]
		if !ok {
			continue
		}
		v, err := strconv.Atoi(string(raw)) // 拒绝 true / 1.5 / "1"
		if err != nil || v < s.min || v > s.max {
			bad(fmt.Sprintf("%s 必须是 %d..%d 的整数", s.key, s.min, s.max))
			return
		}
		steps = append(steps, step{s.tag, s.key, v})
	}

	sent := []string{}
	for _, s := range steps {
		raw, err := w.cloud.Command(r.Context(), d.DevTid, s.tag, map[string]int{s.key: s.val})
		if err != nil {
			writeJSON(rw, http.StatusBadGateway, map[string]any{"error": err.Error(), "sent": sent})
			return
		}
		sent = append(sent, raw)
	}
	select { // 等设备回报新状态
	case <-time.After(600 * time.Millisecond):
	case <-r.Context().Done():
	}
	writeJSON(rw, http.StatusOK, map[string]any{"ok": true, "sent": sent, "device": d.View()})
}

func (w *webServer) rename(rw http.ResponseWriter, r *http.Request) {
	d := w.find(rw, r)
	if d == nil {
		return
	}
	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(rw, r.Body, 4096)).Decode(&body); err != nil {
		writeJSON(rw, http.StatusBadRequest, map[string]any{"error": "需要 JSON"})
		return
	}
	name := strings.TrimSpace(body.Name)
	if n := utf8.RuneCountInString(name); n < 1 || n > 40 {
		writeJSON(rw, http.StatusBadRequest, map[string]any{"error": "名称 1..40 个字符"})
		return
	}
	w.reg.Rename(d.DevTid, name)
	if err := w.reg.Save(); err != nil {
		writeJSON(rw, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(rw, http.StatusOK, d.View())
}

func (w *webServer) unknown(rw http.ResponseWriter, r *http.Request) {
	writeJSON(rw, http.StatusOK, w.cloud.Unknown())
}
