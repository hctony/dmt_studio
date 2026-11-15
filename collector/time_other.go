//go:build !windows

package main

import (
	"os"
	"time"
)

func newestTime(info os.FileInfo) time.Time { return info.ModTime() }
