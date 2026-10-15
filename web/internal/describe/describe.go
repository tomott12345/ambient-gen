// Package describe loads what the web form is built from, straight from
// the Python side so there is one source of truth:
//
//   - compose: `ambient.py --describe` -- every setting (path, type,
//     default, range, choices, help), every preset with its resolved values,
//     every patch with its synth values.
//   - release: process-audio's music pipeline (mastering, Short/Reel clip,
//     visualizer videos, captions, formats), via web/release_bridge.py.
//
// Both are cached and reloaded when a preset, patch or script changes.
package describe

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/tomott12345/ambient-gen/web/internal/runner"
)

// Param is one ambient.py setting (an entry of --list-params).
type Param struct {
	Path    string   `json:"path"`
	Default any      `json:"default"`
	Help    string   `json:"help"`
	Min     *float64 `json:"min"`
	Max     *float64 `json:"max"`
	Choices []any    `json:"choices"`
	Type    string   `json:"type"` // bool | int | float | str
}

type Preset struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Settings    map[string]any `json:"settings"`
	Resolved    map[string]any `json:"resolved"`
}

// Compose is the parsed `ambient.py --describe` output.
type Compose struct {
	Params  []Param        `json:"params"`
	Presets []Preset       `json:"presets"`
	Layers  map[string]any `json:"layers"`
	Kits    map[string]any `json:"kits"`
	Patches []any          `json:"patches"`

	byPath map[string]*Param
	preset map[string]*Preset
}

// Release is process-audio's music pipeline, or why it isn't available.
type Release struct {
	Available bool            `json:"available"`
	Reason    string          `json:"reason,omitempty"`
	Caps      json.RawMessage `json:"caps,omitempty"` // {version, dependencies, formats, run, pipeline}
}

// Snapshot is everything the UI needs; JSON is the /api/capabilities body.
type Snapshot struct {
	Compose *Compose
	Release Release
	JSON    []byte
}

func (c *Compose) Param(path string) (*Param, bool)   { p, ok := c.byPath[path]; return p, ok }
func (c *Compose) Preset(name string) (*Preset, bool) { p, ok := c.preset[name]; return p, ok }

type Store struct {
	Python       string
	AmbientDir   string // folder holding ambient.py
	ProcessAudio string // folder holding process-audio's pipeline_capabilities.py ("" = release disabled)
	BridgePath   string // web/release_bridge.py

	mu      sync.Mutex
	snap    *Snapshot
	stamp   string
	checked time.Time
}

// Get returns the current snapshot, reloading it if the scripts, presets
// or patches changed since it was built (checked at most once a second).
func (s *Store) Get(ctx context.Context) (*Snapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.snap != nil && time.Since(s.checked) < time.Second {
		return s.snap, nil
	}
	s.checked = time.Now()
	stamp := s.fingerprint()
	if s.snap != nil && stamp == s.stamp {
		return s.snap, nil
	}
	snap, err := s.load(ctx)
	if err != nil {
		if s.snap != nil { // keep serving the last good one
			return s.snap, nil
		}
		return nil, err
	}
	s.snap, s.stamp = snap, stamp
	return snap, nil
}

// fingerprint changes when any file the descriptions are built from changes.
func (s *Store) fingerprint() string {
	var b strings.Builder
	add := func(pattern string) {
		files, _ := filepath.Glob(pattern)
		sort.Strings(files)
		for _, f := range files {
			if st, err := os.Stat(f); err == nil {
				fmt.Fprintf(&b, "%s:%d:%d;", f, st.Size(), st.ModTime().UnixNano())
			}
		}
	}
	add(filepath.Join(s.AmbientDir, "*.py"))
	add(filepath.Join(s.AmbientDir, "presets", "*.json"))
	add(filepath.Join(s.AmbientDir, "patches", "*.json"))
	if s.ProcessAudio != "" {
		add(filepath.Join(s.ProcessAudio, "*.py"))
		add(filepath.Join(s.ProcessAudio, "*.json"))
	}
	return b.String()
}

func (s *Store) load(ctx context.Context) (*Snapshot, error) {
	out, stderr, err := runner.Capture(ctx, []string{s.Python, filepath.Join(s.AmbientDir, "ambient.py"), "--describe"},
		s.AmbientDir, 60*time.Second)
	if err != nil {
		return nil, fmt.Errorf("ambient.py --describe failed: %v: %s", err, runner.LastLine(stderr))
	}
	var c Compose
	if err := json.Unmarshal(out, &c); err != nil {
		return nil, fmt.Errorf("ambient.py --describe: bad JSON: %v", err)
	}
	c.byPath = map[string]*Param{}
	for i := range c.Params {
		c.byPath[c.Params[i].Path] = &c.Params[i]
	}
	c.preset = map[string]*Preset{}
	for i := range c.Presets {
		c.preset[c.Presets[i].Name] = &c.Presets[i]
	}

	rel := Release{Available: false, Reason: "process-audio wasn't found, so mastering and videos are off (start with -process-audio DIR)"}
	if s.ProcessAudio != "" {
		out, stderr, err := runner.Capture(ctx, []string{s.Python, s.BridgePath, "--process-audio", s.ProcessAudio, "caps"},
			s.ProcessAudio, 60*time.Second)
		switch {
		case err != nil:
			rel.Reason = "couldn't load process-audio's music pipeline: " + runner.LastLine(stderr)
		case !json.Valid(out):
			rel.Reason = "process-audio returned invalid capabilities"
		default:
			var probe struct {
				Pipeline struct {
					Available      bool   `json:"available"`
					DisabledReason string `json:"disabled_reason"`
				} `json:"pipeline"`
			}
			_ = json.Unmarshal(out, &probe)
			rel = Release{Available: probe.Pipeline.Available, Caps: out}
			if !probe.Pipeline.Available {
				rel.Reason = "process-audio's music pipeline is unavailable: " + probe.Pipeline.DisabledReason
			}
		}
	}
	body, err := json.Marshal(map[string]any{"compose": &c, "release": rel})
	if err != nil {
		return nil, err
	}
	return &Snapshot{Compose: &c, Release: rel, JSON: body}, nil
}

// SetArg validates one compose value against its setting and returns it as
// the text ambient.py's --set expects. Errors read well in the UI.
func (c *Compose) SetArg(path string, v any) (string, error) {
	p, ok := c.byPath[path]
	if !ok {
		return "", fmt.Errorf("unknown setting %q", path)
	}
	var text string
	var num float64
	isNum := false
	switch p.Type {
	case "bool":
		b, ok := v.(bool)
		if !ok {
			return "", fmt.Errorf("%s: expected true or false", path)
		}
		text = strconv.FormatBool(b)
	case "int", "float":
		f, ok := v.(float64)
		if !ok || math.IsNaN(f) || math.IsInf(f, 0) {
			return "", fmt.Errorf("%s: expected a number", path)
		}
		if p.Type == "int" && f != math.Trunc(f) {
			return "", fmt.Errorf("%s: expected a whole number", path)
		}
		num, isNum = f, true
		if p.Type == "int" {
			text = strconv.FormatInt(int64(f), 10)
		} else {
			text = strconv.FormatFloat(f, 'g', -1, 64)
		}
	default:
		str, ok := v.(string)
		if !ok {
			return "", fmt.Errorf("%s: expected text", path)
		}
		if strings.ContainsAny(str, "\x00\n") {
			return "", fmt.Errorf("%s: invalid characters", path)
		}
		text = str
	}
	if len(p.Choices) > 0 {
		found := false
		for _, ch := range p.Choices {
			if fmt.Sprint(ch) == text || (isNum && toFloat(ch) == num) {
				found = true
				break
			}
		}
		if !found {
			return "", fmt.Errorf("%s: %q is not one of the choices", path, text)
		}
	}
	if isNum && p.Min != nil && p.Max != nil && (num < *p.Min || num > *p.Max) {
		return "", fmt.Errorf("%s: %s is outside %g..%g", path, text, *p.Min, *p.Max)
	}
	return text, nil
}

func toFloat(v any) float64 {
	if f, ok := v.(float64); ok {
		return f
	}
	return math.NaN()
}

var ErrNoRelease = errors.New("mastering and videos aren't available")
