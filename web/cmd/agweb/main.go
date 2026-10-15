// agweb: the ambient-gen web app. Compose generative songs with an Easy or
// an Advanced form, then optionally master them and make visualizer
// videos, Shorts and captions with process-audio's music pipeline.
//
//	go build -o bin/agweb ./cmd/agweb && bin/agweb      # http://127.0.0.1:8766
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/tomott12345/ambient-gen/web/internal/describe"
	"github.com/tomott12345/ambient-gen/web/internal/httpapi"
	"github.com/tomott12345/ambient-gen/web/internal/jobs"
	"github.com/tomott12345/ambient-gen/web/ui"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8766", "listen address (loopback only, outside a container)")
	ambient := flag.String("ambient", "", "folder holding ambient.py (default: found by walking up from here)")
	pa := flag.String("process-audio", "", "folder holding process-audio's pipeline_capabilities.py "+
		"(default: $PROCESS_AUDIO, or a process-audio folder next to ambient-gen); without it, mastering and videos are off")
	python := flag.String("python", "python3", "Python with ambient-gen's (and process-audio's) dependencies")
	data := flag.String("data", "", "folder for jobs (default: <ambient>/web/data)")
	workers := flag.Int("workers", 1, "jobs run at the same time (renders and videos already use several cores)")
	container := flag.Bool("container", false, "running inside a container: allow listening on all interfaces "+
		"(publish the port on the host's 127.0.0.1 only)")
	flag.Parse()

	if *container {
		log.Print("container mode: listening on all container interfaces. Publish the port on the host's " +
			"loopback only (docker run -p 127.0.0.1:8766:8766): this app has no login, and anyone who can " +
			"reach the port can start jobs.")
	} else if err := loopbackOnly(*addr); err != nil {
		log.Fatal(err)
	}

	if *ambient == "" {
		*ambient = findUp("ambient.py")
		if *ambient == "" {
			log.Fatal("couldn't find ambient.py; pass -ambient DIR")
		}
	}
	*ambient, _ = filepath.Abs(*ambient)
	if *pa == "" {
		*pa = os.Getenv("PROCESS_AUDIO")
	}
	if *pa == "" {
		if guess := filepath.Join(filepath.Dir(*ambient), "process-audio"); exists(filepath.Join(guess, "pipeline_capabilities.py")) {
			*pa = guess
		}
	}
	if *pa != "" {
		*pa, _ = filepath.Abs(*pa)
		if !exists(filepath.Join(*pa, "pipeline_capabilities.py")) {
			log.Fatalf("-process-audio %s: pipeline_capabilities.py not found there", *pa)
		}
	}
	if *data == "" {
		*data = filepath.Join(*ambient, "web", "data")
	}

	ds := &describe.Store{Python: *python, AmbientDir: *ambient, ProcessAudio: *pa,
		BridgePath: filepath.Join(*ambient, "web", "release_bridge.py")}
	ctx0, cancel0 := context.WithTimeout(context.Background(), 90*time.Second)
	snap, err := ds.Get(ctx0)
	cancel0()
	if err != nil {
		log.Fatalf("loading settings: %v", err)
	}
	if snap.Release.Available {
		log.Printf("process-audio: %s (mastering, Shorts, videos on)", *pa)
	} else {
		log.Printf("release stage off: %s", snap.Release.Reason)
	}

	jm, err := jobs.NewManager(*data, ds, *workers)
	if err != nil {
		log.Fatal(err)
	}
	srv := &http.Server{Addr: *addr, Handler: (&httpapi.Server{Desc: ds, Jobs: jm, UI: ui.FS()}).Handler(),
		ReadHeaderTimeout: 10 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		log.Printf("ambient-gen web on http://%s  (ambient-gen: %s, data: %s)", *addr, *ambient, *data)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	}()
	<-ctx.Done()
	log.Print("shutting down")
	shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutCtx)
	jm.Shutdown()
}

func loopbackOnly(addr string) error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return err
	}
	if host == "localhost" {
		return nil
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("-addr %q isn't a loopback address; this app has no login, so it only listens on 127.0.0.1 "+
			"(inside Docker use -container and publish the port on the host's 127.0.0.1)", addr)
	}
	return nil
}

// findUp looks for name in the working directory, the executable's folder,
// and their parents.
func findUp(name string) string {
	starts := []string{}
	if wd, err := os.Getwd(); err == nil {
		starts = append(starts, wd)
	}
	if exe, err := os.Executable(); err == nil {
		starts = append(starts, filepath.Dir(exe))
	}
	for _, d := range starts {
		for {
			if exists(filepath.Join(d, name)) {
				return d
			}
			parent := filepath.Dir(d)
			if parent == d {
				break
			}
			d = parent
		}
	}
	return ""
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }
