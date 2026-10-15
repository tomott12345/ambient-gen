package jobs

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/tomott12345/ambient-gen/web/internal/describe"
	"github.com/tomott12345/ambient-gen/web/internal/runner"
)

func TestApplyPlansAndManifests(t *testing.T) {
	dir := t.TempDir()
	out := filepath.Join(dir, "out")
	_ = os.MkdirAll(filepath.Join(out, "song"), 0o755)
	_ = os.MkdirAll(filepath.Join(out, "release"), 0o755)
	song := filepath.Join(out, "song", "a.wav")
	master := filepath.Join(out, "release", "m.mp3")
	_ = os.WriteFile(song, []byte("x"), 0o644)
	_ = os.WriteFile(master, []byte("yy"), 0o644)

	j := &Job{ID: "t", Steps: []Step{
		{Step: "song/compose", Stage: "song", Status: "pending"},
		{Step: "release/all", Stage: "release", Label: "Mastering & videos", Status: "pending"}}}
	// the release stage's real plan replaces its placeholder in place
	apply(j, "release", runner.Event{"event": "plan", "steps": []any{
		map[string]any{"step": "master", "label": "Mastering"}, map[string]any{"step": "short", "label": "Short"}}}, out)
	if len(j.Steps) != 3 || j.Steps[1].Step != "release/master" || j.Steps[2].Step != "release/short" {
		t.Fatalf("steps after release plan: %+v", j.Steps)
	}
	apply(j, "release", runner.Event{"event": "step_start", "step": "master"}, out)
	apply(j, "release", runner.Event{"event": "progress", "step": "master", "done": 3.0, "total": 10.0}, out)
	if s := j.Steps[1]; s.Status != "running" || s.Done != 3 || s.Total != 10 {
		t.Fatalf("progress not folded: %+v", s)
	}
	// manifests: song files first; paths outside the job folder are refused
	apply(j, "release", runner.Event{"event": "manifest", "files": []any{
		map[string]any{"kind": "master", "format": "mp3", "path": master},
		map[string]any{"kind": "master", "format": "wav24", "path": "/etc/passwd"}}}, out)
	apply(j, "song", runner.Event{"event": "manifest", "files": []any{map[string]any{"kind": "master", "format": "wav24", "path": song}}}, out)
	if len(j.Files) != 2 || j.Files[0].Kind != "song" || j.Files[1].Stage != "release" || j.Files[1].Index != 1 {
		t.Fatalf("files: %+v", j.Files)
	}
}

func TestDisplayArgvAndSlug(t *testing.T) {
	got := DisplayArgv([]string{"/usr/bin/python3", "/a/ambient.py", "--out", "/data/jobs/x/out/song/s.wav",
		"--title=Hello World", "--out-dir=/data/x", "--input=/a/b.wav"})
	want := "python3 ambient.py --out s.wav '--title=Hello World' --out-dir=<job> --input=b.wav"
	if got != want {
		t.Errorf("DisplayArgv = %q\nwant        %q", got, want)
	}
	if s := slug("  Static Between Stations!! "); s != "static_between_stations" {
		t.Errorf("slug = %q", s)
	}
}

// TestSongJob runs a real (very short) song through the manager.
func TestSongJob(t *testing.T) {
	if testing.Short() {
		t.Skip("integration test")
	}
	ambient, _ := filepath.Abs("../../..")
	python := os.Getenv("AG_PYTHON")
	if python == "" {
		python = "python3"
	}
	if exec.Command(python, "-c", "import numba, scipy").Run() != nil {
		t.Skip("python with numba/scipy not available (set AG_PYTHON)")
	}
	ds := &describe.Store{Python: python, AmbientDir: ambient, BridgePath: filepath.Join(ambient, "web", "release_bridge.py")}
	m, err := NewManager(t.TempDir(), ds, 1)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Shutdown()
	j, err := m.Create(context.Background(), Request{Title: "Test", Preset: "night", Values: map[string]any{"minutes": 0.1, "seed": 7.0}})
	if err != nil {
		t.Fatal(err)
	}
	if j.Seed != 7 {
		t.Fatalf("seed = %d", j.Seed)
	}
	if _, err := m.Create(context.Background(), Request{Preset: "night", Release: &ReleaseRequest{Type: "ambient", Outputs: []string{"master"}}}); err == nil {
		t.Fatal("a release without process-audio should be refused")
	}
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		got, _ := m.Get(j.ID)
		switch State(got["state"].(string)) {
		case Succeeded:
			files, _, _ := m.Files(j.ID)
			if len(files) != 2 || files[0].Kind != "song" || files[0].Size < 1000 {
				t.Fatalf("files: %+v", files)
			}
			return
		case Failed, Canceled, Interrupted:
			t.Fatalf("job ended %s: %v", got["state"], got["error"])
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatal("job didn't finish in time")
}
