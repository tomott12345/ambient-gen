// Package runner launches the Python scripts (ambient.py, and process-audio's
// release.py for the release stage). Adapted from process-audio/web. Each run gets its own
// process group, so cancelling kills the Python script AND the ffmpeg /
// visualizer processes it started. Arguments go straight to exec -- never
// through a shell.
package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Event is one decoded "@@progress {...}" line.
type Event map[string]any

func (e Event) Str(k string) string {
	s, _ := e[k].(string)
	return s
}

func (e Event) Int(k string) (int, bool) {
	f, ok := e[k].(float64)
	return int(f), ok
}

const killGrace = 5 * time.Second

func command(ctx context.Context, argv []string, dir string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1", "MPLBACKEND=Agg")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	// on cancel: SIGTERM the whole group, SIGKILL it if still alive later
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
	cmd.WaitDelay = killGrace
	return cmd
}

// reap makes sure nothing in the group outlives the run (a stuck ffmpeg).
func reap(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}

// Stream runs argv, calling onEvent for every progress line and onLog for
// every other line of stdout/stderr. Returns the exit code (-1 if it
// never started or was killed by a signal).
func Stream(ctx context.Context, argv []string, dir, prefix string,
	onEvent func(Event), onLog func(stream, line string)) (int, error) {
	cmd := command(ctx, argv, dir)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return -1, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return -1, err
	}
	if err := cmd.Start(); err != nil {
		return -1, err
	}
	defer reap(cmd)

	var mu sync.Mutex // callbacks see one line at a time
	var wg sync.WaitGroup
	scan := func(r io.Reader, stream string) {
		defer wg.Done()
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64<<10), 4<<20)
		for sc.Scan() {
			line := sc.Text()
			mu.Lock()
			if stream == "stdout" && strings.HasPrefix(line, prefix) {
				var ev Event
				if json.Unmarshal([]byte(strings.TrimSpace(line[len(prefix):])), &ev) == nil {
					onEvent(ev)
					mu.Unlock()
					continue
				}
			}
			onLog(stream, line)
			mu.Unlock()
		}
	}
	wg.Add(2)
	go scan(stdout, "stdout")
	go scan(stderr, "stderr")
	wg.Wait()
	err = cmd.Wait()
	return exitCode(cmd, err), err
}

// Capture runs argv to completion and returns its stdout (for --plan-only
// --json, analysis, and peaks). stderr is returned for error messages.
func Capture(ctx context.Context, argv []string, dir string, timeout time.Duration) ([]byte, string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := command(ctx, argv, dir)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	reap(cmd)
	if ctx.Err() == context.DeadlineExceeded {
		return nil, errb.String(), errors.New("timed out")
	}
	return out.Bytes(), errb.String(), err
}

func exitCode(cmd *exec.Cmd, err error) int {
	if cmd.ProcessState != nil {
		return cmd.ProcessState.ExitCode()
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode()
	}
	return -1
}

// LastLine returns the last non-empty line of s (an "error: ..." message
// from a script, usually).
func LastLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if l := strings.TrimSpace(lines[i]); l != "" {
			return l
		}
	}
	return ""
}
