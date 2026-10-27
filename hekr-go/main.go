// hekr-local:本地版 Hekr 云,让托马仕 THD-4B 新风不依赖官方服务器。
//
// 配置全部来自环境变量(便于 systemd 的 EnvironmentFile):
//
//	HEKR_DATA       设备数据文件,默认 devices.json
//	HEKR_BIND       监听地址,默认 0.0.0.0
//	HEKR_INFO_PORT  设备问路端口,默认 91
//	HEKR_HUB_PORT   设备长连接端口,默认 83
//	HEKR_HUB_HOST   告诉设备的 hub 地址,默认 hub.hekr.me(需让 DNS 解析到本机)
//	HEKR_WEB_PORT   网页/API 端口,默认 8080
//	HEKR_TOKEN      网页/API 访问令牌,留空则不鉴权(仅建议在可信内网使用)
//	HEKR_LOG        日志级别 debug/info/warn,默认 info
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envPort(key string, def int) (int, error) {
	v := env(key, strconv.Itoa(def))
	p, err := strconv.Atoi(v)
	if err != nil || p < 1 || p > 65535 {
		return 0, fmt.Errorf("%s=%q 不是有效端口", key, v)
	}
	return p, nil
}

func serveTCP(ctx context.Context, ln net.Listener, handle func(net.Conn)) {
	go func() { <-ctx.Done(); ln.Close() }()
	for {
		c, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return
			}
			slog.Error("accept 失败", "err", err)
			time.Sleep(100 * time.Millisecond)
			continue
		}
		go handle(c)
	}
}

func run() error {
	var level slog.Level
	if err := level.UnmarshalText([]byte(env("HEKR_LOG", "info"))); err != nil {
		return fmt.Errorf("HEKR_LOG: %w", err)
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level})))

	infoPort, err := envPort("HEKR_INFO_PORT", 91)
	if err != nil {
		return err
	}
	hubPort, err := envPort("HEKR_HUB_PORT", 83)
	if err != nil {
		return err
	}
	webPort, err := envPort("HEKR_WEB_PORT", 8080)
	if err != nil {
		return err
	}
	bind := env("HEKR_BIND", "0.0.0.0")

	reg, err := LoadRegistry(env("HEKR_DATA", "devices.json"))
	if err != nil {
		return fmt.Errorf("读取设备数据: %w", err)
	}
	cloud := NewCloud(reg, env("HEKR_HUB_HOST", "hub.hekr.me"), hubPort)

	infoLn, err := net.Listen("tcp", net.JoinHostPort(bind, strconv.Itoa(infoPort)))
	if err != nil {
		return err
	}
	hubLn, err := net.Listen("tcp", net.JoinHostPort(bind, strconv.Itoa(hubPort)))
	if err != nil {
		return err
	}
	webLn, err := net.Listen("tcp", net.JoinHostPort(bind, strconv.Itoa(webPort)))
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go serveTCP(ctx, infoLn, cloud.ServeInfo)
	go serveTCP(ctx, hubLn, cloud.ServeHub)

	token := env("HEKR_TOKEN", "")
	srv := &http.Server{Handler: NewWebHandler(cloud, reg, token),
		ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 30 * time.Second}
	go func() {
		if err := srv.Serve(webLn); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("web 服务退出", "err", err)
		}
	}()
	slog.Info("已启动", "info", infoPort, "hub", hubPort, "web", webPort, "devices", len(reg.List()), "auth", token != "")

	<-ctx.Done()
	slog.Info("正在退出")
	shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	srv.Shutdown(shutdown)
	return reg.Save()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "错误:", err)
		os.Exit(1)
	}
}
