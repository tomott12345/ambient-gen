// Package ui embeds the browser front end (plain HTML/CSS/ES module, no
// build step). Rebuild the binary after editing static/.
package ui

import (
	"embed"
	"io/fs"
)

//go:embed static
var static embed.FS

// FS is the static/ folder, served at /.
func FS() fs.FS {
	sub, err := fs.Sub(static, "static")
	if err != nil {
		panic(err)
	}
	return sub
}
