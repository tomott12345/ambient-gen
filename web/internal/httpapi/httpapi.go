// Package httpapi serves the JSON API, live job events (server-sent
// events), downloads, and the embedded UI.
//
// Every POST/DELETE must carry "X-Requested-With: ambient-gen" and, if the
// browser sends an Origin, it must be this server's own -- so another site
// open in the same browser can't start jobs here.
package httpapi

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/tomott12345/ambient-gen/web/internal/describe"
	"github.com/tomott12345/ambient-gen/web/internal/jobs"
)

const csrfHeader = "X-Requested-With"
const csrfValue = "ambient-gen"

type Server struct {
	Desc *describe.Store
	Jobs *jobs.Manager
	UI   fs.FS
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true})
	})
	mux.HandleFunc("GET /api/capabilities", s.capabilities)
	mux.HandleFunc("POST /api/plan", s.plan)
	mux.HandleFunc("POST /api/jobs", s.createJob)
	mux.HandleFunc("GET /api/jobs", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, s.Jobs.List()) })
	mux.HandleFunc("GET /api/jobs/{id}", s.getJob)
	mux.HandleFunc("DELETE /api/jobs/{id}", s.deleteJob)
	mux.HandleFunc("POST /api/jobs/{id}/cancel", s.cancelJob)
	mux.HandleFunc("GET /api/jobs/{id}/events", s.events)
	mux.HandleFunc("GET /api/jobs/{id}/files/{n}", s.file)
	mux.HandleFunc("GET /api/jobs/{id}/zip", s.zip)
	mux.Handle("GET /", http.FileServerFS(s.UI))
	return secure(mux)
}

// secure adds security headers and the CSRF check on state-changing calls.
func secure(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "+
			"style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			if r.Header.Get(csrfHeader) != csrfValue {
				httpError(w, 403, "missing "+csrfHeader+" header")
				return
			}
			if o := r.Header.Get("Origin"); o != "" {
				u, err := url.Parse(o)
				if err != nil || u.Host != r.Host {
					httpError(w, 403, "cross-origin request refused")
					return
				}
			}
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func httpError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (s *Server) capabilities(w http.ResponseWriter, r *http.Request) {
	snap, err := s.Desc.Get(r.Context())
	if err != nil {
		httpError(w, 500, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(snap.JSON)
}

func decode(r *http.Request, v any) error {
	r.Body = http.MaxBytesReader(nil, r.Body, 1<<20)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

// plan shows the commands a request would run, without running them.
func (s *Server) plan(w http.ResponseWriter, r *http.Request) {
	var req jobs.Request
	if err := decode(r, &req); err != nil {
		httpError(w, 400, "bad request: "+err.Error())
		return
	}
	stages, seed, err := s.Jobs.Build(r.Context(), &req, "/job")
	if err != nil {
		httpError(w, 400, err.Error())
		return
	}
	cmds := []map[string]string{}
	for _, st := range stages {
		cmds = append(cmds, map[string]string{"stage": st.Name, "label": st.Label, "command": jobs.DisplayArgv(st.Argv)})
	}
	writeJSON(w, 200, map[string]any{"seed": seed, "commands": cmds})
}

func (s *Server) createJob(w http.ResponseWriter, r *http.Request) {
	var req jobs.Request
	if err := decode(r, &req); err != nil {
		httpError(w, 400, "bad request: "+err.Error())
		return
	}
	j, err := s.Jobs.Create(r.Context(), req)
	if err != nil {
		code := 400
		if errors.Is(err, context.Canceled) {
			code = 499
		}
		httpError(w, code, err.Error())
		return
	}
	writeJSON(w, 201, j.Public())
}

func (s *Server) jobID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("id")
	if !jobs.ValidID(id) {
		httpError(w, 404, "no such job")
		return "", false
	}
	return id, true
}

func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	j, ok := s.Jobs.Get(id)
	if !ok {
		httpError(w, 404, "no such job")
		return
	}
	writeJSON(w, 200, j)
}

func (s *Server) deleteJob(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	switch err := s.Jobs.Delete(id); {
	case errors.Is(err, jobs.ErrNotFound):
		httpError(w, 404, err.Error())
	case errors.Is(err, jobs.ErrBusy):
		httpError(w, 409, err.Error())
	case err != nil:
		httpError(w, 500, err.Error())
	default:
		writeJSON(w, 200, map[string]any{"deleted": id})
	}
}

func (s *Server) cancelJob(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	if err := s.Jobs.Cancel(id); err != nil {
		httpError(w, 404, err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"canceling": id})
}

// events streams job snapshots and log lines as server-sent events.
func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	ch, stop, ok := s.Jobs.Subscribe(id)
	if !ok {
		httpError(w, 404, "no such job")
		return
	}
	defer stop()
	fl, ok := w.(http.Flusher)
	if !ok {
		httpError(w, 500, "streaming unsupported")
		return
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Accel-Buffering", "no")
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ping.C:
			fmt.Fprint(w, ": ping\n\n")
			fl.Flush()
		case msg := <-ch:
			b, _ := json.Marshal(msg)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", msg.Type, b)
			fl.Flush()
		}
	}
}

func (s *Server) file(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	n, err := strconv.Atoi(r.PathValue("n"))
	if err != nil {
		httpError(w, 404, "no such file")
		return
	}
	f, ok := s.Jobs.File(id, n)
	if !ok {
		httpError(w, 404, "no such file")
		return
	}
	fh, err := os.Open(f.Path)
	if err != nil {
		httpError(w, 410, "the file is gone")
		return
	}
	defer fh.Close()
	st, err := fh.Stat()
	if err != nil {
		httpError(w, 500, err.Error())
		return
	}
	if ct := mime.TypeByExtension(filepath.Ext(f.Name)); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	disp := "attachment"
	if r.URL.Query().Get("inline") == "1" {
		disp = "inline"
	}
	w.Header().Set("Content-Disposition", mime.FormatMediaType(disp, map[string]string{"filename": f.Name}))
	http.ServeContent(w, r, f.Name, st.ModTime(), fh) // handles Range for seeking in players
}

func (s *Server) zip(w http.ResponseWriter, r *http.Request) {
	id, ok := s.jobID(w, r)
	if !ok {
		return
	}
	files, title, ok := s.Jobs.Files(id)
	if !ok || len(files) == 0 {
		httpError(w, 404, "no files")
		return
	}
	name := strings.Trim(strings.Map(func(r rune) rune {
		if r == ' ' || r == '-' || r == '_' || (r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			return r
		}
		return -1
	}, title), " ")
	if name == "" {
		name = "ambient-gen"
	}
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": name + ".zip"}))
	zw := zip.NewWriter(w)
	defer zw.Close()
	for _, f := range files {
		fh, err := os.Open(f.Path)
		if err != nil {
			continue
		}
		// audio and video are already compressed: store, don't deflate
		zf, err := zw.CreateHeader(&zip.FileHeader{Name: f.Stage + "/" + f.Name, Method: zip.Store, Modified: time.Now()})
		if err == nil {
			_, err = io.Copy(zf, fh)
		}
		fh.Close()
		if err != nil {
			log.Printf("zip %s: %v", id, err)
			return
		}
	}
}
