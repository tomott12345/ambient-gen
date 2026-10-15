package httpapi

import (
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
)

func TestSecurity(t *testing.T) {
	h := (&Server{UI: fstest.MapFS{"index.html": {Data: []byte("ok")}}}).Handler()
	do := func(method, path string, hdr map[string]string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader("{}"))
		for k, v := range hdr {
			r.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	if w := do("POST", "/api/jobs", nil); w.Code != 403 {
		t.Errorf("POST without %s: %d, want 403", csrfHeader, w.Code)
	}
	if w := do("POST", "/api/jobs", map[string]string{csrfHeader: csrfValue, "Origin": "https://evil.example"}); w.Code != 403 {
		t.Errorf("cross-origin POST: %d, want 403", w.Code)
	}
	if w := do("DELETE", "/api/jobs/../../etc", map[string]string{csrfHeader: csrfValue}); w.Code == 200 {
		t.Errorf("bad job id accepted")
	}
	if w := do("GET", "/api/jobs/zzz/files/0", nil); w.Code != 404 {
		t.Errorf("bad job id: %d, want 404", w.Code)
	}
	w := do("GET", "/api/health", nil)
	if w.Code != 200 || !strings.Contains(w.Header().Get("Content-Security-Policy"), "default-src 'self'") {
		t.Errorf("health: %d, CSP %q", w.Code, w.Header().Get("Content-Security-Policy"))
	}
	if w.Header().Get("X-Frame-Options") != "DENY" {
		t.Error("missing X-Frame-Options")
	}
}
