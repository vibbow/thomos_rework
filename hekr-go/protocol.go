package main

// THD-4B 的 raw 帧编解码。字段表来自云端 protocolTemplate,并已用抓包验证。
//
// 帧: 0x48 | 总长度 | 类型(2=下行/1=上行) | 序号 | cmdId | 字段... | 校验和(前面所有字节之和 & 0xFF)

import (
	"encoding/hex"
	"fmt"
	"strings"
)

const frameHeader = 0x48

type field struct {
	name string
	size int
}

// reportDev(cmdId=1) 的字段顺序与字节数。
var reportFields = []field{
	{"flag", 1}, {"sw", 1}, {"mode", 1}, {"airSpeed", 1}, {"exhaustSpeed", 1}, {"PM25", 2},
	{"temp", 1}, {"hum", 1}, {"co2", 2}, {"filter", 1}, {"ltcFilter", 1}, {"eleFilter", 1},
	{"highFilter", 1}, {"alarm", 1}, {"ltcAlarm", 1}, {"eleAlarm", 1}, {"highAlarm", 1},
}

var uplink = map[byte]struct {
	name   string
	fields []field
}{
	1: {"reportDev", reportFields},
	7: {"reportNum", []field{{"flag1", 1}, {"flag2", 1}, {"flag3", 1}, {"flag4", 1}, {"flag5", 1}, {"flag6", 1}, {"flag7", 1}, {"flag8", 1}}},
	9: {"alarm", []field{{"alarm", 1}, {"ltcAlarm", 1}, {"eleAlarm", 1}, {"highAlarm", 1}}},
}

var downlink = map[string]struct {
	id     byte
	fields []field
}{
	"queryDev":     {0, nil},
	"setSw":        {2, []field{{"flag", 1}, {"sw", 1}}},
	"setMode":      {3, []field{{"flag", 1}, {"mode", 1}}},
	"setSpeed":     {4, []field{{"flag", 1}, {"airSpeed", 1}}},
	"exhaustSpeed": {5, []field{{"flag", 1}, {"exhaustSpeed", 1}}},
	"queryNum":     {6, nil},
	"setCutover":   {8, []field{{"flag", 1}}},
}

func checksum(b []byte) byte {
	var s byte
	for _, x := range b {
		s += x
	}
	return s
}

// buildFrame 生成下行 raw(大写十六进制)。缺省的 flag 取 0(集控),其它字段必须给出。
func buildFrame(tag string, seq byte, vals map[string]int) (string, error) {
	spec, ok := downlink[tag]
	if !ok {
		return "", fmt.Errorf("未知指令 %q", tag)
	}
	var payload []byte
	for _, f := range spec.fields {
		v, present := vals[f.name]
		if !present {
			if f.name != "flag" {
				return "", fmt.Errorf("指令 %s 缺少字段 %s", tag, f.name)
			}
			v = 0
		}
		if v < 0 || v >= 1<<(8*f.size) {
			return "", fmt.Errorf("字段 %s 超出范围: %d", f.name, v)
		}
		for i := f.size - 1; i >= 0; i-- {
			payload = append(payload, byte(v>>(8*i)))
		}
	}
	b := append([]byte{frameHeader, byte(6 + len(payload)), 2, seq, spec.id}, payload...)
	b = append(b, checksum(b))
	return strings.ToUpper(hex.EncodeToString(b)), nil
}

type frame struct {
	Type, Seq, CmdID byte
	Cmd              string
	Fields           map[string]int
}

// parseFrame 解析 raw;帧头、长度或校验和不对时返回错误。
func parseFrame(raw string) (*frame, error) {
	b, err := hex.DecodeString(raw)
	if err != nil {
		return nil, fmt.Errorf("不是十六进制: %w", err)
	}
	if len(b) < 6 || b[0] != frameHeader || int(b[1]) != len(b) {
		return nil, fmt.Errorf("帧头或长度错误: %s", raw)
	}
	if checksum(b[:len(b)-1]) != b[len(b)-1] {
		return nil, fmt.Errorf("校验和错误: %s", raw)
	}
	f := &frame{Type: b[2], Seq: b[3], CmdID: b[4], Fields: map[string]int{}}
	spec, known := uplink[b[4]]
	if b[2] != 1 || !known {
		return f, nil
	}
	f.Cmd = spec.name
	payload := b[5 : len(b)-1]
	i := 0
	for _, fd := range spec.fields {
		if i+fd.size > len(payload) {
			break
		}
		v := 0
		for _, x := range payload[i : i+fd.size] {
			v = v<<8 | int(x)
		}
		f.Fields[fd.name] = v
		i += fd.size
	}
	return f, nil
}
