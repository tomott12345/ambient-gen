// Package jobs runs song jobs. A job has up to two stages, run one after
// the other:
//
//	song     ambient.py composes and renders the WAV (with --preset and --set)
//	release  process-audio's release.py masters it, cuts a Short/Reel, renders
//	         visualizer videos and captions (optional)
//
// Both print "@@progress" events, which are folded into one step list and
// one file list. Job state is persisted to <data>/jobs/<id>/job.json, so
// the job list survives a restart. Files are only served from a stage's
// own manifest, after checking each path is a regular file inside the
// job's output folder. (The structure follows process-audio/web's jobs
// package.)
package jobs

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/tomott12345/ambient-gen/web/internal/describe"
	"github.com/tomott12345/ambient-gen/web/internal/runner"
)

const progressPrefix = "@@progress "

// MethodDefault replaces process-audio's "how it was made" caption text
// (written for live hardware recordings) for generated songs.
const MethodDefault = "Composed and rendered by ambient-gen, a generative music engine in Python: " +
	"every note, sound and arrangement grown from a seed."

type State string

const (
	Queued      State = "queued"
	Running     State = "running"
	Succeeded   State = "succeeded"
	Failed      State = "failed"
	Canceled    State = "canceled"
	Interrupted State = "interrupted"
)

func (s State) Finished() bool { return s != Queued && s != Running }

// Request is what the form sends.
type Request struct {
	Title   string          `json:"title"`
	Preset  string          `json:"preset"`
	Values  map[string]any  `json:"values"`            // ambient.py settings the user changed (dotted paths)
	Release *ReleaseRequest `json:"release,omitempty"` // nil = song only
}

// ReleaseRequest is process-audio's music pipeline form.
type ReleaseRequest struct {
	Type    string         `json:"type"` // ambient | trap | techno | psytrance
	Values  map[string]any `json:"values"`
	Outputs []string       `json:"outputs"`
	Formats []string       `json:"formats"`
}

type Stage struct {
	Name  string   `json:"name"` // song | release
	Label string   `json:"label"`
	Argv  []string `json:"argv"`
}

type Step struct {
	Step   string `json:"step"` // "<stage>/<step>"
	Stage  string `json:"stage"`
	Label  string `json:"label"`
	Status string `json:"status"` // pending | running | done | failed | skipped
	Done   int    `json:"done,omitempty"`
	Total  int    `json:"total,omitempty"`
	Detail string `json:"detail,omitempty"`
}

type File struct {
	Index  int    `json:"index"`
	Stage  string `json:"stage"`
	Kind   string `json:"kind"`
	Format string `json:"format"`
	Name   string `json:"name"`
	Size   int64  `json:"size"`
	Path   string `json:"-"`
}

type Job struct {
	ID         string     `json:"id"`
	Title      string     `json:"title"`
	Preset     string     `json:"preset"`
	Seed       int64      `json:"seed"`
	Request    Request    `json:"request"`
	Stages     []Stage    `json:"stages"`
	State      State      `json:"state"`
	Error      string     `json:"error,omitempty"`
	ExitCode   *int       `json:"exit_code,omitempty"`
	CreatedAt  time.Time  `json:"created_at"`
	StartedAt  *time.Time `json:"started_at,omitempty"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`
	Steps      []Step     `json:"steps"`
	Files      []File     `json:"files"`
	FilePaths  []string   `json:"file_paths,omitempty"` // stored, never sent to clients
}

// Public is the client view: no server paths.
func (j *Job) Public() map[string]any {
	b, _ := json.Marshal(j)
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	delete(m, "file_paths")
	cmds := []map[string]string{}
	for _, s := range j.Stages {
		cmds = append(cmds, map[string]string{"stage": s.Name, "label": s.Label, "command": DisplayArgv(s.Argv)})
	}
	m["commands"] = cmds
	delete(m, "stages")
	return m
}

// DisplayArgv shows a command with paths shortened to their base names.
func DisplayArgv(argv []string) string {
	parts := make([]string, 0, len(argv))
	for i, a := range argv {
		switch {
		case i == 0:
			parts = append(parts, "python3")
		case strings.HasPrefix(a, "--out-dir="):
			parts = append(parts, "--out-dir=<job>")
		case filepath.IsAbs(a):
			parts = append(parts, filepath.Base(a))
		default:
			if k, v, ok := strings.Cut(a, "="); ok && filepath.IsAbs(v) {
				a = k + "=" + filepath.Base(v)
			}
			if strings.ContainsAny(a, " '\"") {
				a = "'" + strings.ReplaceAll(a, "'", `'\''`) + "'"
			}
			parts = append(parts, a)
		}
	}
	return strings.Join(parts, " ")
}

// Message is what subscribers receive: a job snapshot or a log line.
type Message struct {
	Type string         `json:"type"` // "job" | "log"
	Job  map[string]any `json:"job,omitempty"`
	Line string         `json:"line,omitempty"`
}

type entry struct {
	mu     sync.Mutex
	job    *Job
	cancel context.CancelFunc
	logs   []string
	subs   map[chan Message]struct{}
}

type Manager struct {
	dir     string
	desc    *describe.Store
	queue   chan string
	mu      sync.Mutex
	jobs    map[string]*entry
	wg      sync.WaitGroup
	baseCtx context.Context
	stop    context.CancelFunc
}

const logTail = 400

func NewManager(dataDir string, ds *describe.Store, workers int) (*Manager, error) {
	dir := filepath.Join(dataDir, "jobs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	ctx, stop := context.WithCancel(context.Background())
	m := &Manager{dir: dir, desc: ds, queue: make(chan string, 1000), jobs: map[string]*entry{}, baseCtx: ctx, stop: stop}
	if err := m.load(); err != nil {
		return nil, err
	}
	if workers < 1 {
		workers = 1
	}
	for i := 0; i < workers; i++ {
		m.wg.Add(1)
		go m.worker()
	}
	return m, nil
}

func (m *Manager) Shutdown() {
	m.stop()
	close(m.queue)
	m.wg.Wait()
}

func (m *Manager) jobDir(id string) string { return filepath.Join(m.dir, id) }
func (m *Manager) OutDir(id string) string { return filepath.Join(m.dir, id, "out") }

// ---------------------------------------------------------------- building

var idRe = regexp.MustCompile(`^[0-9a-f]{20}$`)

func ValidID(id string) bool { return idRe.MatchString(id) }

func newID() string {
	b := make([]byte, 10)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

var slugRe = regexp.MustCompile(`[^a-z0-9]+`)

func slug(s string) string {
	s = strings.Trim(slugRe.ReplaceAllString(strings.ToLower(s), "_"), "_")
	if len(s) > 48 {
		s = strings.TrimRight(s[:48], "_")
	}
	return s
}

// Build validates a request and returns the commands it will run, plus the
// seed it will use (picked here when the user left it random, so the job
// always records it). outDir is the job's output folder.
func (m *Manager) Build(ctx context.Context, req *Request, outDir string) ([]Stage, int64, error) {
	snap, err := m.desc.Get(ctx)
	if err != nil {
		return nil, 0, err
	}
	c := snap.Compose
	preset, ok := c.Preset(req.Preset)
	if !ok {
		return nil, 0, fmt.Errorf("unknown preset %q", req.Preset)
	}
	req.Title = strings.TrimSpace(req.Title)
	if len(req.Title) > 120 {
		return nil, 0, errors.New("the title is too long (120 characters at most)")
	}
	if strings.ContainsAny(req.Title, "\x00\n\r") {
		return nil, 0, errors.New("the title has invalid characters")
	}
	if req.Values == nil {
		req.Values = map[string]any{}
	}

	seed := int64(0)
	if v, ok := req.Values["seed"].(float64); ok && v > 0 {
		seed = int64(v)
	}
	if seed == 0 {
		n, _ := rand.Int(rand.Reader, big.NewInt(1<<31-2))
		seed = n.Int64() + 1
	}
	req.Values["seed"] = float64(seed)

	keys := make([]string, 0, len(req.Values))
	for k := range req.Values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	sets := []string{}
	for _, k := range keys {
		text, err := c.SetArg(k, req.Values[k])
		if err != nil {
			return nil, 0, err
		}
		sets = append(sets, "--set", k+"="+text)
	}

	name := slug(req.Title)
	if name == "" {
		name = preset.Name
	}
	song := filepath.Join(outDir, "song", fmt.Sprintf("%s_%d.wav", name, seed))
	compose := append([]string{m.desc.Python, filepath.Join(m.desc.AmbientDir, "ambient.py"),
		"--preset", filepath.Join(m.desc.AmbientDir, "presets", preset.Name+".json"),
		"--out", song, "--progress-json"}, sets...)
	stages := []Stage{{Name: "song", Label: "Song", Argv: compose}}

	if req.Release != nil {
		if !snap.Release.Available {
			return nil, 0, fmt.Errorf("%w: %s", describe.ErrNoRelease, snap.Release.Reason)
		}
		r := req.Release
		if r.Values == nil {
			r.Values = map[string]any{}
		}
		if t, _ := r.Values["title"].(string); strings.TrimSpace(t) == "" && req.Title != "" {
			r.Values["title"] = req.Title
		}
		if _, set := r.Values["method"]; !set {
			r.Values["method"] = MethodDefault
		}
		argv, err := m.releaseArgv(ctx, r, song, filepath.Join(outDir, "release"))
		if err != nil {
			return nil, 0, err
		}
		stages = append(stages, Stage{Name: "release", Label: "Master & videos", Argv: argv})
	}
	return stages, seed, nil
}

// releaseArgv asks process-audio's own build_argv (via release_bridge.py)
// for the release command, so its rules live in one place.
func (m *Manager) releaseArgv(ctx context.Context, r *ReleaseRequest, input, outDir string) ([]string, error) {
	body, _ := json.Marshal(map[string]any{"type": r.Type, "values": r.Values, "outputs": r.Outputs,
		"formats": r.Formats, "input": input, "out_dir": outDir})
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, m.desc.Python, m.desc.BridgePath, "--process-audio", m.desc.ProcessAudio, "argv")
	cmd.Stdin = bytes.NewReader(body)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("couldn't build the release command: %s", runner.LastLine(stderr.String()))
	}
	var res struct {
		Argv  []string `json:"argv"`
		Error string   `json:"error"`
	}
	if err := json.Unmarshal(out, &res); err != nil {
		return nil, fmt.Errorf("couldn't build the release command: %v", err)
	}
	if res.Error != "" {
		return nil, errors.New("release: " + res.Error)
	}
	return res.Argv, nil
}

// Create validates the request, records the job and queues it.
func (m *Manager) Create(ctx context.Context, req Request) (*Job, error) {
	id := newID()
	stages, seed, err := m.Build(ctx, &req, m.OutDir(id))
	if err != nil {
		return nil, err
	}
	for _, d := range []string{"song", "release"} {
		if err := os.MkdirAll(filepath.Join(m.OutDir(id), d), 0o755); err != nil {
			return nil, err
		}
	}
	title := req.Title
	if title == "" {
		title = req.Preset
	}
	j := &Job{ID: id, Title: title, Preset: req.Preset, Seed: seed, Request: req, Stages: stages, State: Queued,
		CreatedAt: time.Now().UTC(), Files: []File{},
		Steps: []Step{{Step: "song/compose", Stage: "song", Label: "Composing", Status: "pending"},
			{Step: "song/render", Stage: "song", Label: "Rendering audio", Status: "pending"}}}
	if len(stages) > 1 {
		j.Steps = append(j.Steps, Step{Step: "release/all", Stage: "release", Label: "Mastering & videos", Status: "pending"})
	}
	en := &entry{job: j, subs: map[chan Message]struct{}{}}
	m.mu.Lock()
	m.jobs[id] = en
	m.mu.Unlock()
	en.mu.Lock()
	m.persist(en)
	en.mu.Unlock()
	select {
	case m.queue <- id:
	default:
		return nil, errors.New("the job queue is full -- try again later")
	}
	return j, nil
}

// ---------------------------------------------------------------- state

func (m *Manager) load() error {
	ents, err := os.ReadDir(m.dir)
	if err != nil {
		return err
	}
	for _, e := range ents {
		if !e.IsDir() || !ValidID(e.Name()) {
			continue
		}
		b, err := os.ReadFile(filepath.Join(m.dir, e.Name(), "job.json"))
		if err != nil {
			continue
		}
		var j Job
		if json.Unmarshal(b, &j) != nil {
			continue
		}
		for i := range j.Files {
			if i < len(j.FilePaths) {
				j.Files[i].Path = j.FilePaths[i]
			}
		}
		if !j.State.Finished() {
			j.State = Interrupted
			j.Error = "the server stopped while this job was " + map[bool]string{true: "running", false: "queued"}[j.StartedAt != nil]
			now := time.Now().UTC()
			j.FinishedAt = &now
			for i := range j.Steps {
				if j.Steps[i].Status == "running" {
					j.Steps[i].Status = "failed"
				}
			}
		}
		en := &entry{job: &j, subs: map[chan Message]struct{}{}, logs: tailLines(filepath.Join(m.dir, j.ID, "log.txt"), logTail)}
		m.jobs[j.ID] = en
		m.persist(en)
	}
	return nil
}

func (m *Manager) persist(en *entry) {
	j := en.job
	j.FilePaths = j.FilePaths[:0]
	for _, f := range j.Files {
		j.FilePaths = append(j.FilePaths, f.Path)
	}
	b, _ := json.MarshalIndent(j, "", "  ")
	tmp := filepath.Join(m.jobDir(j.ID), "job.json.tmp")
	if err := os.WriteFile(tmp, b, 0o644); err == nil {
		_ = os.Rename(tmp, filepath.Join(m.jobDir(j.ID), "job.json"))
	}
}

func (m *Manager) get(id string) (*entry, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	en, ok := m.jobs[id]
	return en, ok
}

func (m *Manager) Get(id string) (map[string]any, bool) {
	en, ok := m.get(id)
	if !ok {
		return nil, false
	}
	en.mu.Lock()
	defer en.mu.Unlock()
	return en.job.Public(), true
}

func (m *Manager) File(id string, index int) (*File, bool) {
	en, ok := m.get(id)
	if !ok {
		return nil, false
	}
	en.mu.Lock()
	defer en.mu.Unlock()
	if index < 0 || index >= len(en.job.Files) {
		return nil, false
	}
	f := en.job.Files[index]
	return &f, true
}

func (m *Manager) Files(id string) ([]File, string, bool) {
	en, ok := m.get(id)
	if !ok {
		return nil, "", false
	}
	en.mu.Lock()
	defer en.mu.Unlock()
	return append([]File(nil), en.job.Files...), en.job.Title, true
}

func (m *Manager) List() []map[string]any {
	m.mu.Lock()
	ens := make([]*entry, 0, len(m.jobs))
	for _, en := range m.jobs {
		ens = append(ens, en)
	}
	m.mu.Unlock()
	out := make([]map[string]any, 0, len(ens))
	for _, en := range ens {
		en.mu.Lock()
		out = append(out, en.job.Public())
		en.mu.Unlock()
	}
	sort.Slice(out, func(a, b int) bool { return out[a]["created_at"].(string) > out[b]["created_at"].(string) })
	return out
}

var ErrNotFound = errors.New("no such job")
var ErrBusy = errors.New("the job is still running -- cancel it first")

func (m *Manager) Cancel(id string) error {
	en, ok := m.get(id)
	if !ok {
		return ErrNotFound
	}
	en.mu.Lock()
	defer en.mu.Unlock()
	switch en.job.State {
	case Queued:
		m.finish(en, Canceled, "canceled before it started", nil)
	case Running:
		if en.cancel != nil {
			en.cancel()
		}
	}
	return nil
}

func (m *Manager) Delete(id string) error {
	en, ok := m.get(id)
	if !ok {
		return ErrNotFound
	}
	en.mu.Lock()
	if !en.job.State.Finished() {
		en.mu.Unlock()
		return ErrBusy
	}
	en.mu.Unlock()
	m.mu.Lock()
	delete(m.jobs, id)
	m.mu.Unlock()
	return os.RemoveAll(m.jobDir(id))
}

func (m *Manager) Subscribe(id string) (<-chan Message, func(), bool) {
	en, ok := m.get(id)
	if !ok {
		return nil, nil, false
	}
	ch := make(chan Message, 512)
	en.mu.Lock()
	for _, l := range en.logs {
		ch <- Message{Type: "log", Line: l}
	}
	ch <- Message{Type: "job", Job: en.job.Public()}
	en.subs[ch] = struct{}{}
	en.mu.Unlock()
	return ch, func() {
		en.mu.Lock()
		delete(en.subs, ch)
		en.mu.Unlock()
	}, true
}

func (m *Manager) publish(en *entry, msg Message) {
	for ch := range en.subs {
		select {
		case ch <- msg:
		default:
		}
	}
}

func (m *Manager) snapshot(en *entry) { m.publish(en, Message{Type: "job", Job: en.job.Public()}) }

// finish must be called with en.mu held.
func (m *Manager) finish(en *entry, st State, msg string, code *int) {
	now := time.Now().UTC()
	en.job.State, en.job.Error, en.job.FinishedAt, en.job.ExitCode = st, msg, &now, code
	for i := range en.job.Steps {
		switch en.job.Steps[i].Status {
		case "running":
			if st == Succeeded {
				en.job.Steps[i].Status = "done"
			} else {
				en.job.Steps[i].Status = "failed"
			}
		case "pending":
			if st != Succeeded {
				en.job.Steps[i].Status = "skipped"
			}
		}
	}
	m.persist(en)
	m.snapshot(en)
}

// ---------------------------------------------------------------- running

func (m *Manager) worker() {
	defer m.wg.Done()
	for id := range m.queue {
		if en, ok := m.get(id); ok {
			m.run(en)
		}
	}
}

func (m *Manager) run(en *entry) {
	en.mu.Lock()
	if en.job.State != Queued {
		en.mu.Unlock()
		return
	}
	if m.baseCtx.Err() != nil {
		m.finish(en, Interrupted, "the server stopped before this job started", nil)
		en.mu.Unlock()
		return
	}
	ctx, cancel := context.WithCancel(m.baseCtx)
	defer cancel()
	en.cancel = cancel
	now := time.Now().UTC()
	en.job.State, en.job.StartedAt = Running, &now
	stages := append([]Stage(nil), en.job.Stages...)
	outDir := m.OutDir(en.job.ID)
	m.persist(en)
	m.snapshot(en)
	en.mu.Unlock()

	logf, _ := os.Create(filepath.Join(m.jobDir(en.job.ID), "log.txt"))
	if logf != nil {
		defer logf.Close()
	}
	code := 0
	for _, st := range stages {
		var lastErr string
		var runErr error
		stageDir := filepath.Join(outDir, st.Name)
		m.logLine(en, logf, fmt.Sprintf("== %s: %s", st.Label, DisplayArgv(st.Argv)))
		code, runErr = runner.Stream(ctx, st.Argv, stageDir, progressPrefix,
			func(ev runner.Event) {
				en.mu.Lock()
				defer en.mu.Unlock()
				if apply(en.job, st.Name, ev, outDir) {
					m.persist(en)
				}
				m.snapshot(en)
			},
			func(stream, line string) {
				if l := strings.TrimSpace(line); strings.HasPrefix(l, "error:") {
					lastErr = strings.TrimSpace(strings.TrimPrefix(l, "error:"))
				} else if stream == "stderr" && l != "" && !strings.HasPrefix(l, "rendering") {
					lastErr = l // ambient.py reports problems as a plain last line
				}
				m.logLine(en, logf, line)
			})

		en.mu.Lock()
		switch {
		case m.baseCtx.Err() != nil:
			m.finish(en, Interrupted, "the server stopped while this job was running", &code)
		case ctx.Err() != nil:
			m.finish(en, Canceled, "canceled", &code)
		case runErr != nil || code != 0:
			msg := lastErr
			if msg == "" {
				msg = fmt.Sprintf("%s exited with code %d", st.Label, code)
			}
			m.finish(en, Failed, st.Label+": "+msg, &code)
		case !hasStageFiles(en.job, st.Name):
			m.finish(en, Failed, st.Label+" finished but reported no output files", &code)
		default:
			for i := range en.job.Steps { // a stage's leftovers are done when it exits cleanly
				if en.job.Steps[i].Stage == st.Name && en.job.Steps[i].Status != "done" {
					en.job.Steps[i].Status = "done"
				}
			}
			m.persist(en)
			m.snapshot(en)
			en.mu.Unlock()
			continue
		}
		en.mu.Unlock()
		log.Printf("job %s %s in stage %s (exit %d)", en.job.ID, en.job.State, st.Name, code)
		return
	}
	en.mu.Lock()
	m.finish(en, Succeeded, "", &code)
	en.mu.Unlock()
	log.Printf("job %s succeeded", en.job.ID)
}

func (m *Manager) logLine(en *entry, logf *os.File, line string) {
	if logf != nil {
		fmt.Fprintln(logf, line)
	}
	en.mu.Lock()
	en.logs = append(en.logs, line)
	if len(en.logs) > logTail {
		en.logs = en.logs[len(en.logs)-logTail:]
	}
	m.publish(en, Message{Type: "log", Line: line})
	en.mu.Unlock()
}

func hasStageFiles(j *Job, stage string) bool {
	for _, f := range j.Files {
		if f.Stage == stage {
			return true
		}
	}
	return false
}

// apply folds one progress event from a stage into the job. Returns true
// if it changed something worth persisting (not every progress tick).
func apply(j *Job, stage string, ev runner.Event, outDir string) bool {
	id := func(name string) string { return stage + "/" + name }
	find := func(sid string) *Step {
		for i := range j.Steps {
			if j.Steps[i].Step == sid {
				return &j.Steps[i]
			}
		}
		return nil
	}
	if parent := ev.Str("parent"); parent != "" {
		// a child process working on behalf of one of the stage's steps
		st := find(id(parent))
		if st == nil {
			return false
		}
		switch ev.Str("event") {
		case "step_start":
			st.Detail = ev.Str("label")
			st.Done, st.Total = 0, 0
		case "progress":
			if d, ok := ev.Int("done"); ok {
				st.Done = d
			}
			if t, ok := ev.Int("total"); ok {
				st.Total = t
			}
		}
		return false
	}
	name := ev.Str("step")
	switch ev.Str("event") {
	case "plan":
		// the stage's real step list replaces its placeholder, in place
		raw, _ := ev["steps"].([]any)
		fresh := []Step{}
		for _, s := range raw {
			if sm, ok := s.(map[string]any); ok {
				sid, _ := sm["step"].(string)
				label, _ := sm["label"].(string)
				fresh = append(fresh, Step{Step: id(sid), Stage: stage, Label: label, Status: "pending"})
			}
		}
		out, inserted := []Step{}, false
		for _, s := range j.Steps {
			if s.Stage == stage {
				if !inserted {
					out, inserted = append(out, fresh...), true
				}
				continue
			}
			out = append(out, s)
		}
		if !inserted {
			out = append(out, fresh...)
		}
		j.Steps = out
		return true
	case "step_start":
		if st := find(id(name)); st != nil {
			st.Status = "running"
		} else {
			j.Steps = append(j.Steps, Step{Step: id(name), Stage: stage, Label: ev.Str("label"), Status: "running"})
		}
		return true
	case "step_end":
		if st := find(id(name)); st != nil {
			st.Status, st.Detail = "done", ""
			if st.Total > 0 {
				st.Done = st.Total
			}
		}
		return true
	case "step_error":
		if st := find(id(name)); st != nil {
			st.Status, st.Detail = "failed", ev.Str("message")
		}
		return true
	case "progress":
		if st := find(id(name)); st != nil {
			if d, ok := ev.Int("done"); ok {
				st.Done = d
			}
			if t, ok := ev.Int("total"); ok {
				st.Total = t
			}
			if det := ev.Str("detail"); det != "" && det != "frames" {
				st.Detail = det
			}
		}
		return false
	case "manifest":
		// a stage's last manifest is its list of deliverables
		files, _ := ev["files"].([]any)
		kept := []File{}
		for _, f := range j.Files {
			if f.Stage != stage {
				kept = append(kept, f)
			}
		}
		for _, f := range files {
			fm, ok := f.(map[string]any)
			if !ok {
				continue
			}
			p, _ := fm["path"].(string)
			safe, size, ok := inside(outDir, p)
			if !ok {
				log.Printf("job %s: ignoring manifest path outside the job folder: %q", j.ID, p)
				continue
			}
			kind, _ := fm["kind"].(string)
			format, _ := fm["format"].(string)
			if stage == "song" && kind == "master" {
				kind = "song"
			}
			kept = append(kept, File{Stage: stage, Kind: kind, Format: format, Name: filepath.Base(safe), Size: size, Path: safe})
		}
		sort.SliceStable(kept, func(a, b int) bool { return kept[a].Stage == "song" && kept[b].Stage != "song" })
		for i := range kept {
			kept[i].Index = i
		}
		j.Files = kept
		return true
	}
	return false
}

func tailLines(path string, n int) []string {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	if len(lines) == 1 && lines[0] == "" {
		return nil
	}
	return lines
}

// inside checks that p is a regular file (not a symlink) within dir.
func inside(dir, p string) (string, int64, bool) {
	if p == "" || !filepath.IsAbs(p) {
		return "", 0, false
	}
	realDir, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return "", 0, false
	}
	clean := filepath.Clean(p)
	realP, err := filepath.EvalSymlinks(filepath.Dir(clean))
	if err != nil {
		return "", 0, false
	}
	full := filepath.Join(realP, filepath.Base(clean))
	rel, err := filepath.Rel(realDir, full)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", 0, false
	}
	st, err := os.Lstat(full)
	if err != nil || !st.Mode().IsRegular() {
		return "", 0, false
	}
	return full, st.Size(), true
}
