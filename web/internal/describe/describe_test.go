package describe

import "testing"

func f(v float64) *float64 { return &v }

func TestSetArg(t *testing.T) {
	c := &Compose{Params: []Param{
		{Path: "minutes", Type: "float", Min: f(0.1), Max: f(720)},
		{Path: "harmony.chord_notes", Type: "int", Min: f(2), Max: f(5)},
		{Path: "pad.enabled", Type: "bool"},
		{Path: "harmony.key", Type: "str", Choices: []any{"C", "D#"}},
		{Path: "sample_rate", Type: "int", Choices: []any{44100.0, 48000.0}},
	}}
	c.byPath = map[string]*Param{}
	for i := range c.Params {
		c.byPath[c.Params[i].Path] = &c.Params[i]
	}
	ok := []struct {
		path string
		v    any
		want string
	}{
		{"minutes", 0.5, "0.5"}, {"minutes", 30.0, "30"}, {"harmony.chord_notes", 4.0, "4"},
		{"pad.enabled", false, "false"}, {"harmony.key", "D#", "D#"}, {"sample_rate", 48000.0, "48000"},
	}
	for _, tc := range ok {
		got, err := c.SetArg(tc.path, tc.v)
		if err != nil || got != tc.want {
			t.Errorf("SetArg(%s, %v) = %q, %v; want %q", tc.path, tc.v, got, err, tc.want)
		}
	}
	bad := []struct {
		path string
		v    any
	}{
		{"nope", 1.0}, {"minutes", 0.0}, {"minutes", "5"}, {"harmony.chord_notes", 3.5}, {"pad.enabled", "yes"},
		{"harmony.key", "H"}, {"sample_rate", 22050.0}, {"harmony.key", "C\n--evil"},
	}
	for _, tc := range bad {
		if got, err := c.SetArg(tc.path, tc.v); err == nil {
			t.Errorf("SetArg(%s, %v) = %q; want an error", tc.path, tc.v, got)
		}
	}
}
