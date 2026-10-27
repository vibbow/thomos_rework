package main

// 设备注册表:持久化设备凭据与名称,并保存运行时状态。

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// persisted 是 devices.json 里每台设备的格式。
type persisted struct {
	Name    string `json:"name"`
	CtrlKey string `json:"ctrlKey"`
	BindKey string `json:"bindKey"`
	License string `json:"license"`
}

type Device struct {
	DevTid string

	mu       sync.RWMutex
	name     string
	ctrlKey  string
	bindKey  string
	license  string
	online   bool
	ip       string
	info     map[string]any
	status   map[string]int
	lastSeen time.Time
	conn     *conn
}

// DeviceView 是对外(网页/HA)看到的设备快照。
type DeviceView struct {
	DevTid   string         `json:"devTid"`
	Name     string         `json:"name"`
	Online   bool           `json:"online"`
	IP       string         `json:"ip"`
	LastSeen float64        `json:"lastSeen"`
	Status   map[string]int `json:"status"`
	Info     map[string]any `json:"info"`
}

func (d *Device) View() DeviceView {
	d.mu.RLock()
	defer d.mu.RUnlock()
	st := make(map[string]int, len(d.status))
	for k, v := range d.status {
		st[k] = v
	}
	info := map[string]any{}
	for _, k := range []string{"MAC", "binVer", "rssi", "SSID"} {
		if v, ok := d.info[k]; ok {
			info[k] = v
		}
	}
	var seen float64
	if !d.lastSeen.IsZero() {
		seen = float64(d.lastSeen.UnixMilli()) / 1000
	}
	return DeviceView{DevTid: d.DevTid, Name: d.name, Online: d.online, IP: d.ip, LastSeen: seen, Status: st, Info: info}
}

type Registry struct {
	path   string
	saveMu sync.Mutex

	mu   sync.RWMutex
	devs map[string]*Device
}

func randHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func LoadRegistry(path string) (*Registry, error) {
	r := &Registry{path: path, devs: map[string]*Device{}}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return r, nil
	}
	if err != nil {
		return nil, err
	}
	var m map[string]persisted
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	for tid, p := range m {
		d := r.newDevice(tid)
		d.name, d.ctrlKey, d.bindKey, d.license = p.Name, p.CtrlKey, p.BindKey, p.License
		if d.name == "" {
			d.name = tid
		}
		if d.ctrlKey == "" {
			d.ctrlKey = randHex(16)
		}
		if d.bindKey == "" {
			d.bindKey = randHex(16)
		}
		r.devs[tid] = d
	}
	return r, nil
}

func (r *Registry) newDevice(tid string) *Device {
	return &Device{DevTid: tid, name: tid, ctrlKey: randHex(16), bindKey: randHex(16),
		info: map[string]any{}, status: map[string]int{}}
}

func (r *Registry) Save() error {
	r.saveMu.Lock()
	defer r.saveMu.Unlock()
	r.mu.RLock()
	m := make(map[string]persisted, len(r.devs))
	for tid, d := range r.devs {
		d.mu.RLock()
		m[tid] = persisted{d.name, d.ctrlKey, d.bindKey, d.license}
		d.mu.RUnlock()
	}
	r.mu.RUnlock()
	data, err := json.MarshalIndent(m, "", " ")
	if err != nil {
		return err
	}
	if dir := filepath.Dir(r.path); dir != "." {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return err
		}
	}
	tmp := r.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, r.path)
}

func (r *Registry) Get(tid string) *Device {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.devs[tid]
}

// GetOrCreate 返回设备,不存在则新建。created 表示是否为新建。
func (r *Registry) GetOrCreate(tid string) (d *Device, created bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if d = r.devs[tid]; d != nil {
		return d, false
	}
	d = r.newDevice(tid)
	r.devs[tid] = d
	return d, true
}

func (r *Registry) List() []DeviceView {
	r.mu.RLock()
	out := make([]DeviceView, 0, len(r.devs))
	for _, d := range r.devs {
		out = append(out, d.View())
	}
	r.mu.RUnlock()
	sort.Slice(out, func(i, j int) bool {
		if out[i].Name != out[j].Name {
			return out[i].Name < out[j].Name
		}
		return out[i].DevTid < out[j].DevTid
	})
	return out
}

func (r *Registry) Rename(tid, name string) bool {
	d := r.Get(tid)
	if d == nil {
		return false
	}
	d.mu.Lock()
	d.name = name
	d.mu.Unlock()
	return true
}
