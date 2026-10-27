package main

import "testing"

// 这些帧全部来自真机抓包。
func TestBuildMatchesCapture(t *testing.T) {
	cases := []struct {
		tag  string
		seq  byte
		vals map[string]int
		want string
	}{
		{"queryDev", 0x11, nil, "480602110061"},
		{"setSw", 0x19, map[string]int{"sw": 1}, "480802190200016E"},
		{"setMode", 0x1c, map[string]int{"mode": 1}, "4808021C03000172"},
		{"setSw", 0x1e, map[string]int{"sw": 0}, "4808021E02000072"},
	}
	for _, c := range cases {
		got, err := buildFrame(c.tag, c.seq, c.vals)
		if err != nil || got != c.want {
			t.Errorf("%s: got %q err=%v, want %q", c.tag, got, err, c.want)
		}
	}
}

func TestParseReport(t *testing.T) {
	f, err := parseFrame("481901010100000000000000140000000021210001000001BC")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]int{"sw": 0, "temp": 20, "ltcFilter": 33, "eleFilter": 33, "alarm": 1, "highAlarm": 1, "filter": 0, "highFilter": 0}
	for k, v := range want {
		if f.Fields[k] != v {
			t.Errorf("%s = %d, want %d", k, f.Fields[k], v)
		}
	}
	if f.Cmd != "reportDev" || len(f.Fields) != len(reportFields) {
		t.Errorf("cmd=%q fields=%d", f.Cmd, len(f.Fields))
	}
	g, err := parseFrame("481901010100010032280000140000002D49492D00000000BF")
	if err != nil || g.Fields["airSpeed"] != 50 || g.Fields["exhaustSpeed"] != 40 || g.Fields["sw"] != 1 {
		t.Errorf("running unit: %+v err=%v", g, err)
	}
}

func TestParseRejectsBadFrames(t *testing.T) {
	for _, raw := range []string{
		"", "zz", "4806021100", // 太短/非十六进制
		"480602110062", // 校验和错
		"490602110061", // 帧头错
		"480702110061", // 长度字段错
	} {
		if _, err := parseFrame(raw); err == nil {
			t.Errorf("%q 应当被拒绝", raw)
		}
	}
}

func TestBuildValidation(t *testing.T) {
	if _, err := buildFrame("nope", 0, nil); err == nil {
		t.Error("未知指令应报错")
	}
	if _, err := buildFrame("setSw", 0, nil); err == nil {
		t.Error("缺字段应报错")
	}
	if _, err := buildFrame("setSw", 0, map[string]int{"sw": 256}); err == nil {
		t.Error("超范围应报错")
	}
	if _, err := buildFrame("setSw", 0, map[string]int{"sw": -1}); err == nil {
		t.Error("负数应报错")
	}
}
